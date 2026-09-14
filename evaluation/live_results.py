"""A dependency-free local viewer for incremental evaluation results."""

# ruff: noqa: E501 -- embedded HTML/CSS/JS is clearer without Python wrapping.

from __future__ import annotations

import copy
import json
import queue
import threading
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

__all__ = ["LiveEvaluationViewer", "LiveResultStore", "viewer_page"]

Event = tuple[str, dict[str, object]]
Subscriber = queue.Queue[Event]


class LiveResultStore:
    """Thread-safe run state and fan-out queues for browser subscribers."""

    def __init__(self, meta: Mapping[str, object] | None = None) -> None:
        self._meta = copy.deepcopy(dict(meta or {}))
        self._records: list[dict[str, object]] = []
        self._summary: dict[str, object] | None = None
        self._subscribers: set[Subscriber] = set()
        self._lock = threading.Lock()
        self._closed = False

    def publish(self, record: Mapping[str, object]) -> None:
        """Append one completed query and notify current subscribers."""
        payload = copy.deepcopy(dict(record))
        with self._lock:
            if self._closed:
                return
            self._records.append(payload)
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            subscriber.put(("query", copy.deepcopy(payload)))

    def finish(self, summary: Mapping[str, object]) -> None:
        """Store and publish the final route summary without closing the page."""
        payload = copy.deepcopy(dict(summary))
        with self._lock:
            if self._closed:
                return
            self._summary = payload
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            subscriber.put(("summary", copy.deepcopy(payload)))

    def snapshot(self) -> dict[str, object]:
        """Return an isolated JSON-compatible view of the current run."""
        with self._lock:
            return self._snapshot_locked()

    def subscribe(self) -> tuple[Subscriber, dict[str, object]]:
        """Register first, then capture state under the same lock.

        The caller sends this snapshot before consuming its queue, so a query
        cannot fall into the gap between an HTTP snapshot and SSE subscription.
        """
        subscriber: Subscriber = queue.Queue()
        with self._lock:
            self._subscribers.add(subscriber)
            snapshot = self._snapshot_locked()
            if self._closed:
                subscriber.put(("close", {}))
        return subscriber, snapshot

    def unsubscribe(self, subscriber: Subscriber) -> None:
        """Remove a disconnected browser without affecting publishers."""
        with self._lock:
            self._subscribers.discard(subscriber)

    def close(self) -> None:
        """Wake all SSE handlers so the owning HTTP server can stop."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscribers = tuple(self._subscribers)
            self._subscribers.clear()
        for subscriber in subscribers:
            subscriber.put(("close", {}))

    def _snapshot_locked(self) -> dict[str, object]:
        return copy.deepcopy(
            {
                "meta": self._meta,
                "records": self._records,
                "summary": self._summary,
            }
        )


def viewer_page() -> str:
    """Return the data-independent single-page live evaluation UI."""
    return r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CodeSense Live Evaluation</title>
  <style>
    :root { color-scheme:light; --ink:#182230; --muted:#667085; --line:#dbe2ea;
      --paper:#fff; --wash:#f5f7fa; --blue:#175cd3; --green:#067647; --green-bg:#ecfdf3;
      --red:#b42318; --red-bg:#fef3f2; --gray:#475467; --gray-bg:#f2f4f7; }
    * { box-sizing:border-box; }
    body { margin:0; color:var(--ink); background:var(--wash);
      font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    header { position:sticky; top:0; z-index:2; display:flex; justify-content:space-between;
      gap:20px; padding:18px 28px; color:#fff; background:#101828; box-shadow:0 3px 12px #10182833; }
    h1 { margin:0; font-size:21px; } header p { margin:3px 0 0; color:#d0d5dd; }
    #connection { align-self:center; padding:5px 10px; border-radius:999px; background:#344054; }
    main { max-width:1500px; margin:auto; padding:22px; }
    #summary { display:none; margin-bottom:18px; padding:16px; border:1px solid #b2ccff;
      border-radius:12px; background:#eff4ff; }
    .card { margin-bottom:20px; border:1px solid var(--line); border-radius:14px;
      background:var(--paper); box-shadow:0 6px 20px #1018280d; overflow:hidden; }
    .card-head { padding:17px 20px; border-bottom:1px solid var(--line); background:#fcfcfd; }
    .eyebrow { color:var(--muted); font-size:12px; overflow-wrap:anywhere; }
    .card h2 { margin:5px 0 0; font-size:18px; }
    .answers { padding:14px 20px; border-bottom:1px solid var(--line); }
    .answers h3,.route h3 { margin:0 0 9px; font-size:14px; }
    .routes { display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); }
    .route { min-width:0; padding:17px 20px; border-right:1px solid var(--line); }
    .route:last-child { border-right:0; }
    .route-meta { color:var(--muted); font-size:12px; }
    .metrics { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px; margin:12px 0; }
    .metric { padding:8px 9px; border-radius:8px; background:#f8fafc; }
    .metric b { display:block; font-size:16px; }
    .block-title { margin:14px 0 7px; color:var(--muted); font-size:11px;
      font-weight:700; text-transform:uppercase; }
    .row { margin:5px 0; padding:7px 9px; border-radius:7px; overflow-wrap:anywhere; }
    .matched { color:var(--green); background:var(--green-bg); }
    .missed { color:var(--red); background:var(--red-bg); }
    .extra { color:var(--gray); background:var(--gray-bg); }
    .neutral { color:var(--gray); background:#f8fafc; }
    .error { color:var(--red); white-space:pre-wrap; }
    .empty { color:var(--muted); font-style:italic; }
    code { font:12px ui-monospace,SFMono-Regular,Menlo,monospace; }
    @media(max-width:760px) { header { position:static; display:block; } #connection { display:inline-block;
      margin-top:9px; } main { padding:12px; } .routes { display:block; } .route { border-right:0;
      border-bottom:1px solid var(--line); } }
  </style>
</head>
<body>
  <header><div><h1>CodeSense Live Evaluation</h1><p id="progress">等待评测结果</p></div>
    <div id="connection">连接中</div></header>
  <main><section id="summary"></section><section id="records"></section></main>
  <script>
    const seen = new Set();
    let routeOrder = [];

    function element(tag, className, text) {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = String(text);
      return node;
    }

    function sequence(value) { return Array.isArray(value) ? value : []; }
    function mapping(value) { return value && typeof value === 'object' ? value : {}; }
    function metric(value) { return typeof value === 'number' ? value.toFixed(3) : '—'; }
    function functionName(value) {
      return String(value || '').trim().split('(', 1)[0].split('.').pop();
    }
    function goldPairKey(value) {
      return String(value.file || '') + '\u0000' + functionName(value.function);
    }
    function hitPairKey(value) {
      return String(value.file || '') + '\u0000' + functionName(value.name);
    }

    function appendRows(parent, rows, emptyText) {
      if (!rows.length) parent.append(element('div', 'empty', emptyText));
      rows.forEach(row => parent.append(row));
    }

    function rawAnswers(evaluation) {
      const rows = [];
      sequence(evaluation.answer).forEach(answer => {
        const file = String(answer.file || 'unknown file');
        rows.push(element('div', 'row neutral', file));
        sequence(answer.functions).forEach(name => rows.push(element('div', 'row neutral', file + ' :: ' + name)));
      });
      return rows;
    }

    function goldDiff(metrics) {
      const rows = [];
      const matchedFiles = new Set(sequence(metrics.matched_files).map(String));
      const matchedFunctions = new Set(sequence(metrics.matched_functions).map(goldPairKey));
      sequence(metrics.gold_files).forEach(file => {
        const name = String(file);
        rows.push(element('div', 'row ' + (matchedFiles.has(name) ? 'matched' : 'missed'), name));
      });
      sequence(metrics.gold_functions).forEach(item => {
        const name = String(item.file || '') + ' :: ' + String(item.function || '');
        rows.push(element('div', 'row ' + (matchedFunctions.has(goldPairKey(item)) ? 'matched' : 'missed'), name));
      });
      return rows;
    }

    function hitRows(routeResult, metrics) {
      const matchedFiles = new Set(sequence(metrics.matched_files).map(String));
      const matchedFunctions = new Set(sequence(metrics.matched_functions).map(goldPairKey));
      const hasFunctionGold = sequence(metrics.gold_functions).length > 0;
      return sequence(routeResult.hits).map(hit => {
        const location = String(hit.file || '') + ':' + String(hit.line || 0);
        const label = '#' + String(hit.rank || 0) + ' ' + String(hit.kind || '') + ' ' +
          String(hit.name || '') + ' — ' + location + ' · ' + metric(hit.score);
        const matched = hasFunctionGold ? matchedFunctions.has(hitPairKey(hit)) :
          matchedFiles.has(String(hit.file || ''));
        const status = matched ? 'matched' : 'extra';
        const row = element('div', 'row ' + status, label);
        if (hit.why) row.append(element('div', 'route-meta', hit.why));
        return row;
      });
    }

    function renderMetrics(parent, metrics) {
      const grid = element('div', 'metrics');
      [['File precision','file_precision'],['File recall','file_recall'],
       ['Function precision','function_precision'],['Function recall','function_recall']]
        .forEach(([label, key]) => {
          const item = element('div', 'metric', label);
          item.append(element('b', '', metric(metrics[key])));
          grid.append(item);
        });
      parent.append(grid);
    }

    function renderRoute(evaluation, routeName, rawResult) {
      const routeResult = mapping(rawResult);
      const metrics = mapping(routeResult.metrics);
      const section = element('section', 'route');
      section.append(element('h3', '', routeName));
      const actual = routeResult.actual_route ? 'actual: ' + routeResult.actual_route : 'not completed';
      section.append(element('div', 'route-meta', actual + ' · ' + metric(routeResult.elapsed) + 's'));
      if (routeResult.error) section.append(element('div', 'error', routeResult.error));
      renderMetrics(section, metrics);
      section.append(element('div', 'block-title', 'Answer diff'));
      appendRows(section, Object.keys(metrics).length ? goldDiff(metrics) : rawAnswers(evaluation), '没有标准答案');
      section.append(element('div', 'block-title', 'Search hits'));
      appendRows(section, hitRows(routeResult, metrics), '没有搜索结果');
      return section;
    }

    function renderRecord(record) {
      if (!record || seen.has(record.key)) return;
      seen.add(record.key);
      const evaluation = mapping(record.evaluation);
      const card = element('article', 'card');
      const head = element('div', 'card-head');
      head.append(element('div', 'eyebrow', String(record.repo || '') + ' · ' +
        String(record.instance_id || '') + ' · query ' + String(record.key || '')));
      head.append(element('h2', '', evaluation.query || '(empty query)'));
      card.append(head);
      const answers = element('section', 'answers');
      answers.append(element('h3', '', 'Gold answers'));
      appendRows(answers, rawAnswers(evaluation), '没有标准答案');
      card.append(answers);
      const routes = element('div', 'routes');
      const values = mapping(evaluation.routes);
      const names = routeOrder.length ? routeOrder : Object.keys(values);
      names.forEach(name => routes.append(renderRoute(evaluation, name, values[name])));
      card.append(routes);
      document.getElementById('records').append(card);
      document.getElementById('progress').textContent = '已完成 ' + seen.size + ' 条 query';
    }

    function renderSummary(summary) {
      const section = document.getElementById('summary');
      section.style.display = 'block';
      section.replaceChildren(element('h2', '', '评测完成'));
      Object.entries(mapping(summary)).forEach(([route, values]) => {
        const data = mapping(values);
        section.append(element('div', '', route + ': ' + String(data.completed || 0) + '/' +
          String(data.queries || 0) + ' completed · file P/R ' + metric(data.file_precision) +
          '/' + metric(data.file_recall)));
      });
    }

    function applySnapshot(snapshot) {
      routeOrder = sequence(mapping(snapshot.meta).routes).map(String);
      sequence(snapshot.records).forEach(renderRecord);
      if (snapshot.summary) renderSummary(snapshot.summary);
    }

    const connection = document.getElementById('connection');
    const stream = new EventSource('/events');
    stream.addEventListener('open', () => { connection.textContent = '实时连接'; });
    stream.addEventListener('snapshot', event => applySnapshot(JSON.parse(event.data)));
    stream.addEventListener('query', event => renderRecord(JSON.parse(event.data)));
    stream.addEventListener('summary', event => renderSummary(JSON.parse(event.data)));
    stream.addEventListener('close', () => { connection.textContent = '评测进程已结束'; stream.close(); });
    stream.addEventListener('error', () => { connection.textContent = '等待连接'; });
  </script>
</body>
</html>"""


class _ViewerServer(ThreadingHTTPServer):
    """A loopback server whose client threads never hold process exit open."""

    daemon_threads = True


def _handler_for(store: LiveResultStore) -> type[BaseHTTPRequestHandler]:
    page = viewer_page().encode("utf-8")

    class ViewerHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "CodeSenseLiveViewer/1"

        def do_GET(self) -> None:  # noqa: N802 -- stdlib handler API
            path = self.path.partition("?")[0]
            if path == "/":
                self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", page)
            elif path == "/api/snapshot":
                self._send_json(store.snapshot())
            elif path == "/events":
                self._stream_events()
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def _send_json(self, value: Mapping[str, object]) -> None:
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            self._send_bytes(HTTPStatus.OK, "application/json; charset=utf-8", payload)

        def _send_bytes(self, status: HTTPStatus, content_type: str, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def _stream_events(self) -> None:
            subscriber, snapshot = store.subscribe()
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self._write_event("snapshot", snapshot)
                while True:
                    try:
                        kind, payload = subscriber.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    self._write_event(kind, payload)
                    if kind == "close":
                        return
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            finally:
                # SSE owns this HTTP/1.1 connection until disconnect/close.
                # Prevent BaseHTTPRequestHandler from parsing another request
                # from a socket the browser has already closed.
                self.close_connection = True
                store.unsubscribe(subscriber)

        def _write_event(self, kind: str, payload: Mapping[str, object]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            self.wfile.write(f"event: {kind}\ndata: {encoded}\n\n".encode())
            self.wfile.flush()

        def log_message(self, _format: str, *_args: object) -> None:
            """Keep benchmark output focused on progress and the viewer URL."""

    return ViewerHandler


class LiveEvaluationViewer:
    """Own a loopback HTTP/SSE server and its in-memory result store."""

    HOST: ClassVar[str] = "127.0.0.1"

    def __init__(
        self,
        store: LiveResultStore,
        server: _ViewerServer,
        thread: threading.Thread,
    ) -> None:
        self._store = store
        self._server = server
        self._thread = thread
        self._close_lock = threading.Lock()
        self._closed = False

    @classmethod
    def start(
        cls,
        *,
        port: int = 8765,
        meta: Mapping[str, object] | None = None,
    ) -> LiveEvaluationViewer:
        """Bind loopback, start the daemon accept thread, and return its owner."""
        store = LiveResultStore(meta)
        server = _ViewerServer((cls.HOST, port), _handler_for(store))
        thread = threading.Thread(
            target=server.serve_forever,
            name="codesense-live-evaluation-viewer",
            daemon=True,
        )
        try:
            thread.start()
        except Exception:
            server.server_close()
            raise
        return cls(store, server, thread)

    @property
    def url(self) -> str:
        """Loopback URL to print for manual opening."""
        return f"http://{self.HOST}:{self._server.server_address[1]}"

    def publish(self, record: Mapping[str, object]) -> None:
        """Publish one query result without waiting for a browser."""
        self._store.publish(record)

    def finish(self, summary: Mapping[str, object]) -> None:
        """Publish the final aggregate while leaving connected pages intact."""
        self._store.finish(summary)

    def close(self) -> None:
        """Stop owned threads and sockets; safe to call more than once."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._store.close()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
