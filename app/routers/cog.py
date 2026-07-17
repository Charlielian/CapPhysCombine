from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import BASE_DIR
from app.jsonutil import df_records
from app.pipelines.core import CogCoverageManager

router = APIRouter(prefix="/api/cog", tags=["cog"])


class CogRecord(BaseModel):
    CGI: str
    共站同覆盖名: str | None = None
    物理站名: str | None = None
    小区名称: str | None = None
    使用频段: str | None = None
    是否覆盖层: Any = None
    小区所属区域: str | None = None
    路测网格: str | None = None
    经度: float | None = None
    纬度: float | None = None
    sectionid: int | None = None


class CogUpdate(BaseModel):
    共站同覆盖名: str | None = None
    物理站名: str | None = None
    小区名称: str | None = None
    使用频段: str | None = None
    是否覆盖层: Any = None
    小区所属区域: str | None = None
    路测网格: str | None = None
    经度: float | None = None
    纬度: float | None = None
    sectionid: int | None = None


def _df_records(df: pd.DataFrame) -> list[dict]:
    return df_records(df)


@router.get("")
def list_cog(
    q: str | None = Query(None, description="搜索关键字"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    with CogCoverageManager() as mgr:
        if q:
            df = mgr.search(q)
            total = len(df)
            df = df.iloc[offset : offset + limit]
        else:
            df = mgr.get_all(limit=limit, offset=offset)
            total_df = mgr.conn.execute(
                "SELECT COUNT(*) AS c FROM 共站同覆盖小区表"
            ).fetchdf()
            total = int(total_df.iloc[0]["c"]) if not total_df.empty else 0
        return {"total": total, "records": _df_records(df)}


@router.get("/export/excel")
def export_cog():
    out = BASE_DIR / "共站同覆盖导出.xlsx"
    with CogCoverageManager() as mgr:
        count = mgr.export_to_excel(out)
    if count == 0:
        raise HTTPException(status_code=404, detail="无可导出数据")
    return FileResponse(
        out,
        filename=out.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/import")
async def import_cog(file: UploadFile = File(...), replace: bool = False):
    if not file.filename:
        raise HTTPException(status_code=400, detail="未选择文件")
    suffix = Path(file.filename).suffix or ".xlsx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    try:
        with CogCoverageManager() as mgr:
            count = mgr.import_from_excel(tmp_path, replace=replace)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"imported": count}


@router.post("")
def create_cog(record: CogRecord):
    with CogCoverageManager() as mgr:
        existing = mgr.get_by_cgi(record.CGI)
        if not existing.empty:
            raise HTTPException(status_code=409, detail="CGI 已存在")
        ok = mgr.add(record.model_dump())
        if not ok:
            raise HTTPException(status_code=400, detail="添加失败")
    return {"ok": True}


@router.get("/{cgi}")
def get_cog(cgi: str):
    with CogCoverageManager() as mgr:
        df = mgr.get_by_cgi(cgi)
        if df.empty:
            raise HTTPException(status_code=404, detail="记录不存在")
        return _df_records(df)[0]


@router.put("/{cgi}")
def update_cog(cgi: str, record: CogUpdate):
    with CogCoverageManager() as mgr:
        existing = mgr.get_by_cgi(cgi)
        if existing.empty:
            raise HTTPException(status_code=404, detail="记录不存在")
        payload = {k: v for k, v in record.model_dump().items() if v is not None}
        base = existing.iloc[0].to_dict()
        base.update(payload)
        ok = mgr.update(cgi, base)
        if not ok:
            raise HTTPException(status_code=400, detail="更新失败")
    return {"ok": True}


@router.delete("/{cgi}")
def delete_cog(cgi: str):
    with CogCoverageManager() as mgr:
        ok = mgr.delete(cgi)
        if not ok:
            raise HTTPException(status_code=400, detail="删除失败")
    return {"ok": True}
