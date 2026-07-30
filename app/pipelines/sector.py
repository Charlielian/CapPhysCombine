"""扇区冲突检测与自动修正。"""

from __future__ import annotations

import os
import re

import pandas as pd

from app.pipelines.common import (
    LTE_BANDS,
    GuiLogger,
    GuiProgress,
    LogCallback,
    ProgressCallback,
    apply_lte_network_structure,
    build_band_aggregations,
    co_site_coverage_type,
    normalize_text,
)
from app.pipelines.io import read_excel

SECTION_PATTERN = re.compile(r"(?:扇区|S)(\d+)", re.IGNORECASE)
BAND_SECTION_HINTS = {
    "F1": 1,
    "F2": 2,
    "E1": 1,
    "E2": 2,
    "E3": 3,
    "D1": 1,
    "D3": 3,
    "D7": 7,
    "D8": 8,
}


def extract_section_no_from_name(name):
    text = normalize_text(name)
    if not text:
        return None
    m = SECTION_PATTERN.search(text)
    if m:
        return int(m.group(1))
    return None


def _guess_section_no(row):
    name_no = extract_section_no_from_name(row.get("小区名称", ""))
    if name_no is not None:
        return name_no
    band_a = normalize_text(row.get("BAND_A", ""))
    if band_a in BAND_SECTION_HINTS:
        return BAND_SECTION_HINTS[band_a]
    return None


def detect_sector_conflicts(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    required = {"物理站", "BAND", "sectionid", "CGI", "小区名称", "共站同覆盖名"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"缺少列: {', '.join(sorted(missing))}")

    work = df.copy()
    work["物理站"] = work["物理站"].map(normalize_text)
    work["BAND"] = work["BAND"].map(normalize_text)
    work["sectionid"] = pd.to_numeric(work["sectionid"], errors="coerce")

    conflict_frames = [grp for _, grp in work.groupby(["物理站", "BAND", "sectionid"], dropna=False) if len(grp) > 1]
    if not conflict_frames:
        return work.iloc[0:0].copy()
    return pd.concat(conflict_frames, ignore_index=True)


def suggest_sector_fixes(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    work = df.copy()
    work["建议扇区号"] = work.apply(_guess_section_no, axis=1)
    return work


def recompute_derived_fields(df: pd.DataFrame) -> pd.DataFrame:
    """修正 sectionid 后，重算依赖列，保持汇总口径一致。"""
    work = df.copy()
    if "物理扇区制式" in work.columns:
        valid_sector = work[work["共站同覆盖名"].notna() & (work["共站同覆盖名"] != "")]
        if len(valid_sector) > 0:
            sector_all, sector_lte = build_band_aggregations(valid_sector, "共站同覆盖名", LTE_BANDS)
            work["物理扇区制式"] = work["共站同覆盖名"].map(sector_all).fillna("")
            work["物理扇区LTE制式"] = work["共站同覆盖名"].map(sector_lte).fillna("")
    if "物理站制式" in work.columns:
        valid_station = work[work["物理站"].notna() & (work["物理站"] != "")]
        if len(valid_station) > 0:
            station_all, station_lte = build_band_aggregations(valid_station, "物理站", LTE_BANDS)
            work["物理站制式"] = work["物理站"].map(station_all).fillna("")
            work["物理站LTE制式"] = work["物理站"].map(station_lte).fillna("")
    if {"物理扇区LTE制式", "覆盖层"}.issubset(work.columns):
        work = apply_lte_network_structure(work)
    if "物理站制式" in work.columns:
        work["共站制式情况"] = work["物理站制式"].apply(co_site_coverage_type)
    return work


def auto_fix_sector_conflicts(df: pd.DataFrame):
    if df.empty:
        return df.copy(), df.copy(), df.copy()
    work = df.copy()
    conflict_rows = []
    fix_rows = []

    group_cols = ["物理站", "BAND", "sectionid"]
    for _, grp in work.groupby(group_cols, dropna=False):
        if len(grp) <= 1:
            continue

        grp = grp.copy()
        grp["建议扇区号"] = grp.apply(_guess_section_no, axis=1)
        used = set()

        for idx, row in grp.sort_values(by=["建议扇区号", "方位角"], na_position="last").iterrows():
            conflict_rows.append(row)
            suggested = row.get("建议扇区号")
            new_section = None
            if pd.notna(suggested):
                suggested = int(suggested)
                if suggested not in used:
                    new_section = suggested
                    used.add(suggested)
            if new_section is None:
                candidate = 1
                while candidate in used:
                    candidate += 1
                new_section = candidate
                used.add(candidate)

            old_section = row.get("sectionid")
            old_name = row.get("共站同覆盖名", "")
            base_name = normalize_text(old_name)
            if base_name:
                base_name = re.sub(r"(?:-?扇区\d+|-?S\d+)$", "", base_name)
            else:
                base_name = normalize_text(row.get("物理站", ""))

            new_name = f"{base_name}-扇区{new_section}"
            work.at[idx, "sectionid"] = new_section
            work.at[idx, "共站同覆盖名"] = new_name
            fix_rows.append(
                {
                    "CGI": row.get("CGI", ""),
                    "小区名称": row.get("小区名称", ""),
                    "物理站": row.get("物理站", ""),
                    "站点类型": row.get("站点类型", ""),
                    "BAND": row.get("BAND", ""),
                    "原sectionid": old_section,
                    "新sectionid": new_section,
                    "原共站同覆盖名": old_name,
                    "新共站同覆盖名": new_name,
                    "建议扇区号": row.get("建议扇区号"),
                }
            )

    conflict_df = pd.DataFrame(conflict_rows).drop_duplicates()
    fix_df = pd.DataFrame(fix_rows)
    work = recompute_derived_fields(work)
    return work, conflict_df, fix_df




def run_physical_table_sector_fix(
    input_path: str,
    output_dir: str | None = None,
    auto_fix: bool = True,
    progress_callback: ProgressCallback | None = None,
    log_callback: LogCallback | None = None,
) -> dict:
    """检测并修正物理表扇区冲突

    Args:
        input_path: 物理表Excel文件路径
        output_dir: 输出目录，默认为输入文件所在目录
        auto_fix: 是否自动修正冲突
        progress_callback: 进度回调函数
        log_callback: 日志回调函数

    Returns:
        包含 fixed_df, conflict_df, fix_df 的字典
    """
    logger = GuiLogger(log_callback)
    progress = GuiProgress(progress_callback, logger)

    progress.update(15, f"读取物理表: {os.path.basename(input_path)}")
    df = read_excel(Path(input_path))

    progress.update(40, "检测扇区冲突...")
    conflicts = detect_sector_conflicts(df)

    if output_dir is None:
        output_dir = os.path.dirname(input_path) or "."

    base_name = os.path.splitext(os.path.basename(input_path))[0]

    result = {
        "conflicts": conflicts,
        "fixed_df": df.copy(),
        "conflict_df": pd.DataFrame(),
        "fix_df": pd.DataFrame(),
    }

    if conflicts.empty:
        progress.update(100, "未发现扇区冲突")
        logger.log("未发现扇区冲突")
        return result

    logger.log(f"发现 {len(conflicts)} 条冲突记录")

    if auto_fix:
        progress.update(60, "开始自动修正扇区冲突...")
        fixed_df, conflict_df, fix_df = auto_fix_sector_conflicts(df)
        progress.update(85, "保存修正结果...")

        # 保存结果
        out_path = os.path.join(output_dir, f"{base_name}-已修正.xlsx")
        fixed_df.to_excel(out_path, index=False)
        logger.log(f"已保存修正结果: {out_path}")

        if not conflict_df.empty:
            conflict_out = os.path.join(output_dir, f"{base_name}-扇区冲突明细.xlsx")
            conflict_df.to_excel(conflict_out, index=False)
            logger.log(f"已保存冲突明细: {conflict_out}")

        if not fix_df.empty:
            fix_out = os.path.join(output_dir, f"{base_name}-扇区修正明细.xlsx")
            fix_df.to_excel(fix_out, index=False)
            logger.log(f"已保存修正明细: {fix_out}")

        result["fixed_df"] = fixed_df
        result["conflict_df"] = conflict_df
        result["fix_df"] = fix_df
        progress.update(100, f"修正完成：冲突 {len(conflict_df)} 条，修正 {len(fix_df)} 条")

    return result



__all__ = [
    "detect_sector_conflicts",
    "suggest_sector_fixes",
    "recompute_derived_fields",
    "auto_fix_sector_conflicts",
    "run_physical_table_sector_fix",
]
