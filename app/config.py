"""Path and runtime configuration."""

from pathlib import Path

from app.pipelines.core import (
    BASE_DIR,
    DATA_DIR,
    FILE_PATTERNS,
    LOG_DIR,
    LOWEFF_OUTPUT_PATH,
    PHYSICAL_FILE_PATTERNS,
    UNIFIED_DB_PATH,
)

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 4008
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

__all__ = [
    "BASE_DIR",
    "DATA_DIR",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "FILE_PATTERNS",
    "LOG_DIR",
    "LOWEFF_OUTPUT_PATH",
    "PHYSICAL_FILE_PATTERNS",
    "STATIC_DIR",
    "UNIFIED_DB_PATH",
]
