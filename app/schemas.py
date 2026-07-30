"""Shared API response schemas.

Provides uniform envelope models so every endpoint returns a consistent shape:
    { "ok": true, "data": ... }        on success
    { "ok": false, "error": ... }      on failure

Usage in routers:
    raise AppError(status_code=404, detail="文件不存在")   # auto-converts to JSON
    return SuccessResponse(data=result_dict)                # explicit success envelope
"""

from __future__ import annotations

from typing import Any, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, Field

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    """Structured error body returned by the global exception handler."""

    code: str = "error"
    message: str
    detail: str | None = None


class AppError(HTTPException):
    """Typed HTTP error that carries a machine-readable *code*.

    Raising ``AppError(status_code=404, code="not_found", detail="…")``
    from any router will be caught by the global handler in ``app/main.py``
    and rendered as ``{ "ok": false, "error": { … } }``.
    """

    def __init__(
        self,
        status_code: int = 400,
        detail: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code or _status_to_code(status_code)


def _status_to_code(status: int) -> str:
    if status == 400:
        return "bad_request"
    if status == 404:
        return "not_found"
    if status == 409:
        return "conflict"
    if status == 422:
        return "validation_error"
    if status >= 500:
        return "internal_error"
    return "error"


# ---------------------------------------------------------------------------
# Success / paginated envelopes
# ---------------------------------------------------------------------------


class SuccessResponse(BaseModel):
    """Generic success envelope: ``{ "ok": true, "data": … }``."""

    ok: bool = Field(default=True, init=False)
    data: Any = None


class PaginatedResponse(BaseModel):
    """Paginated success envelope.

    Example::

        { "ok": true, "total": 42, "limit": 200, "offset": 0, "records": [...] }
    """

    ok: bool = Field(default=True, init=False)
    total: int = 0
    limit: int = 200
    offset: int = 0
    records: list[dict[str, Any]] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Error envelope: ``{ "ok": false, "error": { … } }``."""

    ok: bool = Field(default=False, init=False)
    error: ErrorDetail


# ---------------------------------------------------------------------------
# Job-specific schemas (reused by jobs_api router)
# ---------------------------------------------------------------------------


class JobSummary(BaseModel):
    """Lightweight representation of a job for list endpoints."""

    id: str
    kind: str
    status: str
    progress: int = 0
    message: str = ""
    created_at: str | None = None
    finished_at: str | None = None


class JobDetail(JobSummary):
    """Full job representation including logs and results."""

    logs: list[str] = Field(default_factory=list)
    result_files: list[str] = Field(default_factory=list)
    result_data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AssetInfo(BaseModel):
    """Describes a pipeline asset and its upstream dependencies."""

    name: str
    kind: str  # "pipeline" | "derived" | "input"
    depends_on: list[str] = Field(default_factory=list)


class AssetGraphResponse(BaseModel):
    """Response for GET /api/jobs/assets — the dependency DAG."""

    ok: bool = Field(default=True, init=False)
    assets: list[AssetInfo] = Field(default_factory=list)
    edges: list[dict[str, str]] = Field(default_factory=list)


__all__ = [
    "AppError",
    "AssetGraphResponse",
    "AssetInfo",
    "ErrorDetail",
    "ErrorResponse",
    "JobDetail",
    "JobSummary",
    "PaginatedResponse",
    "SuccessResponse",
]
