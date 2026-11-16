"""Minimal MCP stdio server exposing ``search_pages(site_key, location, type, keyword)``.

Run it with:

    python -m app.mcp

The server uses newline-delimited JSON-RPC 2.0, which is the stdio transport
used by MCP clients. It is intentionally dependency-free and reads only the
same per-site index files as the HTTP API.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from .config import Settings
from .index import IndexManager
from .tools import mcp_tool

log = logging.getLogger("bidgpt.mcp")
PROTOCOL_VERSION = "2024-11-05"


class McpServer:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.index_manager = IndexManager(self.settings.data_dir)

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if request_id is None:
            # Notification such as notifications/initialized.
            return None

        try:
            if method == "initialize":
                return self._ok(
                    request_id,
                    {
                        "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "bidgpt", "version": "1.0.0"},
                    },
                )
            if method == "ping":
                return self._ok(request_id, {})
            if method == "tools/list":
                return self._ok(request_id, {"tools": [mcp_tool()]})
            if method == "resources/list":
                return self._ok(request_id, {"resources": []})
            if method == "tools/call":
                name = params.get("name")
                if name != "search_pages":
                    return self._error(request_id, -32602, f"Unknown tool: {name}")
                arguments = params.get("arguments") or {}
                site_key = str(arguments.get("site_key") or "").strip()
                if not site_key:
                    return self._error(request_id, -32602, "site_key is required")
                index = self.index_manager.get(site_key)
                if index is None:
                    payload = {
                        "site_key": site_key,
                        "query": arguments,
                        "result_count": 0,
                        "results": [],
                        "message": "No index is ready for this site key.",
                    }
                else:
                    hits = index.search(
                        arguments.get("location"),
                        arguments.get("type"),
                        arguments.get("keyword"),
                        limit=8,
                    )
                    payload = {
                        "site_key": site_key,
                        "query": {
                            "location": arguments.get("location"),
                            "type": arguments.get("type"),
                            "keyword": arguments.get("keyword"),
                        },
                        "result_count": len(hits),
                        "results": [hit.public() for hit in hits],
                    }
                return self._ok(
                    request_id,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(payload, ensure_ascii=False, indent=2),
                            }
                        ],
                        "isError": False,
                    },
                )
            return self._error(request_id, -32601, f"Method not found: {method}")
        except Exception as exc:
            log.exception("MCP request failed")
            return self._error(request_id, -32603, f"Internal error: {exc}")

    @staticmethod
    def _ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def close(self) -> None:
        self.index_manager.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    server = McpServer()
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": "Parse error"},
                        }
                    ),
                    flush=True,
                )
                continue
            response = server.handle(message)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        server.close()


if __name__ == "__main__":
    main()
