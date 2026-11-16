from __future__ import annotations

import json

from app.config import Settings
from app.index import PageRecord, build_index
from app.mcp import McpServer


def test_mcp_lists_and_calls_search_pages(tmp_path):
    settings = Settings(data_dir=tmp_path)
    build_index(
        settings.site_dir("sk_mcp"),
        [
            PageRecord(
                url="https://example.com/guntur",
                title="Guntur Electrical Tender",
                text="Guntur electrical works.",
                tender_type="Electrical Works",
                location="Guntur",
                budget="Rs. 1,00,000",
                timeline="30 days",
                products="LED lights",
                department="Example",
                bid_by_date="2026-11-30",
                crawled_at="2026-10-30T00:00:00+00:00",
                content_hash="x",
            )
        ],
    )
    server = McpServer(settings)
    try:
        initialize = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert initialize["result"]["capabilities"]["tools"] == {}

        listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tool = listed["result"]["tools"][0]
        assert tool["name"] == "search_pages"
        assert "site_key" in tool["inputSchema"]["properties"]

        called = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "search_pages",
                    "arguments": {"site_key": "sk_mcp", "location": "Guntur"},
                },
            }
        )
        payload = json.loads(called["result"]["content"][0]["text"])
        assert payload["result_count"] == 1
        assert payload["results"][0]["bid_by_date"] == "2026-11-30"
    finally:
        server.close()
