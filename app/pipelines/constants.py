"""Shared constants re-exports."""

# 常量出口说明：
# 该模块只重新导出共享业务常量，目的是让旧代码继续使用 app.pipelines.constants。
# 常量的唯一来源仍是 common.py，避免维护两份频段或距离阈值定义。

from app.pipelines.common import (
    BAND_3DMIMO,
    COVERAGE_LAYER_MAP,
    DISTANCE_INDOOR_M,
    DISTANCE_MACRO_M,
    FILE_PATTERNS,
    LTE_BAND_MAPPING,
    LTE_BANDS,
    LTE_FREQ_MAPPING,
    NR_BANDS,
    NR_FREQ_MAPPING,
    PHYSICAL_FILE_PATTERNS,
)

__all__ = [
    "BAND_3DMIMO",
    "COVERAGE_LAYER_MAP",
    "DISTANCE_INDOOR_M",
    "DISTANCE_MACRO_M",
    "FILE_PATTERNS",
    "LTE_BAND_MAPPING",
    "LTE_BANDS",
    "LTE_FREQ_MAPPING",
    "NR_BANDS",
    "NR_FREQ_MAPPING",
    "PHYSICAL_FILE_PATTERNS",
]
