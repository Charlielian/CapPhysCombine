"""Shared API response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Structured error body returned by global exception handlers."""

    error: str
    detail: str = ""
    status_code: int = 500


class SuccessResponse(BaseModel):
    """Generic success envelope."""

    ok: bool = True
    message: str = ""
    data: dict[str, Any] | list[Any] | None = None


class JobSummary(BaseModel):
    """Compact representation of a job for list endpoints."""

    id: str
    kind: str
    status: str
    progress: int = 0
    message: str = ""
    created_at: str
    finished_at: str | None = None
    result_files: list[str] = Field(default_factory=list)
    error: str | None = None


class JobDetail(JobSummary):
    """Full job representation including logs and result_data."""

    logs: list[str] = Field(default_factory=list)
    result_data: dict[str, Any] = Field(default_factory=dict)


class JobStartResponse(BaseModel):
    """Returned when a job is successfully enqueued."""

    ok: bool = True
    job: JobSummary


class AssetStatus(BaseModel):
    """One pipeline output asset and its last run info."""

    name: str
    last_run: str | None = None
    last_status: str | None = None
    deps: list[str] = Field(default_factory=list)
