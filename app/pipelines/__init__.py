"""Pipeline package: domain modules for CapPhysCombine."""

from app.pipelines.capacity import (
    FILE_PATTERNS,
    build_4g_table,
    build_5g_table,
    build_45g_table,
    run_pipeline,
)
from app.pipelines.common import (
    BASE_DIR,
    DATA_DIR,
    LOWEFF_OUTPUT_PATH,
    PHYSICAL_FILE_PATTERNS,
    get_data_file_status,
    list_output_files,
    setup_logging,
)
from app.pipelines.core import (
    run_physical_table_pipeline,
    discover_4g_week_files,
    run_multi_week_loweff,
    MULTIWEEK_OUTPUT_PATH,
)
from app.pipelines.loweff import build_low_efficiency_table, build_station_band_evaluation, run_low_efficiency_pipeline
from app.pipelines.nrm_sync import run_nrm_sync_pipeline
from app.pipelines.sector import (
    detect_sector_conflicts,
    run_physical_table_sector_fix,
    suggest_sector_fixes,
)
from app.pipelines.zero_low_flow import (
    run_4g_zero_low_flow_pipeline,
    run_5g_zero_low_flow_pipeline,
    run_zero_low_flow_pipeline,
)

__all__ = [
    "BASE_DIR",
    "DATA_DIR",
    "FILE_PATTERNS",
    "LOWEFF_OUTPUT_PATH",
    "MULTIWEEK_OUTPUT_PATH",
    "PHYSICAL_FILE_PATTERNS",
    "build_45g_table",
    "build_4g_table",
    "build_5g_table",
    "build_low_efficiency_table",
    "build_station_band_evaluation",
    "detect_sector_conflicts",
    "discover_4g_week_files",
    "get_data_file_status",
    "list_output_files",
    "run_4g_zero_low_flow_pipeline",
    "run_5g_zero_low_flow_pipeline",
    "run_low_efficiency_pipeline",
    "run_multi_week_loweff",
    "run_physical_table_pipeline",
    "run_nrm_sync_pipeline",
    "run_physical_table_sector_fix",
    "run_pipeline",
    "run_zero_low_flow_pipeline",
    "setup_logging",
    "suggest_sector_fixes",
]
