"""Re-exports for physical table pipeline.

所有符号均来自 core.py 的统一实现，确保 ``from app.pipelines.physical import ...``
与 ``from app.pipelines.core import ...`` 等价。
"""

from app.pipelines.core import (
    PHYSICAL_FILE_PATTERNS,
    PhysicalTableAggregator,
    aggregate_by_distance,
    build_cc_lookup,
    calc_nr_freq,
    cluster_by_distance,
    connect_physical_db,
    init_physical_database,
    merge_cc_and_spatial_fields,
    read_common_coverage,
    read_lte_cellant,
    read_nr_cellant,
    run_physical_table_pipeline,
    union_find_cluster,
)
from app.pipelines.spatial import get_grid_by_coords_batch

__all__ = [
    "PHYSICAL_FILE_PATTERNS",
    "PhysicalTableAggregator",
    "aggregate_by_distance",
    "build_cc_lookup",
    "calc_nr_freq",
    "cluster_by_distance",
    "connect_physical_db",
    "get_grid_by_coords_batch",
    "init_physical_database",
    "merge_cc_and_spatial_fields",
    "read_common_coverage",
    "read_lte_cellant",
    "read_nr_cellant",
    "run_physical_table_pipeline",
    "union_find_cluster",
]
