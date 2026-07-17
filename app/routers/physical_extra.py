from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from app.config import BASE_DIR, LOWEFF_OUTPUT_PATH
from app.jsonutil import df_records
from app.pipelines.core import (
    detect_sector_conflicts,
    run_physical_table_sector_fix,
)
from app.pipelines.zero_low_flow import latest_zero_low_flow_output

router = APIRouter(prefix="/api", tags=["physical"])


def _df_records(df: pd.DataFrame, limit: int = 500) -> list[dict]:
    return df_records(df, limit=limit)


@router.post("/physical/conflicts/check")
def check_conflicts(path: str | None = None):
    excel = Path(path) if path else BASE_DIR / "物理表汇总结果.xlsx"
    if not excel.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在: {excel.name}")
    try:
        df = pd.read_excel(excel)
        conflicts = detect_sector_conflicts(df)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "file": excel.name,
        "conflict_count": len(conflicts),
        "conflicts": _df_records(conflicts, limit=200),
    }


@router.post("/physical/conflicts/fix")
def fix_conflicts(path: str | None = None):
    excel = Path(path) if path else BASE_DIR / "物理表汇总结果.xlsx"
    if not excel.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在: {excel.name}")
    try:
        result = run_physical_table_sector_fix(
            input_path=str(excel),
            output_dir=str(BASE_DIR),
            auto_fix=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    files = []
    base_name = excel.stem
    for name in (
        f"{base_name}-已修正.xlsx",
        f"{base_name}-扇区冲突明细.xlsx",
        f"{base_name}-扇区修正明细.xlsx",
    ):
        if (BASE_DIR / name).is_file():
            files.append(name)

    return {
        "conflict_count": len(result.get("conflict_df", pd.DataFrame())),
        "fix_count": len(result.get("fix_df", pd.DataFrame())),
        "files": files,
    }


@router.get("/loweff/view")
def loweff_view(
    sheet: str = Query("5g", pattern="^(summary|5g|4g|4g_all)$"),
    keyword: str = Query(""),
    low_type: str = Query(""),
    band: str = Query(""),
    limit: int = Query(500, ge=1, le=5000),
):
    if not LOWEFF_OUTPUT_PATH.is_file():
        raise HTTPException(status_code=404, detail="尚未生成低效小区结果，请先运行任务")

    sheet_map = {
        "summary": "统计汇总",
        "5g": "5G低效明细",
        "4g": "4G低效明细",
        "4g_all": "全量4G小区评估",
    }
    try:
        df = pd.read_excel(LOWEFF_OUTPUT_PATH, sheet_name=sheet_map[sheet])
        summary_df = pd.read_excel(LOWEFF_OUTPUT_PATH, sheet_name="统计汇总")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if low_type and "低效类型" in df.columns:
        df = df[df["低效类型"].astype(str) == low_type]
    if band and "band" in df.columns:
        df = df[df["band"].astype(str) == band]
    if keyword:
        mask = pd.Series(False, index=df.index)
        for col in df.columns:
            mask = mask | df[col].astype(str).str.contains(keyword, case=False, na=False)
        df = df[mask]

    summary = {}
    if not summary_df.empty and {"指标", "数值"}.issubset(summary_df.columns):
        for _, row in summary_df.iterrows():
            key = str(row["指标"]).strip()
            val = row["数值"]
            summary[key] = None if pd.isna(val) else val

    return {
        "sheet": sheet,
        "total": len(df),
        "columns": list(df.columns.astype(str)),
        "records": _df_records(df, limit=limit),
        "file": LOWEFF_OUTPUT_PATH.name,
        "summary": summary,
    }


@router.get("/zero-low-flow/view")
def zero_low_flow_view(
    sheet: str = Query("risk", pattern="^(summary|risk|all)$"),
    keyword: str = Query(""),
    risk: str = Query(""),
    status: str = Query(""),
    limit: int = Query(500, ge=1, le=5000),
):
    path = latest_zero_low_flow_output()
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="尚未生成零低流量风险结果，请先运行任务")

    sheet_map = {
        "summary": "监控汇总",
        "risk": "风险小区明细",
        "all": "全量监控明细",
    }
    try:
        df = pd.read_excel(path, sheet_name=sheet_map[sheet])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if risk and "风险等级" in df.columns:
        df = df[df["风险等级"].astype(str) == risk]
    if status and "当日状态" in df.columns:
        df = df[df["当日状态"].astype(str) == status]

    if keyword:
        mask = pd.Series(False, index=df.index)
        for col in df.columns:
            mask = mask | df[col].astype(str).str.contains(keyword, case=False, na=False)
        df = df[mask]

    return {
        "sheet": sheet,
        "total": len(df),
        "columns": list(df.columns.astype(str)),
        "records": _df_records(df, limit=limit),
        "file": path.name,
    }

