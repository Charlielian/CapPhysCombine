from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import BASE_DIR, DATA_DIR
from app.pipelines.core import get_data_file_status, list_output_files

router = APIRouter(prefix="/api/data", tags=["data"])

CAPACITY_LABELS = {
    "5g_week": "5G小区容量(周)",
    "5g_day": "5G小区容量(天)",
    "5g_mr": "5G MR覆盖",
    "5g_kpi": "5G KPI报表",
    "4g_week": "4G重要场景(周)",
    "4g_day": "4G重要场景(天)",
    "4g_mr": "4G MR覆盖",
    "cog_coverage": "共站同覆盖(可选)",
}

PHYSICAL_LABELS = {
    "nr_cellant": "5G工参 (*_nr_*.xlsx)",
    "lte_cellant": "4G工参 (*_lte_*.xlsx)",
}


@router.get("/status")
def data_status():
    status = get_data_file_status()
    for item in status["capacity"]:
        item["label"] = CAPACITY_LABELS.get(item["key"], item["key"])
    for item in status["physical"]:
        item["label"] = PHYSICAL_LABELS.get(item["key"], item["key"])
    return status


@router.post("/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for upload in files:
        if not upload.filename:
            continue
        name = Path(upload.filename).name
        if not name.lower().endswith((".xlsx", ".xls", ".csv")):
            raise HTTPException(status_code=400, detail=f"不支持的文件类型: {name}")
        dest = DATA_DIR / name
        with dest.open("wb") as f:
            shutil.copyfileobj(upload.file, f)
        saved.append(name)
    return {"saved": saved, "count": len(saved)}


@router.get("/outputs")
def outputs():
    return {"files": list_output_files()}


@router.get("/outputs/{name}")
def download_output(name: str):
    safe_name = Path(name).name
    path = (BASE_DIR / safe_name).resolve()
    if path.parent != BASE_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if path.suffix.lower() not in {".xlsx", ".xls", ".csv"}:
        raise HTTPException(status_code=400, detail="仅允许下载表格文件")
    return FileResponse(
        path,
        filename=safe_name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/files")
def list_data_files():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for path in sorted(DATA_DIR.iterdir()):
        if path.is_file() and not path.name.startswith("."):
            files.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "mtime": path.stat().st_mtime,
                }
            )
    return {"files": files}
