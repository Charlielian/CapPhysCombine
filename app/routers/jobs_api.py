"""Job management API — capacity / physical / nrm_sync / conflicts / loweff / zero-low-flow.

Uses FastAPI dependency injection for ``JobManager`` so the router is
fully testable (swap ``app.dependency_overrides[get_job_manager]`` in tests).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.jobs import JobManager
from app.jobs import job_manager as _default_manager
from app.pipelines.core import discover_4g_week_files
from app.schemas import (
    AppError,
    SuccessResponse,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


def get_job_manager() -> JobManager:
    """Return the shared ``JobManager`` singleton.

    Production: module-level singleton.
    Tests:      override via ``app.dependency_overrides[get_job_manager]``.
    """
    return _default_manager


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class ZeroLowFlowRequest(BaseModel):
    file_paths: list[str] | None = None
    network: str = "4g"


class MultiWeekLoweffRequest(BaseModel):
    week_file_paths: list[str]


# ---------------------------------------------------------------------------
# Asset dependency graph
# ---------------------------------------------------------------------------


@router.get("/assets")
def asset_graph(
    mgr: JobManager = Depends(get_job_manager),
) -> dict[str, Any]:
    """返回 pipeline 依赖图（节点 + 边 + 最近运行状态）。"""
    status = mgr.get_asset_graph_status()
    # Merge edges from ASSET_DEPS
    edges: list[dict[str, str]] = []
    for item in status:
        for dep in item["depends_on"]:
            edges.append({"from": dep, "to": item["name"]})
    return SuccessResponse(data={"assets": status, "edges": edges}).model_dump()


# ---------------------------------------------------------------------------
# Job CRUD
# ---------------------------------------------------------------------------


@router.get("")
def list_jobs(
    limit: int = Query(50, ge=1, le=500),
    mgr: JobManager = Depends(get_job_manager),
) -> dict[str, Any]:
    """列出任务历史（内存 + DuckDB 持久化合并，按创建时间倒序）。"""
    jobs = mgr.list_history(limit=limit)
    return SuccessResponse(data=jobs).model_dump()


@router.get("/current")
def current_job(
    mgr: JobManager = Depends(get_job_manager),
) -> dict[str, Any]:
    """返回当前正在运行的任务（无则返回 null）。"""
    job = mgr.current()
    return SuccessResponse(data=job.to_dict() if job else None).model_dump()


@router.get("/{job_id}")
def get_job(
    job_id: str,
    mgr: JobManager = Depends(get_job_manager),
) -> dict[str, Any]:
    """按 ID 获取任务详情。"""
    job = mgr.get(job_id)
    if job is None:
        raise AppError(status_code=404, detail=f"任务 {job_id} 不存在")
    return SuccessResponse(data=job.to_dict()).model_dump()


# ---------------------------------------------------------------------------
# Start endpoints — each enforces the asset dependency graph automatically.
# ---------------------------------------------------------------------------


def _start(mgr: JobManager, kind: str, start_fn: Any) -> dict[str, Any]:
    """Shared helper: call *start_fn*, catch RuntimeError (single-flight or deps)."""
    try:
        job = start_fn()
    except RuntimeError as exc:
        raise AppError(status_code=409, detail=str(exc)) from exc
    return SuccessResponse(data=job.to_dict()).model_dump()


@router.post("/start/capacity")
def start_capacity(mgr: JobManager = Depends(get_job_manager)) -> dict[str, Any]:
    """启动容量表合成任务。"""
    return _start(mgr, "capacity", mgr.start_capacity)


@router.post("/start/physical")
def start_physical(mgr: JobManager = Depends(get_job_manager)) -> dict[str, Any]:
    """启动物理表汇总任务。"""
    return _start(mgr, "physical", mgr.start_physical)


@router.post("/start/nrm-sync")
def start_nrm_sync(
    nrm_dir: str | None = Query(None),
    update_freq: bool = Query(True),
    mgr: JobManager = Depends(get_job_manager),
) -> dict[str, Any]:
    """启动网管数据同步任务。"""
    return _start(mgr, "nrm_sync", lambda: mgr.start_nrm_sync(nrm_dir=nrm_dir, update_freq=update_freq))


@router.post("/start/loweff")
def start_loweff(mgr: JobManager = Depends(get_job_manager)) -> dict[str, Any]:
    """启动低效小区分析任务。"""
    return _start(mgr, "loweff", mgr.start_loweff)


@router.post("/start/conflicts/check")
def start_conflicts_check(mgr: JobManager = Depends(get_job_manager)) -> dict[str, Any]:
    """启动扇区冲突检测任务。"""
    return _start(mgr, "conflicts_check", mgr.start_check_conflicts)


@router.post("/start/conflicts/fix")
def start_conflicts_fix(mgr: JobManager = Depends(get_job_manager)) -> dict[str, Any]:
    """启动扇区冲突修正任务。"""
    return _start(mgr, "conflicts_fix", mgr.start_fix_conflicts)


@router.post("/start/zero-low-flow")
def start_zero_low_flow(
    body: ZeroLowFlowRequest | None = None,
    mgr: JobManager = Depends(get_job_manager),
) -> dict[str, Any]:
    """启动零低流量风险分析任务。"""
    file_paths = body.file_paths if body else None
    network = body.network if body else "4g"
    kind = f"zero_low_flow_{network}"
    return _start(
        mgr,
        kind,
        lambda: mgr.start_zero_low_flow(file_paths=file_paths, network=network),
    )


# ---------------------------------------------------------------------------
# Multi-week 4G evaluation
# ---------------------------------------------------------------------------


@router.get("/multi-week/weeks")
def list_week_files() -> dict[str, Any]:
    """扫描 data/ 及子目录，返回所有可用的 重要场景-周*.xlsx 文件。"""
    weeks = discover_4g_week_files()
    return SuccessResponse(data=weeks).model_dump()


@router.post("/start/multi-week-loweff")
def start_multi_week_loweff(
    body: MultiWeekLoweffRequest,
    mgr: JobManager = Depends(get_job_manager),
) -> dict[str, Any]:
    """启动多周期4G全量评估任务。"""
    return _start(
        mgr,
        "multi_week_loweff",
        lambda: mgr.start_multi_week_loweff(week_file_paths=body.week_file_paths),
    )
