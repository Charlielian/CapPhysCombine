"""容量表合成结果查询 API（持久化在统一数据库）。"""

# API 说明：
# 容量结果查询只读统一数据库中的三张结果表，列表接口负责分页和关键词筛选，
# 不重新运行流水线；这样页面刷新不会重复执行昂贵的 Excel 导入。

from __future__ import annotations

from fastapi import APIRouter, Query

from app.jsonutil import df_records
from app.pipelines.cog_db import CapacityResultManager

router = APIRouter(prefix="/api/capacity-results", tags=["capacity-results"])

# 容量表名映射 -> 中文显示名
TABLE_LABELS = {
    "合成_5G容量表": "5G 容量表",
    "合成_4G容量表": "4G 容量表",
    "合成_45G容量表": "45G 容量表",
}

ALL_TABLES = list(TABLE_LABELS.keys())


@router.get("/summary")
def summary():
    """返回三张容量表各自的行数。"""
    with CapacityResultManager() as mgr:
        data = mgr.get_summary()
    result = []
    for tbl in ALL_TABLES:
        result.append({
            "table": tbl,
            "label": TABLE_LABELS[tbl],
            "row_count": data.get(tbl, 0),
        })
    return {"tables": result}


@router.get("/view")
def view(
    table: str = Query("合成_5G容量表", description="表名"),
    keyword: str = Query("", description="搜索关键字"),
    keyword_columns: str = Query("", description="搜索列（逗号分隔）"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """分页查询指定容量表。"""
    if table not in ALL_TABLES:
        table = ALL_TABLES[0]

    with CapacityResultManager() as mgr:
        columns = mgr.list_columns(table)
        kw_cols = None
        if keyword_columns:
            kw_cols = [c.strip() for c in keyword_columns.split(",") if c.strip()]
        elif keyword:
            # 默认在常见字段中搜索
            kw_cols = [c for c in columns if any(
                k in c for k in ["CGI", "NCGI", "名称", "地市", "物理站", "扇区"]
            )]
        df, total = mgr.query(
            table,
            keyword=keyword,
            keyword_columns=kw_cols,
            limit=limit,
            offset=offset,
        )

    return {
        "table": table,
        "label": TABLE_LABELS.get(table, table),
        "columns": columns,
        "records": df_records(df),
        "total": total,
    }


__all__ = ["router"]
