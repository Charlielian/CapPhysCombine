"""DuckDB Spatial  point-in-polygon 批量查询（替代 GeoPandas + Shapely）。

使用 ``st_read`` 加载 GeoJSON，通过 ``ST_Contains`` 完成空间关联，
全程在 SQL 内完成，性能优于逐点 Python 循环。
"""

from __future__ import annotations

import os

import duckdb
import pandas as pd


def _ensure_spatial(con: duckdb.DuckDBPyConnection) -> None:
    """确保 spatial 扩展已加载（幂等）。"""
    try:
        con.load_extension("spatial")
    except duckdb.IOException:
        con.install_extension("spatial")
        con.load_extension("spatial")


def get_grid_by_coords_batch(
    geojson_path: str | os.PathLike,
    lons,
    lats,
    *,
    con: duckdb.DuckDBPyConnection | None = None,
) -> list[dict | None]:
    """批量根据经纬度做点在多边形内查询，返回每点匹配的属性字典或 None。

    与原 GeoPandas 版接口兼容：返回长度等于输入点数的 list，每个元素
    为 GeoJSON 层的属性 dict（去掉 geometry 列），未命中或坐标无效时为 None。

    Parameters
    ----------
    geojson_path : GeoJSON 文件路径。
    lons, lats : 经纬度序列（list / array / Series），长度须相等。
    con : 可选的 DuckDB 连接。不传则内部创建临时连接并关闭。
    """
    if not os.path.exists(geojson_path):
        return [None] * len(lons)

    n = len(lons)

    own_conn = con is None
    if own_conn:
        con = duckdb.connect()
        _ensure_spatial(con)

    try:
        # ---------- 1. 加载 GeoJSON 层 ----------
        con.execute(
            "CREATE OR REPLACE TEMP TABLE _layer AS SELECT * FROM st_read(?)",
            [str(geojson_path)],
        )

        # ---------- 2. 过滤无效坐标并构建点表 ----------
        indices: list[int] = []
        flat_lons: list[float] = []
        flat_lats: list[float] = []

        for i in range(n):
            lon, lat = lons[i], lats[i]
            if pd.isna(lon) or pd.isna(lat):
                continue
            if lon == 0 or lat == 0 or str(lon) == "***":
                continue
            try:
                flat_lons.append(float(lon))
                flat_lats.append(float(lat))
                indices.append(i)
            except (ValueError, TypeError):
                continue

        results: list[dict | None] = [None] * n

        if not indices:
            return results

        pts = pd.DataFrame({
            "_orig_idx": indices,
            "lon": flat_lons,
            "lat": flat_lats,
        })
        con.register("_pts_df", pts)
        con.execute("""
            CREATE OR REPLACE TEMP TABLE _pts AS
            SELECT
                _orig_idx,
                st_point(lon, lat) AS geom
            FROM _pts_df
        """)
        con.unregister("_pts_df")

        # ---------- 3. 空间关联 ----------
        # 取 layer 所有非 geometry 列，用 ROW_NUMBER 去重（边界多匹配时取第一个）
        layer_cols = [
            c for c in con.execute("DESCRIBE _layer").fetchdf()["column_name"]
            if c != "geom"
        ]
        select_layer = ", ".join(f"l.\"{c}\"" for c in layer_cols)

        matched = con.execute(f"""
            WITH joined AS (
                SELECT
                    p._orig_idx,
                    {select_layer},
                    ROW_NUMBER() OVER (PARTITION BY p._orig_idx ORDER BY p._orig_idx) AS _rn
                FROM _pts p
                JOIN _layer l ON st_contains(l.geom, p.geom)
            )
            SELECT * FROM joined WHERE _rn = 1
        """).fetchdf()

        drop = {"_orig_idx", "_rn"}
        for _, row in matched.iterrows():
            orig = int(row["_orig_idx"])
            results[orig] = {
                k: v for k, v in row.to_dict().items() if k not in drop
            }

        return results

    finally:
        # 清理临时表
        for tbl in ("_layer", "_pts"):
            try:
                con.execute(f"DROP TABLE IF EXISTS {tbl}")
            except Exception:
                pass
        if own_conn:
            con.close()
