"""Serve a readable, auto-refreshing view of a semantic-query JSONL file."""

# ruff: noqa: E501 -- embedded HTML/CSS/JS is clearer without Python wrapping.

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

__all__ = [
    "QueryFileViewer",
    "group_query_records",
    "query_viewer_page",
    "read_query_records",
]

# Edit this path when reviewing another generated query file.
INPUT = (
    Path(__file__).resolve().parents[1]
    / "outputs"
    / "open_swe_traces"
    / "codesense-semantic-query.jsonl"
)
PORT = 8766
REFRESH_SECONDS = 2.0


def read_query_records(path: Path) -> list[dict[str, object]]:
    """Read the current JSONL contents so each browser poll sees new cases."""
    records: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}") from exc
            if not isinstance(value, Mapping):
                raise ValueError(f"line {line_number} must contain a JSON object")
            records.append(dict(value))
    return records


def group_query_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    """Group flat query rows by trace while preserving trace and query order."""
    groups: dict[tuple[str, str, str], dict[str, object]] = {}
    for index, record in enumerate(records):
        repo = str(record.get("repo") or "")
        instance_id = str(record.get("instance_id") or "")
        trajectory_id = str(record.get("trajectory_id") or "")
        query_id = str(record.get("query_id") or "")
        trace_identity = trajectory_id or f"query:{query_id or index}"
        key = (repo, instance_id, trace_identity)
        group = groups.get(key)
        if group is None:
            group = {
                "repo": repo,
                "instance_id": instance_id,
                "trajectory_id": trajectory_id,
                "issue_statement": record.get("issue_statement") or "",
                "trace_answer": record.get("trace_answer") or [],
                "queries": [],
            }
            groups[key] = group
        elif not group["trace_answer"] and record.get("trace_answer"):
            group["trace_answer"] = record["trace_answer"]
        queries = group["queries"]
        if isinstance(queries, list):
            queries.append(record)
    return list(groups.values())


def query_viewer_page(*, refresh_seconds: float = REFRESH_SECONDS) -> str:
    """Return the data-independent page that polls the current query JSONL."""
    refresh_ms = max(250, int(refresh_seconds * 1000))
    return rf"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CodeSense Query Viewer</title>
  <style>
    :root {{ color-scheme:light; --ink:#172033; --muted:#667085; --line:#dce3ed;
      --paper:#fff; --wash:#f5f7fb; --blue:#1d4ed8; --amber:#b45309; --amber-bg:#fff7df;
      --red:#b42318; --red-bg:#fef3f2; --purple:#6941c6; --purple-bg:#f4f3ff; }}
    * {{ box-sizing:border-box; }} html {{ scroll-behavior:smooth; }}
    body {{ margin:0; color:var(--ink); background:var(--wash);
      font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    header {{ position:sticky; top:0; z-index:3; display:flex; justify-content:space-between;
      gap:18px; padding:18px 28px; color:#fff; background:#172033; box-shadow:0 3px 12px #10182833; }}
    header h1 {{ margin:0; font-size:21px; }} header p {{ margin:3px 0 0; color:#cbd5e1; }}
    #status {{ align-self:center; padding:5px 10px; border-radius:999px; background:#344054; }}
    .layout {{ display:grid; grid-template-columns:280px minmax(0,1fr); gap:22px;
      max-width:1550px; margin:auto; padding:22px; }}
    aside {{ position:sticky; top:94px; align-self:start; max-height:calc(100vh - 116px);
      overflow:auto; padding:14px; border:1px solid var(--line); border-radius:12px;
      background:var(--paper); }}
    aside h2 {{ margin:0 0 10px; color:var(--muted); font-size:12px; text-transform:uppercase; }}
    aside a {{ display:block; padding:7px 9px 7px 22px; color:var(--ink); text-decoration:none;
      border-radius:8px; }} aside a:hover {{ background:#eef3ff; }} aside span {{ display:block; color:var(--muted);
      font-size:12px; overflow-wrap:anywhere; }}
    .trace-nav {{ margin-bottom:8px; }} .trace-nav summary {{ cursor:pointer; padding:9px;
      border-radius:8px; font-weight:700; }} .trace-nav summary:hover {{ background:#eef3ff; }}
    main {{ min-width:0; }}
    .error {{ margin-bottom:18px; padding:13px 16px; color:var(--red); background:var(--red-bg);
      border:1px solid #fecdca; border-radius:9px; white-space:pre-wrap; }}
    .trace-group {{ margin-bottom:28px; padding:24px; background:var(--paper); border:1px solid var(--line);
      border-radius:14px; box-shadow:0 8px 26px #1720330d; scroll-margin-top:96px; }}
    .trace-head {{ display:flex; justify-content:space-between; gap:16px; padding-bottom:17px;
      border-bottom:1px solid var(--line); }} .trace-head h2 {{ margin:5px 0 3px; font-size:21px; }}
    .query-list {{ display:grid; gap:18px; margin-top:22px; }}
    .case {{ padding:20px; background:#fbfcfe; border:1px solid var(--line);
      border-radius:12px; scroll-margin-top:96px; }}
    .case-head {{ display:flex; justify-content:space-between; gap:16px; padding-bottom:17px;
      border-bottom:1px solid var(--line); }}
    .case h2 {{ margin:5px 0 3px; font-size:21px; }} h3 {{ margin:0 0 9px; font-size:15px; }}
    .meta {{ color:var(--muted); overflow-wrap:anywhere; }}
    .pill {{ display:inline-block; padding:3px 8px; border-radius:999px; color:var(--blue);
      background:#eef3ff; font-size:12px; font-weight:650; white-space:nowrap; }}
    .section {{ margin-top:20px; }}
    .issue,.query,.reason {{ margin:0; padding:13px 15px; border-radius:9px; white-space:pre-wrap;
      overflow-wrap:anywhere; }}
    .issue {{ background:#f8fafc; }} .query {{ color:#163b77; background:#eff6ff;
      font-size:16px; font-weight:650; }} .reason {{ background:#f8fafc; }}
    .locations {{ margin:0; padding-left:22px; }} .locations li {{ margin:6px 0; overflow-wrap:anywhere; }}
    code {{ padding:2px 5px; border-radius:4px; background:#eef1f5;
      font:12px ui-monospace,SFMono-Regular,Menlo,monospace; }}
    .trace {{ display:grid; gap:8px; }} .event {{ border:1px solid var(--line); border-radius:9px; }}
    .event.prompt {{ border-color:#e5a832; background:var(--amber-bg); }}
    .event.result {{ border-color:#bdb4fe; background:var(--purple-bg); }}
    .event summary {{ cursor:pointer; padding:10px 12px; font-weight:650; }}
    .event-body {{ padding:0 12px 12px; }} .field {{ margin-top:9px; }}
    .label {{ display:block; margin-bottom:3px; color:var(--muted); font-size:11px;
      font-weight:700; text-transform:uppercase; }}
    pre {{ margin:0; padding:10px; border-radius:7px; color:#e2e8f0; background:#172033;
      white-space:pre-wrap; overflow-wrap:anywhere; font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }}
    .empty {{ color:var(--muted); font-style:italic; }}
    @media(max-width:850px) {{ header {{ position:static; }} .layout {{ grid-template-columns:1fr; }}
      aside {{ position:static; max-height:none; }} .case-head {{ display:block; }} }}
  </style>
</head>
<body>
  <header><div><h1>CodeSense Query Viewer</h1><p id="source">等待读取 query 文件</p></div>
    <div id="status">连接中</div></header>
  <div class="layout"><aside><h2>Traces / Queries</h2><nav id="navigation"></nav></aside>
    <main><div id="error"></div><section id="cases"></section></main></div>
  <script>
    function element(tag, className, text) {{
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = String(text);
      return node;
    }}
    function sequence(value) {{ return Array.isArray(value) ? value : []; }}
    function mapping(value) {{ return value && typeof value === 'object' ? value : {{}}; }}
    function section(title, value, className) {{
      const wrapper = element('section', 'section');
      wrapper.append(element('h3', '', title));
      wrapper.append(element('div', className, value || '(missing)'));
      return wrapper;
    }}
    function locations(values) {{
      const list = element('ul', 'locations');
      const items = sequence(values);
      if (!items.length) return element('div', 'empty', '没有结构化代码位置');
      items.forEach(value => {{
        const location = mapping(value);
        const suffix = sequence(location.functions).length
          ? ' :: ' + sequence(location.functions).map(String).join(', ') : '';
        list.append(element('li', '', String(location.file || 'unknown file') + suffix));
      }});
      return list;
    }}
    function evidence(values) {{
      const list = element('ul', 'locations');
      const items = sequence(values);
      if (!items.length) return element('div', 'empty', '没有使用证据');
      items.forEach(value => {{
        const item = mapping(value);
        const functions = sequence(item.functions).length
          ? ' :: ' + sequence(item.functions).map(String).join(', ') : '';
        list.append(element('li', '', String(item.kind || 'used') + ' at event ' +
          String(item.event_index ?? '?') + ' · ' + String(item.file || 'unknown file') + functions));
      }});
      return list;
    }}
    function eventCard(rawEvent, promptEvents, resultEvents) {{
      const event = mapping(rawEvent);
      const index = Number(event.index);
      const isPrompt = promptEvents.has(index);
      const isResult = resultEvents.has(index);
      const state = isPrompt ? ' prompt' : isResult ? ' result' : '';
      const details = element('details', 'event' + state);
      if (isPrompt || isResult) details.open = true;
      const tool = event.tool_name ? ' · ' + String(event.tool_name) : '';
      const marker = isPrompt ? ' · prompt 可见' : isResult ? ' · 搜索结果（prompt 不可见）' : '';
      details.append(element('summary', '', 'Event ' + index + ' · ' + String(event.role || 'unknown') + tool + marker));
      const body = element('div', 'event-body');
      [['Text','text'],['Tool input','tool_input'],['Tool output','tool_output']].forEach(pair => {{
        if (!event[pair[1]]) return;
        const field = element('div', 'field');
        field.append(element('span', 'label', pair[0]));
        field.append(element('pre', '', event[pair[1]]));
        body.append(field);
      }});
      if (!body.childNodes.length) body.append(element('div', 'empty', '没有文本内容'));
      details.append(body);
      return details;
    }}
    function queryCard(record, traceNumber, queryNumber) {{
      const id = 'trace-' + traceNumber + '-query-' + queryNumber;
      const card = element('article', 'case');
      card.id = id;
      const head = element('div', 'case-head');
      const identity = element('div');
      identity.append(element('span', 'pill', 'Query ' + queryNumber));
      identity.append(element('h2', '', record.query_id || 'unnamed query'));
      identity.append(element('div', 'meta', 'source events: ' +
        (sequence(record.source_event_indices).join(', ') || '(none)') +
        ' · result events: ' + (sequence(record.result_event_indices).join(', ') || '(none)')));
      head.append(identity);
      head.append(element('span', 'pill', sequence(record.answer).length + ' gold'));
      card.append(head);
      card.append(section('Semantic query', record.query, 'query'));
      card.append(section('Why semantic', mapping(record.provenance).query_reason, 'reason'));
      const answer = element('section', 'section');
      answer.append(element('h3', '', 'Gold answers'));
      answer.append(locations(record.answer));
      card.append(answer);
      const candidates = element('section', 'section');
      candidates.append(element('h3', '', 'Candidate answers'));
      candidates.append(locations(record.candidate_answers));
      card.append(candidates);
      const usage = element('section', 'section');
      usage.append(element('h3', '', 'Usage evidence'));
      usage.append(evidence(record.usage_evidence));
      card.append(usage);
      const trace = element('section', 'section');
      trace.append(element('h3', '', 'Query-related trace events'));
      const events = element('div', 'trace');
      const promptEvents = new Set(sequence(record.source_event_indices).map(Number));
      const resultEvents = new Set(sequence(record.result_event_indices).map(Number));
      const related = new Set([...promptEvents, ...resultEvents]);
      sequence(record.source_events)
        .filter(event => related.has(Number(mapping(event).index)))
        .forEach(event => events.append(eventCard(event, promptEvents, resultEvents)));
      if (!events.childNodes.length)
        events.append(element('div', 'empty', '没有与该 query 关联的 trace 事件'));
      trace.append(events);
      card.append(trace);
      return [id, card];
    }}
    function traceCard(rawTrace, traceNumber) {{
      const trace = mapping(rawTrace);
      const queries = sequence(trace.queries);
      const id = 'trace-' + traceNumber;
      const card = element('article', 'trace-group');
      card.id = id;
      const head = element('div', 'trace-head');
      const identity = element('div');
      identity.append(element('span', 'pill', 'Trace ' + traceNumber));
      identity.append(element('h2', '', trace.repo || 'unknown repository'));
      identity.append(element('div', 'meta', 'instance: ' + String(trace.instance_id || '') +
        ' · trajectory: ' + String(trace.trajectory_id || '')));
      head.append(identity);
      head.append(element('span', 'pill', queries.length + ' queries'));
      card.append(head);
      card.append(section('Issue statement', trace.issue_statement, 'issue'));
      const traceAnswer = element('section', 'section');
      traceAnswer.append(element('h3', '', 'Trace answer'));
      traceAnswer.append(locations(trace.trace_answer));
      card.append(traceAnswer);

      const queryList = element('section', 'query-list');
      const queryLinks = [];
      queries.forEach((rawQuery, index) => {{
        const query = mapping(rawQuery);
        const pair = queryCard(query, traceNumber, index + 1);
        queryList.append(pair[1]);
        queryLinks.push({{id: pair[0], query: query, number: index + 1}});
      }});
      card.append(queryList);

      const original = element('details', 'section event');
      original.append(element('summary', '', 'Original trace · ' +
        sequence(mapping(queries[0]).source_events).length + ' events'));
      const originalEvents = element('div', 'event-body trace');
      sequence(mapping(queries[0]).source_events).forEach(event =>
        originalEvents.append(eventCard(event, new Set(), new Set())));
      if (!originalEvents.childNodes.length)
        originalEvents.append(element('div', 'empty', '没有 trace 事件'));
      original.append(originalEvents);
      card.append(original);
      return {{id: id, card: card, queryLinks: queryLinks}};
    }}
    function traceNavigation(trace, traceNumber, queryLinks) {{
      const details = element('details', 'trace-nav');
      details.open = true;
      const summary = element('summary', '', trace.repo || 'unknown repository');
      summary.append(element('span', '', (trace.instance_id || trace.trajectory_id || '') +
        ' · ' + queryLinks.length + ' queries'));
      details.append(summary);
      queryLinks.forEach(item => {{
        const link = element('a', '', 'Query ' + item.number);
        link.href = '#' + item.id;
        link.append(element('span', '', item.query.query_id || ''));
        details.append(link);
      }});
      return details;
    }}
    function render(snapshot) {{
      const navigation = document.getElementById('navigation');
      const cases = document.getElementById('cases');
      const error = document.getElementById('error');
      navigation.replaceChildren(); cases.replaceChildren(); error.replaceChildren();
      document.getElementById('source').textContent = String(snapshot.source || '');
      document.getElementById('status').textContent = snapshot.error
        ? '读取失败' : String(snapshot.trace_count || 0) + ' traces · ' +
          String(snapshot.query_count || 0) + ' queries';
      if (snapshot.error) error.append(element('div', 'error', snapshot.error));
      sequence(snapshot.traces).forEach((rawTrace, index) => {{
        const trace = mapping(rawTrace);
        const rendered = traceCard(trace, index + 1);
        navigation.append(traceNavigation(trace, index + 1, rendered.queryLinks));
        cases.append(rendered.card);
      }});
      if (!sequence(snapshot.traces).length && !snapshot.error)
        cases.append(element('div', 'empty', 'query 文件中还没有 trace'));
    }}
    async function loadCases() {{
      try {{
        const response = await fetch('/api/cases', {{cache:'no-store'}});
        render(await response.json());
      }} catch (error) {{
        document.getElementById('status').textContent = '连接失败';
        document.getElementById('error').replaceChildren(element('div', 'error', String(error)));
      }}
    }}
    loadCases();
    setInterval(loadCases, {refresh_ms});
  </script>
</body>
</html>"""


class _ViewerServer(ThreadingHTTPServer):
    daemon_threads = True


def _handler_for(path: Path, page: bytes) -> type[BaseHTTPRequestHandler]:
    class ViewerHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "CodeSenseQueryViewer/1"

        def do_GET(self) -> None:  # noqa: N802 -- stdlib handler API
            request_path = self.path.partition("?")[0]
            if request_path == "/":
                self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", page)
            elif request_path == "/api/cases":
                self._send_json(_query_snapshot(path))
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

        def log_message(self, _format: str, *_args: object) -> None:
            """Keep the terminal limited to the viewer URL and fatal errors."""

    return ViewerHandler


def _query_snapshot(path: Path) -> dict[str, object]:
    try:
        records = read_query_records(path)
    except (OSError, ValueError) as exc:
        return {
            "source": str(path),
            "trace_count": 0,
            "query_count": 0,
            "traces": [],
            "error": str(exc),
        }
    traces = group_query_records(records)
    return {
        "source": str(path),
        "trace_count": len(traces),
        "query_count": len(records),
        "traces": traces,
        "error": None,
    }


class QueryFileViewer:
    """Own a loopback HTTP server that rereads one query JSONL on demand."""

    HOST: ClassVar[str] = "127.0.0.1"

    def __init__(self, server: _ViewerServer, thread: threading.Thread) -> None:
        self._server = server
        self._thread = thread
        self._closed = False
        self._close_lock = threading.Lock()

    @classmethod
    def start(
        cls,
        path: Path,
        *,
        port: int = PORT,
        refresh_seconds: float = REFRESH_SECONDS,
    ) -> QueryFileViewer:
        page = query_viewer_page(refresh_seconds=refresh_seconds).encode("utf-8")
        server = _ViewerServer((cls.HOST, port), _handler_for(path.expanduser().resolve(), page))
        thread = threading.Thread(
            target=server.serve_forever, name="codesense-query-viewer", daemon=True
        )
        try:
            thread.start()
        except Exception:
            server.server_close()
            raise
        return cls(server, thread)

    @property
    def url(self) -> str:
        return f"http://{self.HOST}:{self._server.server_address[1]}"

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def main() -> int:
    """Start the local viewer and keep it available until Ctrl+C."""
    viewer = QueryFileViewer.start(INPUT, port=PORT, refresh_seconds=REFRESH_SECONDS)
    print(f"query viewer: {viewer.url}")
    print(f"source: {INPUT}")
    print("press Ctrl+C to exit")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        viewer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
