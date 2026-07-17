"""JSON helpers for pandas payloads."""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd


def json_safe_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    # pandas / numpy scalars
    if hasattr(value, "item") and callable(value.item):
        try:
            return json_safe_value(value.item())
        except (ValueError, AttributeError):
            pass
    return value


def df_records(df: pd.DataFrame | None, limit: int | None = None) -> list[dict]:
    """Convert DataFrame to JSON-safe list of dicts (NaN/NaT/Inf -> null)."""
    if df is None or df.empty:
        return []
    view = df if limit is None else df.head(limit)
    # pandas to_json already emits null for NaN
    return json.loads(
        view.to_json(orient="records", date_format="iso", force_ascii=False)
    )
