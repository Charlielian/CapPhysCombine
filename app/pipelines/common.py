"""Shared paths, constants, logging, and IO helpers.

This module is now a backward-compatibility shim that re-exports from:
- app.pipelines.paths     (path constants, file patterns)
- app.pipelines.logging_util (logging setup, GuiLogger, GuiProgress)
- app.pipelines.io        (DuckDB / Excel IO)
"""

# 兼容层说明：
# common.py 仍是旧调用方使用的公共入口，实际实现已拆到 paths、logging_util 和 io。
# 新代码应优先从职责明确的子模块导入；这里的 re-export 用于保持历史脚本和外部调用兼容。

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from app.pipelines.io import (
    cache_stats,
    clear_parquet_cache,
    db_to_dataframe,
    excel_to_db,
    get_db_connection,
    get_table_columns,
    get_unified_db_connection,
    init_db,
    load_small_table,
    read_excel,
    small_excel_to_db,
    table_exists,
)
from app.pipelines.logging_util import (
    GuiLogger,
    GuiProgress,
    LogCallback,
    ProgressCallback,
    SourceFileError,
    cleanup_old_logs,
    get_logger,
    setup_logging,
)

# Re-export everything from sub-modules for backward compatibility
from app.pipelines.paths import (
    BASE_DIR,
    CHUNK_SIZE,
    DATA_DIR,
    DATE_RE,
    DB_PATH,
    FILE_PATTERNS,
    LOG_DIR,
    LOG_RETENTION_DAYS,
    LOWEFF_OUTPUT_PATH,
    OUTPUT_4G,
    OUTPUT_5G,
    OUTPUT_45G,
    PHYSICAL_FILE_PATTERNS,
    TIME_COLUMN_CANDIDATES,
    UNIFIED_DB_PATH,
)

# ==============================================================================
# Band / frequency mapping constants (kept here for shared usage)
# ==============================================================================

NR_FREQ_MAPPING = {
    "2.6GHz": {"2.6G一载波": 504990, "2.6G二载波": 524910},
    "4.9GHz": {"4.9G一载波": 721824},
    "700M": {"700M": 152650},
}

LTE_BAND_MAPPING = {
    "F1": "F",
    "F2": "F",
    "FDD1800": "FDD1800",
    "A频段": "A",
    "FDD900": "FDD900",
    "E1": "E",
    "E2": "E",
    "E3": "E",
    "D1": "D",
    "D3": "D",
    "D7": "D",
    "D8": "D",
    "NB": "NB",
}

BAND_3DMIMO = {"D1", "D3", "D7", "D8"}

LTE_FREQ_MAPPING = {
    "F1": 38400,
    "F2": 38544,
    "FDD1800": [1300, 1301],
    "A频段": 36275,
    "FDD900": 3590,
    "E1": 38950,
    "E2": 39148,
    "E3": 39292,
    "D1": 40936,
    "D3": 40936,
    "D7": 41134,
    "D8": 41332,
    "NB": None,
}

LTE_BANDS = {"F", "FDD1800", "FDD900", "D", "E", "A", "NB", "5G-3Dmimo"}
NR_BANDS = {"700M", "2.6GHz", "4.9GHz"}

DISTANCE_INDOOR_M = 100
DISTANCE_MACRO_M = 50

COVERAGE_LAYER_MAP = {
    "True": 1,
    "1": 1,
    "是": 1,
    "Yes": 1,
    "False": 0,
    "0": 0,
    "否": 0,
    "No": 0,
}

LARGE_TABLES = {"5g_day", "5g_mr", "5g_kpi", "4g_day", "4g_mr"}

PHYSICAL_TABLE_AVAILABLE = True


# ==============================================================================
# Utility functions
# ==============================================================================


def pick_latest_file(pattern: str):
    from pathlib import Path

    matches = sorted(DATA_DIR.glob(pattern))
    if not matches:
        raise SourceFileError(f"未找到匹配文件: {DATA_DIR / pattern}")

    def sort_key(path: Path) -> tuple[tuple[str, ...], str]:
        dates = tuple(DATE_RE.findall(path.name))
        return dates, path.name

    return max(matches, key=sort_key)


def first_existing(df: pd.DataFrame, columns: Iterable[str], default=None):
    for column in columns:
        if column in df.columns:
            return df[column]
    return pd.Series(default, index=df.index)


def to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.replace(0, pd.NA)
    return numerator.div(denominator)


def normalize_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def build_output_paths(timestamp: str | None = None) -> dict:

    suffix = f"_{timestamp}" if timestamp else ""
    return {
        "5g": BASE_DIR / f"合成_容量表_5G{suffix}.xlsx",
        "4g": BASE_DIR / f"合成_容量表_4G{suffix}.xlsx",
        "45g": BASE_DIR / f"容量表_45G{suffix}.xlsx",
    }


def first_valid_timestamp(df: pd.DataFrame, candidates: list[str]):
    import pandas as _pd

    for column in candidates:
        if column not in df.columns:
            continue
        series = _pd.to_datetime(df[column], errors="coerce").dropna()
        if not series.empty:
            return series.iloc[0]
    return None


def pick_matching_files(pattern: str):
    from pathlib import Path

    matches = list(DATA_DIR.glob(pattern))
    if not matches:
        return []

    def sort_key(path: Path) -> tuple[tuple[str, ...], str]:
        dates = tuple(DATE_RE.findall(path.name))
        return dates, path.name

    return sorted(matches, key=sort_key)


def resolve_output_timestamp(conn) -> str | None:
    for source_name in ["5g_week", "4g_week"]:
        if not table_exists(conn, source_name):
            continue
        df = db_to_dataframe(f'SELECT * FROM "{source_name}" LIMIT 100', conn)
        if df.empty:
            continue
        start_time = first_valid_timestamp(df, TIME_COLUMN_CANDIDATES["start"])
        end_time = first_valid_timestamp(df, TIME_COLUMN_CANDIDATES["end"])
        if start_time is not None and end_time is not None:
            return f"{start_time:%Y%m%d}_{end_time:%Y%m%d}"
    return None


def normalize_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


# ==============================================================================
# Band aggregation helpers
# ==============================================================================


def map_lte_band(band_a: str, cell_name: str) -> str:
    if band_a in BAND_3DMIMO and "RD-Z" in str(cell_name):
        return "5G-3Dmimo"
    return LTE_BAND_MAPPING.get(band_a, band_a)


def build_band_aggregations(df: pd.DataFrame, group_col: str, lte_bands=None):
    lte_bands = lte_bands or LTE_BANDS

    def calc_bands(x):
        bands = {str(b) for b in x if b and str(b) != "nan"}
        return "/".join(sorted(bands))

    def calc_lte_bands(x):
        lte_set = {str(b) for b in x if b in lte_bands}
        return "/".join(sorted(lte_set))

    agg_all = df.groupby(group_col)["BAND"].apply(calc_bands).to_dict()
    agg_lte = df.groupby(group_col)["BAND"].apply(calc_lte_bands).to_dict()
    return agg_all, agg_lte


def co_site_coverage_type(station_bands_str: str) -> str:
    if not station_bands_str:
        return ""
    bands = set(station_bands_str.split("/"))
    has_nr = bool(bands & NR_BANDS)
    has_lte = bool(bands & LTE_BANDS)
    if has_nr and has_lte:
        return "有5有4"
    if has_nr and not has_lte:
        return "有5无4"
    if not has_nr and has_lte:
        return "无5有4"
    return ""


def apply_lte_network_structure(agg_df: pd.DataFrame) -> pd.DataFrame:
    df = agg_df.copy()
    df["LTE频段数"] = df["物理扇区LTE制式"].str.count("/") + (df["物理扇区LTE制式"] != "").astype(int)
    df["覆盖层值"] = df["覆盖层"].astype(str).map(COVERAGE_LAYER_MAP).fillna(-1).astype(int)
    conditions = [
        (df["覆盖层值"] == 1) & (df["LTE频段数"] == 1),
        (df["覆盖层值"] == 0) & (df["LTE频段数"] == 1),
        (df["覆盖层值"] == 1) & (df["LTE频段数"] > 1),
        (df["覆盖层值"] == 0) & (df["LTE频段数"] > 1),
    ]
    choices = ["单层网_覆盖层", "单层网", "多层网_覆盖层", "多层网"]
    df["网络结构4G"] = np.select(conditions, choices, default="")
    df.drop(columns=["LTE频段数", "覆盖层值"], inplace=True)
    return df


def get_data_file_status() -> dict:
    """扫描 data/ 目录，返回容量表与物理表源文件就绪状态。"""
    capacity: list[dict] = []
    capacity_ready = 0
    for key, pattern in FILE_PATTERNS.items():
        files = pick_matching_files(pattern)
        optional = key == "cog_coverage"
        found = len(files) > 0
        if found or optional:
            if found:
                capacity_ready += 1
        capacity.append(
            {
                "key": key,
                "pattern": pattern,
                "found": found,
                "optional": optional,
                "files": [f.name for f in files],
                "count": len(files),
            }
        )

    physical: list[dict] = []
    physical_ready = 0
    for key, pattern in PHYSICAL_FILE_PATTERNS.items():
        files = sorted(DATA_DIR.glob(pattern))
        files = [f for f in files if not f.name.startswith(".~")]
        found = len(files) > 0
        if found:
            physical_ready += 1
        physical.append(
            {
                "key": key,
                "pattern": pattern,
                "found": found,
                "optional": False,
                "files": [f.name for f in files],
                "count": len(files),
            }
        )

    required_capacity = len([k for k in FILE_PATTERNS if k != "cog_coverage"])
    required_found = sum(1 for item in capacity if item["found"] and not item["optional"])
    return {
        "data_dir": str(DATA_DIR),
        "capacity": capacity,
        "physical": physical,
        "capacity_ready": capacity_ready,
        "capacity_total": len(FILE_PATTERNS),
        "capacity_required_ready": required_found == required_capacity,
        "physical_ready": physical_ready,
        "physical_total": len(PHYSICAL_FILE_PATTERNS),
        "physical_all_ready": physical_ready == len(PHYSICAL_FILE_PATTERNS),
    }


def list_output_files() -> list[dict]:
    """列出项目根目录下可下载的结果 Excel。"""
    patterns = [
        "合成_容量表_*.xlsx",
        "容量表_45G_*.xlsx",
        "物理表汇总结果.xlsx",
        "物理表汇总结果-*.xlsx",
        "低效小区结果.xlsx",
        "4G日监控_零低流量风险小区_*.xlsx",
        "*-扇区冲突明细.xlsx",
        "*-扇区修正明细.xlsx",
        "*-已修正.xlsx",
    ]
    seen: set = set()
    results: list[dict] = []
    for pattern in patterns:
        for path in sorted(BASE_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            results.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "mtime": path.stat().st_mtime,
                }
            )
    return results


__all__ = [
    # Paths
    "BASE_DIR", "DATA_DIR", "LOG_DIR", "LOG_RETENTION_DAYS", "UNIFIED_DB_PATH", "DB_PATH",
    "CHUNK_SIZE", "LOWEFF_OUTPUT_PATH", "FILE_PATTERNS", "PHYSICAL_FILE_PATTERNS",
    "OUTPUT_5G", "OUTPUT_4G", "OUTPUT_45G", "DATE_RE", "TIME_COLUMN_CANDIDATES",
    # Logging
    "ProgressCallback", "LogCallback", "SourceFileError", "setup_logging", "cleanup_old_logs",
    "get_logger", "GuiLogger", "GuiProgress",
    # IO
    "get_db_connection", "get_unified_db_connection", "init_db", "read_excel",
    "excel_to_db", "small_excel_to_db", "db_to_dataframe", "table_exists",
    "get_table_columns", "load_small_table", "clear_parquet_cache", "cache_stats",
    # Band constants
    "NR_FREQ_MAPPING", "LTE_BAND_MAPPING", "BAND_3DMIMO", "LTE_FREQ_MAPPING",
    "LTE_BANDS", "NR_BANDS", "DISTANCE_INDOOR_M", "DISTANCE_MACRO_M",
    "COVERAGE_LAYER_MAP", "LARGE_TABLES",
    # Utility functions
    "pick_latest_file", "first_existing", "to_numeric", "safe_divide",
    "normalize_datetime", "build_output_paths", "first_valid_timestamp",
    "pick_matching_files", "resolve_output_timestamp", "normalize_text",
    # Band helpers
    "map_lte_band", "build_band_aggregations", "co_site_coverage_type",
    "apply_lte_network_structure", "get_data_file_status", "list_output_files",
    "PHYSICAL_TABLE_AVAILABLE",
]
