"""Request/response schemas."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class JobCreate(BaseModel):
    operation: str = Field(min_length=1, max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=160)

    @field_validator("operation")
    @classmethod
    def _known_operation(cls, value: str) -> str:
        from app.operations import OPERATIONS

        if value not in OPERATIONS:
            raise ValueError(f"Unsupported operation: {value}")
        return value


class SessionUploadResult(BaseModel):
    stored: bool
    encrypted: bool
    sessionid: str | None = None
    cookies: int = 0
    dropped_cookies: list[str] = Field(default_factory=list)
    status: str = "unverified"
