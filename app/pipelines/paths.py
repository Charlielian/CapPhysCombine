"""项目路径常量与文件模式。"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def _get_base_dir() -> Path:
    """返回项目根目录（兼容 PyInstaller 打包和源码运行）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


BASE_DIR = _get_base_dir()
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
LOG_RETENTION_DAYS = 7
DB_PATH = BASE_DIR / "capphys.db"
UNIFIED_DB_PATH = BASE_DIR / "capphys_unified.db"
LOWEFF_OUTPUT_PATH = BASE_DIR / "低效小区结果.xlsx"
# Parquet 中间层缓存目录（避免每次 init_db 重新解析 Excel）
PARQUET_CACHE_DIR = BASE_DIR / ".cache" / "parquet"

OUTPUT_5G = BASE_DIR / "合成_容量表_5G.xlsx"
OUTPUT_4G = BASE_DIR / "合成_容量表_4G.xlsx"
OUTPUT_45G = BASE_DIR / "容量表_45G.xlsx"

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
TIME_COLUMN_CANDIDATES = {
    "start": ["记录开始时间", "开始时间"],
    "end": ["记录结束时间", "结束时间"],
}

FILE_PATTERNS = {
    "5g_week": "5G小区容量-周*.xlsx",
    "5g_day": "5G小区容量报表*.xlsx",
    "5g_mr": "5GMR覆盖-小区天*.xlsx",
    "5g_kpi": "5G小区性能KPI报表*.xlsx",
    "4g_week": "重要场景-周*.xlsx",
    "4g_day": "重要场景-天*.xlsx",
    "4g_mr": "4GMR覆盖-小区天*.xlsx",
    "cog_coverage": "共站同覆盖小区_4g_5g.xlsx",
}

PHYSICAL_FILE_PATTERNS = {
    "nr_cellant": "*_nr_*.xlsx",
    "lte_cellant": "*_lte_*.xlsx",
}

CHUNK_SIZE = 5000


__all__ = [
    "BASE_DIR",
    "DATA_DIR",
    "LOG_DIR",
    "LOG_RETENTION_DAYS",
    "DB_PATH",
    "UNIFIED_DB_PATH",
    "LOWEFF_OUTPUT_PATH",
    "PARQUET_CACHE_DIR",
    "OUTPUT_5G",
    "OUTPUT_4G",
    "OUTPUT_45G",
    "DATE_RE",
    "TIME_COLUMN_CANDIDATES",
    "FILE_PATTERNS",
    "PHYSICAL_FILE_PATTERNS",
    "CHUNK_SIZE",
]
