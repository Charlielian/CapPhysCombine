"""45G 工参数据查询 API（原始小区表 / 物理表汇总）。"""

from __future__ import annotations

from typing import Any

import duckdb
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from app.jsonutil import df_records
from app.pipelines.core import UNIFIED_DB_PATH, get_unified_db_connection

router = APIRouter(prefix="/api/physical-query", tags=["physical-query"])

TABLE_MAP = {
    "raw": "原始小区表",
    "agg": "物理表汇总",
}

RAW_DISPLAY_COLS = [
    "CGI",
    "网络制式",
    "小区名称",
    "物理站",
    "BAND",
    "BAND_A",
    "厂家",
    "站点类型",
    "网元状态",
    "覆盖类型",
    "路测网格",
    "乡镇街道",
    "经度",
    "纬度",
    "方位角",
    "挂高",
    "来源文件",
]

AGG_DISPLAY_COLS = [
    "CGI",
    "网络制式",
    "小区名称",
    "物理站",
    "共站同覆盖名",
    "sectionid",
    "BAND",
    "BAND_A",
    "厂家",
    "站点类型",
    "网元状态",
    "覆盖类型",
    "路测网格",
    "区域",
    "覆盖层",
    "共站制式情况",
    "经度",
    "纬度",
]

FILTER_DIMS = {
    "raw": ["网络制式", "BAND", "厂家", "站点类型", "网元状态", "覆盖类型", "路测网格"],
    "agg": [
        "网络制式",
        "BAND",
        "厂家",
        "站点类型",
        "网元状态",
        "覆盖类型",
        "路测网格",
        "区域",
        "覆盖层",
        "共站制式情况",
    ],
}

SEARCH_COLS = {
    "raw": ["CGI", "小区名称", "物理站", "物理站ID", "天线名", "乡镇街道", "一级标签", "来源文件"],
    "agg": [
        "CGI",
        "小区名称",
        "物理站",
        "物理站ID",
        "共站同覆盖名",
        "天线名",
        "乡镇街道",
        "一级标签",
        "督办网格中文名",
        "物理站名_距离聚合",
    ],
}


def _connect() -> duckdb.DuckDBPyConnection:
    if not UNIFIED_DB_PATH.is_file():
        raise HTTPException(status_code=404, detail="统一数据库不存在，请先运行物理表汇总")
    try:
        return get_unified_db_connection()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"数据库连接失败: {exc}") from exc


def _table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_name = ?
        LIMIT 1
        """,
        [table],
    ).fetchone()
    return row is not None


def _existing_columns(conn: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    df = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = ?
        ORDER BY ordinal_position
        """,
        [table],
    ).fetchdf()
    return [str(c) for c in df["column_name"].tolist()] if not df.empty else []


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _build_where(
    dims: dict[str, str],
    keyword: str,
    search_cols: list[str],
    available: set[str],
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    for col, val in dims.items():
        if not val or col not in available:
            continue
        clauses.append(f"CAST({_quote_ident(col)} AS VARCHAR) = ?")
        params.append(val)

    kw = (keyword or "").strip()
    if kw:
        cols = [c for c in search_cols if c in available]
        if cols:
            like = f"%{kw}%"
            or_parts = [f"CAST({_quote_ident(c)} AS VARCHAR) ILIKE ?" for c in cols]
            clauses.append("(" + " OR ".join(or_parts) + ")")
            params.extend([like] * len(cols))

    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


@router.get("/meta")
def physical_query_meta(source: str = Query("raw", pattern="^(raw|agg)$")):
    """返回可用筛选项与记录规模。"""
    table = TABLE_MAP[source]
    conn = _connect()
    try:
        if not _table_exists(conn, table):
            raise HTTPException(
                status_code=404,
                detail=f"表 {table} 不存在，请先运行物理表汇总",
            )
        available = _existing_columns(conn, table)
        avail_set = set(available)
        total = int(conn.execute(f"SELECT COUNT(*) FROM {_quote_ident(table)}").fetchone()[0])

        filters: dict[str, list[str]] = {}
        for col in FILTER_DIMS[source]:
            if col not in avail_set:
                filters[col] = []
                continue
            rows = conn.execute(
                f"""
                SELECT DISTINCT CAST({_quote_ident(col)} AS VARCHAR) AS v
                FROM {_quote_ident(table)}
                WHERE {_quote_ident(col)} IS NOT NULL
                  AND CAST({_quote_ident(col)} AS VARCHAR) != ''
                ORDER BY 1
                LIMIT 200
                """
            ).fetchall()
            filters[col] = [str(r[0]) for r in rows if r[0] is not None]

        display = RAW_DISPLAY_COLS if source == "raw" else AGG_DISPLAY_COLS
        display_cols = [c for c in display if c in avail_set]
        if not display_cols:
            display_cols = available[:18]

        return {
            "source": source,
            "table": table,
            "total": total,
            "columns": available,
            "display_columns": display_cols,
            "filters": filters,
        }
    finally:
        conn.close()


@router.get("/view")
def physical_query_view(
    source: str = Query("raw", pattern="^(raw|agg)$"),
    keyword: str = Query(""),
    net: str = Query("", description="网络制式"),
    band: str = Query(""),
    vendor: str = Query("", description="厂家"),
    site_type: str = Query("", description="站点类型"),
    status: str = Query("", description="网元状态"),
    cover_type: str = Query("", description="覆盖类型"),
    grid: str = Query("", description="路测网格"),
    region: str = Query("", description="区域（汇总表）"),
    cover_layer: str = Query("", description="覆盖层（汇总表）"),
    co_site: str = Query("", description="共站制式情况（汇总表）"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """分页筛选查询 45G 工参（原始小区 / 物理表汇总）。"""
    table = TABLE_MAP[source]
    conn = _connect()
    try:
        if not _table_exists(conn, table):
            raise HTTPException(
                status_code=404,
                detail=f"表 {table} 不存在，请先运行物理表汇总",
            )

        available = _existing_columns(conn, table)
        avail_set = set(available)

        dims = {
            "网络制式": net,
            "BAND": band,
            "厂家": vendor,
            "站点类型": site_type,
            "网元状态": status,
            "覆盖类型": cover_type,
            "路测网格": grid,
            "区域": region,
            "覆盖层": cover_layer,
            "共站制式情况": co_site,
        }
        where_sql, params = _build_where(
            dims, keyword, SEARCH_COLS[source], avail_set
        )

        count_sql = f"SELECT COUNT(*) FROM {_quote_ident(table)}{where_sql}"
        total = int(conn.execute(count_sql, params).fetchone()[0])

        display = RAW_DISPLAY_COLS if source == "raw" else AGG_DISPLAY_COLS
        select_cols = [c for c in display if c in avail_set]
        if not select_cols:
            select_cols = available[:18]
        select_sql = ", ".join(_quote_ident(c) for c in select_cols)

        order_col = "CGI" if "CGI" in avail_set else select_cols[0]
        data_sql = (
            f"SELECT {select_sql} FROM {_quote_ident(table)}"
            f"{where_sql} ORDER BY {_quote_ident(order_col)}"
            f" LIMIT ? OFFSET ?"
        )
        df: pd.DataFrame = conn.execute(
            data_sql, params + [limit, offset]
        ).fetchdf()

        return {
            "source": source,
            "table": table,
            "total": total,
            "limit": limit,
            "offset": offset,
            "columns": select_cols,
            "records": df_records(df),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
