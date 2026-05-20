import os
import json
import subprocess
import threading
from typing import Dict, List, Any, Optional
from definition import JDTLS_PATH
from parsers.tools import get_function_position

class JavaLSPClient:
    """
    A lightweight LSP client communicating with Java Language Server (JDT.LS) via stdio.
    """
    def __init__(self, project_root: str, jdtls_path: str = JDTLS_PATH ):
        self.project_root = project_root
        self.jdtls_path = jdtls_path
        self._process = None
        self._req_id = 1
        self._responses = {}

    def start(self):
        import traceback
        import hashlib

        # Use a data directory outside the project root to prevent "overlaps the workspace location" error
        proj_hash = hashlib.md5(self.project_root.encode('utf-8')).hexdigest()[:8]
        data_dir = os.path.abspath(os.path.expanduser(f"~/.cache/jdtls_workspace_{proj_hash}"))

        cmd = [self.jdtls_path, "-data", data_dir]
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.project_root,
            text=False
        )
        # Start a thread to read responses
        threading.Thread(target=self._read_loop, daemon=True).start()
        self._initialize()

    def _read_loop(self):
        while self._process and self._process.poll() is None:
            # Read Content-Length: ...
            header = self._process.stdout.readline().decode('utf-8')
            if not header.startswith("Content-Length:"):
                continue
            length = int(header.split(":")[1].strip())
            # Skip empty line
            self._process.stdout.readline()
            # Read body
            body = self._process.stdout.read(length).decode('utf-8')
            data = json.loads(body)

            # Print raw server status/progress notifications
            if "method" in data:
                print(f"[JDTLS Raw] {json.dumps(data, ensure_ascii=False)}")

            if "id" in data:
                self._responses[data["id"]] = data

    def _send_request(self, method: str, params: dict) -> dict:
        req_id = self._req_id
        self._req_id += 1
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params
        }
        body = json.dumps(msg)
        content = f"Content-Length: {len(body)}\r\n\r\n{body}"
        self._process.stdin.write(content.encode('utf-8'))
        self._process.stdin.flush()

        # In a real implementation, you would wait for the specific response id with a timeout
        import time
        for _ in range(50):
            if req_id in self._responses:
                return self._responses.pop(req_id)
            time.sleep(0.1)
        return {}

    def _send_notification(self, method: str, params: dict):
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        body = json.dumps(msg)
        content = f"Content-Length: {len(body)}\r\n\r\n{body}"
        self._process.stdin.write(content.encode('utf-8'))
        self._process.stdin.flush()

    def _initialize(self):
        res = self._send_request("initialize", {
            "processId": os.getpid(),
            "rootUri": f"file://{self.project_root}",
            "capabilities": {
                "textDocument": {
                    "callHierarchy": {"dynamicRegistration": True}
                }
            }
        })
        self._send_notification("initialized", {})

    def stop(self):
        if self._process:
            self._process.terminate()

class JavaCallChainExtractor:
    def __init__(self, lsp_client: JavaLSPClient):
        self.lsp_client = lsp_client

    def get_call_chain(self, filepath: str, func_name: str, layer: int) -> dict:
        """
        Retrieves the call chain (callers and callees) up to 'layer' depth.
        Now it identifies the function position by func_name inside the file.
        """
        # 0. Get function position
        pos = get_function_position(filepath, func_name)
        if not pos:
            return {"error": f"Could not find function {func_name} in {filepath}"}

        line, character = pos
        # 1. Prepare Call Hierarchy Item
        file_uri = f"file://{os.path.abspath(filepath)}"
        prep_res = self.lsp_client._send_request("textDocument/prepareCallHierarchy", {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character}
        })

        if not prep_res.get("result"):
            return {"error": "Could not resolve call hierarchy item at given position."}

        target_item = prep_res["result"][0]

        return {
            "target": target_item,
            "callers_tree": self._get_incoming(target_item, layer),
            "callees_tree": self._get_outgoing(target_item, layer)
        }

    def _get_incoming(self, item: dict, depth: int) -> List[dict]:
        if depth <= 0:
            return []
        res = self.lsp_client._send_request("callHierarchy/incomingCalls", {"item": item})
        incoming = res.get("result", [])
        tree = []
        for call in incoming:
            caller = call["from"]
            tree.append({
                "caller": caller,
                "ranges": call["fromRanges"],
                "callers_tree": self._get_incoming(caller, depth - 1)
            })
        return tree

    def _get_outgoing(self, item: dict, depth: int) -> List[dict]:
        if depth <= 0:
            return []
        res = self.lsp_client._send_request("callHierarchy/outgoingCalls", {"item": item})
        outgoing = res.get("result", [])
        tree = []
        for call in outgoing:
            callee = call["to"]
            tree.append({
                "callee": callee,
                "ranges": call["fromRanges"],
                "callees_tree": self._get_outgoing(callee, depth - 1)
            })
        return tree

if __name__ == "__main__":
    import sys
    # Add project root to path so we can import parsers as a module
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import time

    project_root = "/Users/huangzhuochen/IdeaProjects/youlai-boot-master"
    target_file = f"{project_root}/src/main/java/com/youlai/boot/system/service/impl/UserServiceImpl.java"
    func_name = "updateUser"

    # You may need to provide the absolute path to `jdtls` if it's not in your PATH
    lsp_client = JavaLSPClient(project_root=project_root, jdtls_path="jdtls")

    print("Starting LSP Server and indexing project (this may take a few seconds)...")
    lsp_client.start()

    # Wait for the JDT.LS workspace to initialize
    time.sleep(10)

    try:
        extractor = JavaCallChainExtractor(lsp_client)
        print(f"Extracting call chain for layer=2...")
        result = extractor.get_call_chain(
            filepath=target_file,
            func_name=func_name,
            layer=2
        )
        print("\n=== Call Chain Result ===")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        lsp_client.stop()
        print("\nLSP Server stopped.")
