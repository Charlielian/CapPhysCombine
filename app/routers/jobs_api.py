from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.jobs import job_manager

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/current")
def current_job():
    job = job_manager.current()
    return {
        "busy": job_manager.is_busy(),
        "job": job.to_dict() if job else None,
    }


@router.get("/{job_id}")
def get_job(job_id: str):
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job.to_dict()


@router.post("/capacity")
def start_capacity():
    try:
        job = job_manager.start_capacity()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.to_dict()


@router.post("/physical")
def start_physical():
    try:
        job = job_manager.start_physical()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.to_dict()


@router.post("/loweff")
def start_loweff():
    try:
        job = job_manager.start_loweff()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.to_dict()

@router.post("/physical/conflicts/check")
def start_check_conflicts():
    try:
        job = job_manager.start_check_conflicts()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.to_dict()


@router.post("/physical/conflicts/fix")
def start_fix_conflicts():
    try:
        job = job_manager.start_fix_conflicts()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.to_dict()


@router.post("/zero-low-flow")
def start_zero_low_flow():
    try:
        job = job_manager.start_zero_low_flow()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.to_dict()

