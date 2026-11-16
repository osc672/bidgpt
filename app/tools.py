"""The single search tool exposed to LLM/MCP clients."""

from __future__ import annotations

from typing import Any

SEARCH_PAGES_DESCRIPTION = (
    "Search the tender page index of one registered website. Use this tool for "
    "every tender question. Returned pages are the only allowed source of "
    "truth. Location and type matching is case-insensitive and tolerant of "
    "minor spelling variants (for example Guntur/Guntoor)."
)

SEARCH_PAGES_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "location": {
            "type": "string",
            "description": "City, district or place, e.g. Guntur. Optional if keyword is supplied.",
        },
        "type": {
            "type": "string",
            "description": "Tender type/category, e.g. electrical, civil, housekeeping.",
        },
        "keyword": {
            "type": "string",
            "description": "Additional words to match in the page, e.g. LED street lights.",
        },
    },
    "additionalProperties": False,
}


def claude_tool(site_key: str | None = None) -> dict[str, Any]:
    description = SEARCH_PAGES_DESCRIPTION
    if site_key:
        description += f" The current site_key is {site_key}."
    return {
        "name": "search_pages",
        "description": description,
        "input_schema": SEARCH_PAGES_INPUT_SCHEMA,
    }


def openai_tool(site_key: str | None = None) -> dict[str, Any]:
    description = SEARCH_PAGES_DESCRIPTION
    if site_key:
        description += f" The current site_key is {site_key}."
    return {
        "type": "function",
        "function": {
            "name": "search_pages",
            "description": description,
            "parameters": SEARCH_PAGES_INPUT_SCHEMA,
        },
    }


def mcp_tool() -> dict[str, Any]:
    schema = dict(SEARCH_PAGES_INPUT_SCHEMA)
    schema = {
        "type": "object",
        "properties": {
            "site_key": {
                "type": "string",
                "description": "The site key returned when the website was registered.",
            },
            **SEARCH_PAGES_INPUT_SCHEMA["properties"],
        },
        "required": ["site_key"],
        "additionalProperties": False,
    }
    return {
        "name": "search_pages",
        "description": SEARCH_PAGES_DESCRIPTION,
        "inputSchema": schema,
    }
