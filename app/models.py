from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SiteCreate(BaseModel):
    url: str = Field(min_length=4, max_length=2_000)


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=4_000)


class ChatRequest(BaseModel):
    site_key: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    message: str = Field(min_length=1, max_length=2_000)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=20)
