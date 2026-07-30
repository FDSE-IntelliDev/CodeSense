import os
import json
import subprocess
import threading
import hashlib
import time
import uuid
from pathlib import Path
from typing import List, Optional

from codesense.config import load_config
from codesense.parsers.tools import get_function_position


class ParallelJavaLSPClient:
    """
    Lightweight LSP client for multiprocessing workers. Each instance
    creates an isolated jdtls workspace/config directory based on a
    worker-specific hash so multiple workers can run concurrently.
    """

    def __init__(
        self,
        project_root: str,
        jdtls_path: Optional[str] = None,
        request_timeout: float = 10.0,
        verbose: bool = False,
        worker_id: Optional[str] = None,
    ):
        self.project_root = project_root
        self.jdtls_path = jdtls_path
        self.request_timeout = request_timeout
        self.verbose = verbose
        self.worker_id = worker_id or uuid.uuid4().hex[:8]

        self._process = None
        self._req_id = 1
        self._responses = {}
        self._opened_documents = set()
        self.data_dir = ""
        self.configuration_dir = ""

    def start(self):
        # Create unique dirs per worker to avoid workspace overlap
        hash_input = f"{self.project_root}_worker_{self.worker_id}"
        proj_hash = hashlib.md5(hash_input.encode("utf-8")).hexdigest()[:8]

        repo_root = Path(__file__).resolve().parent.parent
        self.data_dir = os.path.abspath(str(repo_root / "output" / f"jdtls_workspace_{proj_hash}"))
        self.configuration_dir = os.path.abspath(str(repo_root / "output" / f"jdtls_config_{proj_hash}"))
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.configuration_dir, exist_ok=True)

        # 同 java_lsp_client：缺省值在起进程时才解析，构造对象不碰配置。
        jdtls = self.jdtls_path or load_config().tools.jdtls_path
        cmd = [jdtls, "-configuration", self.configuration_dir, "-data", self.data_dir]
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.project_root,
            text=False,
        )

        threading.Thread(target=self._read_loop, daemon=True).start()
        # initialize will send the initialize request and an initialized notification
        self._initialize()

    def _read_loop(self):
        while True:
            process = self._process
            if not process or process.poll() is not None:
                return
            header = process.stdout.readline().decode("utf-8")
            if not header.startswith("Content-Length:"):
                continue
            try:
                length = int(header.split(":")[1].strip())
            except Exception:
                continue
            # skip empty line
            process.stdout.readline()
            body = process.stdout.read(length).decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                continue

            if self.verbose and "method" in data:
                print(f"[Worker {self.worker_id}] [JDTLS Raw] {json.dumps(data, ensure_ascii=False)}")

            if "id" in data:
                self._responses[data["id"]] = data

    def _send_request(self, method: str, params: dict) -> dict:
        req_id = self._req_id
        self._req_id += 1
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        body = json.dumps(msg)
        content = f"Content-Length: {len(body)}\r\n\r\n{body}"
        self._process.stdin.write(content.encode("utf-8"))
        self._process.stdin.flush()

        deadline = time.time() + self.request_timeout
        while time.time() < deadline:
            if req_id in self._responses:
                return self._responses.pop(req_id)
            time.sleep(0.05)
        return {}

    def _send_notification(self, method: str, params: dict):
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        body = json.dumps(msg)
        content = f"Content-Length: {len(body)}\r\n\r\n{body}"
        self._process.stdin.write(content.encode("utf-8"))
        self._process.stdin.flush()

    def _initialize(self):
        # send initialize request
        self._send_request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": f"file://{self.project_root}",
                "capabilities": {"textDocument": {"callHierarchy": {"dynamicRegistration": True}}},
            },
        )
        # notify initialized
        self._send_notification("initialized", {})

    def open_document(self, filepath: str, language_id: str = "java"):
        file_path = os.path.abspath(filepath)
        if file_path in self._opened_documents:
            return
        try:
            text = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        self._send_notification(
            "textDocument/didOpen",
            {"textDocument": {"uri": f"file://{file_path}", "languageId": language_id, "version": 1, "text": text}},
        )
        self._opened_documents.add(file_path)

    def stop(self):
        process = self._process
        if process:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
            finally:
                self._process = None


class ParallelJavaCallChainExtractor:
    def __init__(self, lsp_client: ParallelJavaLSPClient):
        self.lsp_client = lsp_client

    def get_call_chain(self, filepath: str, func_name: str, layer: Optional[int] = None) -> dict:
        pos = get_function_position(filepath, func_name)
        if not pos:
            return {"error": f"Could not find function {func_name} in {filepath}"}

        line, character = pos
        file_uri = f"file://{os.path.abspath(filepath)}"
        prep_res = self.lsp_client._send_request(
            "textDocument/prepareCallHierarchy", {"textDocument": {"uri": file_uri}, "position": {"line": line, "character": character}}
        )

        if not prep_res.get("result"):
            return {"error": "Could not resolve call hierarchy item at given position."}

        target_item = prep_res["result"][0]
        visited_incoming = set()
        visited_outgoing = set()

        return {"target": target_item, "callers_tree": self._get_incoming(target_item, layer, visited_incoming), "callees_tree": self._get_outgoing(target_item, layer, visited_outgoing)}

    def _make_item_key(self, item: dict) -> str:
        """Create a unique key for a call hierarchy item to detect cycles."""
        uri = item.get("uri", "")
        range_info = item.get("range", {})
        start = range_info.get("start", {})
        return f"{uri}:{start.get('line', 0)}:{start.get('character', 0)}"

    def _get_incoming(self, item: dict, depth: Optional[int], visited: set) -> List[dict]:
        if depth is not None and depth <= 0:
            return []
        item_key = self._make_item_key(item)
        if item_key in visited:
            return []
        visited.add(item_key)
        res = self.lsp_client._send_request("callHierarchy/incomingCalls", {"item": item})
        incoming = res.get("result", [])
        if incoming is None:
            return []
        tree = []
        for call in incoming:
            caller = call["from"]
            next_depth = depth - 1 if depth is not None else None
            tree.append({"caller": caller, "ranges": call["fromRanges"], "callers_tree": self._get_incoming(caller, next_depth, visited)})
        return tree

    def get_callers(self, filepath: str, func_name: str, layer: Optional[int] = None) -> List[dict]:
        """
        Get all callers of a function.

        Args:
            filepath: absolute path to the source file.
            func_name: function name (e.g. 'addUser').
            layer: max depth of caller hierarchy (None = unlimited).

        Returns:
            Flat list of all caller items (deduplicated by uri:line:character).
        """
        pos = get_function_position(filepath, func_name)
        if not pos:
            return []

        line, character = pos
        file_uri = f"file://{os.path.abspath(filepath)}"
        prep_res = self.lsp_client._send_request(
            "textDocument/prepareCallHierarchy",
            {"textDocument": {"uri": file_uri}, "position": {"line": line, "character": character}},
        )

        if not prep_res.get("result"):
            return []

        target_item = prep_res["result"][0]
        visited = set()
        tree = self._get_incoming(target_item, layer, visited)

        return self._flatten_callers(tree)

    @staticmethod
    def _flatten_callers(tree: List[dict]) -> List[dict]:
        """Flatten a caller tree into a deduplicated flat list."""
        result: List[dict] = []
        seen: set = set()

        def _walk(nodes: List[dict]):
            for node in nodes:
                caller = node.get("caller")
                if not caller:
                    continue
                uri = caller.get("uri", "")
                rng = caller.get("range", {})
                start = rng.get("start", {})
                key = f"{uri}:{start.get('line', 0)}:{start.get('character', 0)}"
                if key in seen:
                    _walk(node.get("callers_tree", []))
                    continue
                seen.add(key)
                result.append(caller)
                _walk(node.get("callers_tree", []))

        _walk(tree)
        return result

    def get_callees(self, filepath: str, func_name: str, layer: Optional[int] = None) -> List[dict]:
        """
        Get all callees of a function.

        Args:
            filepath: absolute path to the source file.
            func_name: function name (e.g. 'addUser').
            layer: max depth of callee hierarchy (None = unlimited).

        Returns:
            Flat list of all callee items (deduplicated by uri:line:character).
        """
        pos = get_function_position(filepath, func_name)
        if not pos:
            return []

        line, character = pos
        file_uri = f"file://{os.path.abspath(filepath)}"
        prep_res = self.lsp_client._send_request(
            "textDocument/prepareCallHierarchy",
            {"textDocument": {"uri": file_uri}, "position": {"line": line, "character": character}},
        )

        if not prep_res.get("result"):
            return []

        target_item = prep_res["result"][0]
        visited = set()
        tree = self._get_outgoing(target_item, layer, visited)

        return self._flatten_callees(tree)

    @staticmethod
    def _flatten_callees(tree: List[dict]) -> List[dict]:
        """Flatten a callee tree into a deduplicated flat list."""
        result: List[dict] = []
        seen: set = set()

        def _walk(nodes: List[dict]):
            for node in nodes:
                callee = node.get("callee")
                if not callee:
                    continue
                uri = callee.get("uri", "")
                rng = callee.get("range", {})
                start = rng.get("start", {})
                key = f"{uri}:{start.get('line', 0)}:{start.get('character', 0)}"
                if key in seen:
                    _walk(node.get("callees_tree", []))
                    continue
                seen.add(key)
                result.append(callee)
                _walk(node.get("callees_tree", []))

        _walk(tree)
        return result

    def _get_outgoing(self, item: dict, depth: Optional[int], visited: set) -> List[dict]:
        if depth is not None and depth <= 0:
            return []
        item_key = self._make_item_key(item)
        if item_key in visited:
            return []
        visited.add(item_key)
        res = self.lsp_client._send_request("callHierarchy/outgoingCalls", {"item": item})
        outgoing = res.get("result", [])
        if outgoing is None:
            return []
        tree = []
        for call in outgoing:
            callee = call["to"]
            next_depth = depth - 1 if depth is not None else None
            tree.append({"callee": callee, "ranges": call["fromRanges"], "callees_tree": self._get_outgoing(callee, next_depth, visited)})
        return tree
