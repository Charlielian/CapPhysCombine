"""容量表合成流水线。"""

from __future__ import annotations

import time
from pathlib import Path

import duckdb
import pandas as pd

from app.pipelines.cog_db import CapacityResultManager, CogCoverageManager
from app.pipelines.common import (
    DB_PATH,
    FILE_PATTERNS,
    LARGE_TABLES,
    LOWEFF_OUTPUT_PATH,
    GuiLogger,
    GuiProgress,
    LogCallback,
    ProgressCallback,
    build_output_paths,
    db_to_dataframe,
    first_existing,
    get_db_connection,
    get_logger,
    get_table_columns,
    init_db,
    load_small_table,
    pick_matching_files,
    resolve_output_timestamp,
)


def import_cog_coverage_to_db(
    conn: duckdb.DuckDBPyConnection,
    logger: GuiLogger | None = None
) -> bool:
    """导入共站同覆盖小区表到数据库 - 现在使用统一数据库
    
    注意: 此函数保留以保持兼容性，实际上数据已保存在统一数据库中
    用户可以通过GUI管理共站同覆盖表
    """
    logger = logger or GuiLogger()
    logger.log("共站同覆盖表已从统一数据库加载")
    return True


def load_cog_coverage_mapping(
    conn: duckdb.DuckDBPyConnection,
    logger: GuiLogger | None = None
) -> pd.DataFrame:
    """从统一数据库加载共站同覆盖小区表，返回 CGI -> 共站同覆盖名 的映射表
    
    现在使用统一数据库的共站同覆盖表，两个功能共享同一个数据源
    """
    logger = logger or GuiLogger()

    # 使用统一数据库的共站同覆盖表
    try:
        with CogCoverageManager() as mgr:
            count = mgr.get_count()
            if count == 0:
                logger.log("统一数据库中共站同覆盖表为空，请通过管理界面导入")
                return pd.DataFrame(columns=["CGI", "共站同覆盖名"])

            logger.log(f"从统一数据库加载共站同覆盖映射表（{count} 条记录）")

            # 获取所有数据
            df = mgr.get_all()
            if df.empty:
                return pd.DataFrame(columns=["CGI", "共站同覆盖名"])

            # 选择需要的列
            required_cols = ["CGI", "共站同覆盖名"]
            extra_cols = ["路测网格", "乡镇街道", "是否覆盖层", "小区所属区域", "覆盖层"]

            available_cols = [c for c in required_cols + extra_cols if c in df.columns]
            mapping = df[available_cols].copy()

            # 确保CGI为字符串类型
            mapping["CGI"] = mapping["CGI"].astype(str)

            # 去重（保留第一个）
            mapping = mapping.drop_duplicates(subset=["CGI"], keep="first")

            extra_loaded = [c for c in extra_cols if c in mapping.columns]
            logger.log(f"共站同覆盖映射表加载完成，共 {len(mapping)} 条映射，额外字段: {extra_loaded}")
            return mapping

    except Exception as e:
        logger.log(f"从统一数据库加载共站同覆盖表失败: {e}")
        return pd.DataFrame(columns=["CGI", "共站同覆盖名"])



def _import_one_file_type(
    name: str,
    files: list[Path],
    is_large: bool,
    db_path_str: str,
    log_callback: LogCallback | None,
) -> tuple[str, int, int, float, str | None]:
    """工作线程：导入单个文件类型到独立 DuckDB 连接。

    每个线程持有自己的连接，DuckDB 自动序列化写入，但 Excel 解析并行执行。
    返回 (name, total_rows, col_count, elapsed, error)。
    """
    import duckdb as _duckdb

    from app.pipelines.io import excel_to_db as _excel_to_db
    from app.pipelines.io import get_table_columns as _get_table_columns
    from app.pipelines.io import small_excel_to_db as _small_excel_to_db
    from app.pipelines.logging_util import GuiLogger as _GuiLogger

    logger = _GuiLogger(callback=log_callback, component=f"导入-{name}")
    thread_conn = _duckdb.connect(db_path_str)
    try:
        start = time.perf_counter()
        total_rows = 0
        for i, path in enumerate(files):
            append = i > 0
            if is_large:
                rows = _excel_to_db(path, name, thread_conn, logger, append=append)
            else:
                rows = _small_excel_to_db(path, name, thread_conn, logger, append=append)
            total_rows += rows
        col_count = len(_get_table_columns(thread_conn, name))
        elapsed = time.perf_counter() - start
        return name, total_rows, col_count, elapsed, None
    except Exception as exc:
        return name, 0, 0, 0.0, str(exc)
    finally:
        thread_conn.close()


def load_sources_to_db(conn: duckdb.DuckDBPyConnection, logger: GuiLogger | None = None) -> dict[str, list[Path]]:
    """将源数据文件导入数据库（ThreadPoolExecutor 并行导入不同文件类型）。

    硬约束：不同文件类型必须并行导入。
    实现：每个文件类型一个工作线程，每个线程持有独立 DuckDB 连接；
    DuckDB 自动序列化写入，Excel 解析（CPU 密集部分）并行执行。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    logger = logger or GuiLogger()

    selected: dict[str, list[Path]] = {}

    for name, pattern in FILE_PATTERNS.items():
        files = pick_matching_files(pattern)
        if files:
            selected[name] = files

    logger.log("使用以下源文件：")
    for name, files in selected.items():
        if len(files) == 1:
            logger.log(f"- {name}: {files[0].name}")
        else:
            logger.log(f"- {name}: {len(files)} 个文件")
            for f in files:
                logger.log(f"    - {f.name}")

    logger.log(f"开始并行导入数据（{len(selected)} 个文件类型）...")
    start_total = time.perf_counter()

    db_path_str = str(DB_PATH)
    max_workers = min(len(selected) if selected else 1, 8)

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="import") as executor:
        futures = {
            executor.submit(
                _import_one_file_type,
                name,
                files,
                name in LARGE_TABLES,
                db_path_str,
                logger.callback,
            ): name
            for name, files in selected.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                _, rows, cols, elapsed, error = future.result()
                if error:
                    logger.log(f"导入失败 [{name}]: {error}")
                else:
                    logger.log(
                        f"导入完成 [{name}]: {rows} 行 x {cols} 列 (耗时 {elapsed:.1f}s)"
                    )
            except Exception as e:
                logger.log(f"导入异常 [{name}]: {e}")

    # 导入共站同覆盖表（如果存在）—— 串行，因为依赖统一数据库
    import_cog_coverage_to_db(conn, logger)

    elapsed_total = time.perf_counter() - start_total
    logger.log(f"所有数据导入完成，总耗时 {elapsed_total:.1f}s")

    return selected



def apply_sector_mapping(table: pd.DataFrame, mapping: pd.DataFrame, cgi_column: str, logger: GuiLogger | None = None) -> pd.DataFrame:
    """用共站同覆盖映射表更新容量表的扇区字段"""
    logger = logger or GuiLogger()

    if mapping.empty or cgi_column not in table.columns:
        logger.log(f"跳过扇区更新（CGI列: {cgi_column}）")
        return table

    table = table.copy()
    table[cgi_column] = table[cgi_column].astype(str)

    # 需要映射的字段列表
    mapping_cols = ["共站同覆盖名", "路测网格", "乡镇街道", "是否覆盖层", "小区所属区域"]
    mapping_cols = [col for col in mapping_cols if col in mapping.columns]

    # 遍历映射每个字段
    # 修复：原实现 table[target_col] = table[cgi_column].map(col_mapping)
    # 会用 NaN 覆盖映射未命中的行（包括原本已有值的行）。
    # 改为 combine_first：仅当映射命中时覆盖，未命中保留原值。
    for col in mapping_cols:
        col_mapping = dict(zip(mapping["CGI"], mapping[col]))
        target_col = "扇区" if col == "共站同覆盖名" else col
        if target_col not in table.columns:
            table[target_col] = pd.NA

        original_count = table[target_col].notna().sum()
        mapped = table[cgi_column].map(col_mapping)
        # 仅用映射命中（mapped.notna()）的值覆盖原列
        table[target_col] = mapped.where(mapped.notna(), table[target_col])
        updated_count = table[target_col].notna().sum()
        newly_matched = int(mapped.notna().sum())
        logger.log(
            f"字段 [{target_col}] 更新完成: 映射命中 {newly_matched} 条，"
            f"非空总数 {original_count} -> {updated_count}"
        )

    return table


def build_5g_table(conn: duckdb.DuckDBPyConnection, logger: GuiLogger | None = None) -> pd.DataFrame:
    logger = logger or GuiLogger()

    logger.log("  [5G] 在数据库中进行日表聚合...")

    day_cols = get_table_columns(conn, "5g_day")
    util_col_5g = "忙时小区PRB利用率" if "忙时小区PRB利用率" in day_cols else "忙时小区PRB利用率(%)"

    conn.execute("DROP TABLE IF EXISTS _5g_day_agg")
    conn.execute("DROP TABLE IF EXISTS _5g_day_weekday")
    conn.execute("DROP TABLE IF EXISTS _5g_day_weekend")

    conn.execute(f"""
        CREATE TABLE _5g_day_agg AS
        SELECT
            CAST(NCGI AS VARCHAR) AS NCGI,
            AVG("{util_col_5g}") AS 自忙时利用率,
            AVG("日RLC层上下行总流量(G)") AS 日均流量,
            AVG("忙时上行PRB平均利用率(%)") AS 自忙时上行PRB平均利用率,
            AVG("忙时下行PRB平均利用率(%)") AS 自忙时下行PRB平均利用率,
            AVG("忙时PDCCH信道CCE占用率(%)") AS 自忙时PDCCH信道CCE占用率,
            AVG("RRC连接最大数-忙时") AS 自忙时RRC连接最大数,
            AVG("RRC连接平均数-忙时") AS 自忙时有效RRC连接平均数,
            AVG("忙时RLC层上行业务字节数(G)") AS 自忙时上行流量,
            AVG("忙时RLC层下行业务字节数(G)") AS 自忙时下行流量,
            AVG("忙时RLC层上行业务字节数(G)") + AVG("忙时RLC层下行业务字节数(G)") AS 自忙时总流量,
            AVG("RRC连接最大数-忙时") AS 自忙时有效RRC连接最大数
        FROM "5g_day"
        GROUP BY CAST(NCGI AS VARCHAR)
    """)

    conn.execute(f"""
        CREATE TABLE _5g_day_weekday AS
        SELECT
            CAST(NCGI AS VARCHAR) AS NCGI,
            AVG("{util_col_5g}") AS 工作日自忙时利用率,
            AVG("日RLC层上下行总流量(G)") AS 工作日日均流量,
            AVG("RRC连接最大数-忙时") AS 工作日自忙时RRC连接最大数
        FROM "5g_day"
        WHERE CAST(extract(dayofweek FROM CAST("记录开始时间" AS TIMESTAMP)) AS INTEGER) IN (2, 3, 4, 5, 6)
        GROUP BY CAST(NCGI AS VARCHAR)
    """)

    conn.execute(f"""
        CREATE TABLE _5g_day_weekend AS
        SELECT
            CAST(NCGI AS VARCHAR) AS NCGI,
            AVG("{util_col_5g}") AS 周末自忙时利用率,
            AVG("日RLC层上下行总流量(G)") AS 周末日均流量,
            AVG("RRC连接最大数-忙时") AS 周末自忙时RRC连接最大数
        FROM "5g_day"
        WHERE CAST(extract(dayofweek FROM CAST("记录开始时间" AS TIMESTAMP)) AS INTEGER) IN (1, 7)
        GROUP BY CAST(NCGI AS VARCHAR)
    """)

    conn.execute("DROP TABLE IF EXISTS _5g_day_zero_stats")
    conn.execute("""
        CREATE TABLE _5g_day_zero_stats AS
        WITH day_raw AS (
            SELECT
                CAST(NCGI AS VARCHAR) AS NCGI,
                CAST("记录开始时间" AS TIMESTAMP) AS 记录开始时间,
                COALESCE("日RLC层上下行总流量(G)", 0) AS 日流量
            FROM "5g_day"
        ),
        zero_stats AS (
            SELECT
                NCGI,
                SUM(CASE WHEN CAST(extract(dayofweek FROM 记录开始时间) AS INTEGER) IN (2, 3, 4, 5, 6)
                         AND 日流量 = 0 THEN 1 ELSE 0 END) AS 工作日零流量天数,
                SUM(CASE WHEN CAST(extract(dayofweek FROM 记录开始时间) AS INTEGER) IN (1, 7)
                         AND 日流量 = 0 THEN 1 ELSE 0 END) AS 周末零流量天数
            FROM day_raw
            GROUP BY NCGI
        ),
        top3 AS (
            SELECT
                NCGI,
                AVG(日流量) AS 最大3天流量均值
            FROM (
                SELECT
                    NCGI,
                    日流量,
                    ROW_NUMBER() OVER (PARTITION BY NCGI ORDER BY 日流量 DESC) AS rn
                FROM day_raw
            ) ranked
            WHERE rn <= 3
            GROUP BY NCGI
        )
        SELECT
            z.NCGI,
            z.工作日零流量天数,
            z.周末零流量天数,
            t.最大3天流量均值
        FROM zero_stats z
        LEFT JOIN top3 t ON z.NCGI = t.NCGI
    """)

    logger.log("  [5G] 在数据库中进行 MR 表聚合...")
    conn.execute("DROP TABLE IF EXISTS _5g_mr_agg")
    conn.execute("""
        CREATE TABLE _5g_mr_agg AS
        SELECT
            CAST("小区NCGI" AS VARCHAR) AS NCGI,
            AVG("移动RSRP采样的总采样点") AS MRO移动总采样点,
            SUM("移动RSRP采样强于-110采样点") AS MRO强于110采样点合计,
            SUM("移动RSRP采样的总采样点") AS MRO总采样点合计,
            AVG("移动平均TA(M)") AS 平均TA米,
            CASE 
                WHEN SUM("移动RSRP采样的总采样点") = 0 OR SUM("移动RSRP采样的总采样点") IS NULL THEN NULL 
                ELSE SUM("移动RSRP采样强于-110采样点") * 1.0 / SUM("移动RSRP采样的总采样点") 
            END AS MRO移动覆盖率
        FROM "5g_mr"
        GROUP BY CAST("小区NCGI" AS VARCHAR)
    """)

    logger.log("  [5G] 在数据库中进行 KPI 表聚合...")
    conn.execute("DROP TABLE IF EXISTS _5g_kpi_agg")
    conn.execute("""
        CREATE TABLE _5g_kpi_agg AS
        SELECT
            CAST(NCGI AS VARCHAR) AS NCGI,
            AVG("VoNR语音话务量") AS VoNR语音话务量
        FROM "5g_kpi"
        GROUP BY CAST(NCGI AS VARCHAR)
    """)

    logger.log("  [5G] 加载周表并去重...")
    week_df = load_small_table(conn, "5g_week")
    if week_df.empty:
        return pd.DataFrame()

    week_df["NCGI"] = week_df["NCGI"].astype(str)
    week_unique = week_df.drop_duplicates(subset=["NCGI"]).copy()

    logger.log("  [5G] 读取聚合结果并合并...")
    day_agg = db_to_dataframe("SELECT * FROM _5g_day_agg", conn)
    weekday_agg = db_to_dataframe("SELECT * FROM _5g_day_weekday", conn)
    weekend_agg = db_to_dataframe("SELECT * FROM _5g_day_weekend", conn)
    zero_stats = db_to_dataframe("SELECT * FROM _5g_day_zero_stats", conn)
    mr_agg = db_to_dataframe("SELECT * FROM _5g_mr_agg", conn)
    kpi_agg = db_to_dataframe("SELECT * FROM _5g_kpi_agg", conn)

    result = week_unique.merge(day_agg, on="NCGI", how="left")
    result = result.merge(weekday_agg, on="NCGI", how="left")
    result = result.merge(weekend_agg, on="NCGI", how="left")
    result = result.merge(zero_stats, on="NCGI", how="left")
    result = result.merge(mr_agg, on="NCGI", how="left")
    result = result.merge(kpi_agg, on="NCGI", how="left")

    logger.log("  [5G] 计算流量系数、长尾分类等派生字段...")

    avg_traffic = result["日均流量"].mean(skipna=True)
    result["流量系数"] = result["日均流量"] / avg_traffic if pd.notna(avg_traffic) and avg_traffic != 0 else pd.NA
    tail_threshold = result["日均流量"].quantile(0.3)
    result["流量排名升序"] = result["日均流量"].rank(method="min", ascending=True)

    is_na_traffic = result["日均流量"].isna()
    is_tail = (result["日均流量"] <= tail_threshold) & ~is_na_traffic
    is_zero = result["日均流量"] == 0
    is_high_util = result["自忙时利用率"].notna() & (result["自忙时利用率"] > 20)

    result["长尾小区"] = pd.NA
    result.loc[is_tail & is_zero, "长尾小区"] = "长尾具体原因待确认"
    result.loc[is_tail & ~is_zero & is_high_util, "长尾小区"] = "长尾待观察"
    result.loc[is_tail & ~is_zero & ~is_high_util, "长尾小区"] = "长尾需处理"

    result["流量是否正常"] = pd.NA
    result.loc[result["流量系数"] < 0.2, "流量是否正常"] = "低流量系数小区"
    result.loc[(result["流量系数"] >= 0.2) & (result["流量系数"] < 3), "流量是否正常"] = "正常"
    result.loc[result["流量系数"] >= 3, "流量是否正常"] = "高流量系数小区"

    result["负荷情况"] = pd.NA
    result.loc[result["自忙时利用率"].notna() & (result["自忙时利用率"] > 80), "负荷情况"] = "负荷高小区"
    result.loc[result["自忙时利用率"].isna() | (result["自忙时利用率"] <= 80), "负荷情况"] = "正常"

    result["记录开始时间"] = first_existing(result, ["记录开始时间"])
    result["记录结束时间"] = first_existing(result, ["记录结束时间"])
    result["地市"] = first_existing(result, ["地市"])
    result["网元状态"] = first_existing(result, ["网元状态"])
    result["小区名称"] = first_existing(result, ["小区名称"])
    result["band"] = first_existing(result, ["使用频段"])
    result["覆盖类型"] = first_existing(result, ["覆盖类型"])
    result["场景 V容量表"] = first_existing(result, ["场景1", "一级场景"])
    result["TYPE"] = pd.NA
    result["是否全省高负荷预警小区（集团口径）"] = pd.NA
    result["是否高负荷待扩容小区"] = first_existing(result, ["是否高负荷待扩容小区", "是否高负荷"])
    result["是否全省高负荷预警小区（省内口径）"] = pd.NA
    result["物理站"] = first_existing(result, ["站点名称"])

    ordered_columns = [
        "记录开始时间", "记录结束时间", "地市", "NCGI", "网元状态", "小区名称", "扇区", "band",
        "覆盖类型", "场景 V容量表", "TYPE", "流量是否正常", "负荷情况", "流量排名升序", "长尾小区",
        "自忙时利用率", "日均流量", "VoNR语音话务量", "MRO移动总采样点", "MRO移动覆盖率", "平均TA米",
        "工作日自忙时利用率", "工作日日均流量", "工作日自忙时RRC连接最大数",
        "工作日零流量天数", "周末零流量天数", "最大3天流量均值",
        "周末自忙时利用率", "周末日均流量", "周末自忙时RRC连接最大数",
        "自忙时上行PRB平均利用率", "自忙时下行PRB平均利用率", "自忙时PDCCH信道CCE占用率",
        "自忙时有效RRC连接最大数", "自忙时RRC连接最大数", "自忙时有效RRC连接平均数",
        "自忙时总流量", "自忙时上行流量", "自忙时下行流量",
        "是否全省高负荷预警小区（集团口径）", "是否高负荷待扩容小区", "是否全省高负荷预警小区（省内口径）",
        "流量系数", "物理站",
    ]
    return result.reindex(columns=ordered_columns)


def build_4g_table(conn: duckdb.DuckDBPyConnection, logger: GuiLogger | None = None) -> pd.DataFrame:
    logger = logger or GuiLogger()

    logger.log("  [4G] 在数据库中进行日表聚合...")

    conn.execute("DROP TABLE IF EXISTS _4g_day_temp")
    conn.execute("DROP TABLE IF EXISTS _4g_day_agg")
    conn.execute("DROP TABLE IF EXISTS _4g_day_weekday")
    conn.execute("DROP TABLE IF EXISTS _4g_day_weekend")

    conn.execute("""
        CREATE TABLE _4g_day_temp AS
        SELECT
            CAST(CGI AS VARCHAR) AS CGI,
            CASE WHEN "自忙时上行PRB平均利用率" > "自忙时下行PRB平均利用率" THEN "自忙时上行PRB平均利用率" ELSE "自忙时下行PRB平均利用率" END AS 自忙时利用率,
            "日4G流量（GB）",
            "自忙时上行PRB平均利用率",
            "自忙时下行PRB平均利用率",
            "自忙时PDCCH信道CCE占用率",
            "自忙时有效RRC连接最大数",
            "自忙时RRC连接最大数",
            "自忙时有效RRC连接平均数",
            "自忙时空口上行业务字节数",
            "自忙时空口下行业务字节数",
            "记录开始时间"
        FROM "4g_day"
    """)

    conn.execute("""
        CREATE TABLE _4g_day_agg AS
        SELECT
            CGI,
            AVG(自忙时利用率) AS 自忙时利用率,
            AVG("日4G流量（GB）") AS 日均流量,
            AVG("自忙时上行PRB平均利用率") AS 自忙时上行PRB平均利用率,
            AVG("自忙时下行PRB平均利用率") AS 自忙时下行PRB平均利用率,
            AVG("自忙时PDCCH信道CCE占用率") AS 自忙时PDCCH信道CCE占用率,
            AVG("自忙时有效RRC连接最大数") AS 自忙时有效RRC连接最大数,
            AVG("自忙时RRC连接最大数") AS 自忙时RRC连接最大数,
            AVG("自忙时有效RRC连接平均数") AS 自忙时有效RRC连接平均数,
            AVG("自忙时空口上行业务字节数") AS 自忙时上行流量,
            AVG("自忙时空口下行业务字节数") AS 自忙时下行流量,
            AVG("自忙时空口上行业务字节数") + AVG("自忙时空口下行业务字节数") AS 自忙时总流量
        FROM _4g_day_temp
        GROUP BY CGI
    """)

    conn.execute("""
        CREATE TABLE _4g_day_weekday AS
        SELECT
            CGI,
            AVG(自忙时利用率) AS 工作日自忙时利用率,
            AVG("日4G流量（GB）") AS 工作日日均流量,
            AVG("自忙时RRC连接最大数") AS 工作日自忙时RRC连接最大数
        FROM _4g_day_temp
        WHERE CAST(extract(dayofweek FROM CAST("记录开始时间" AS TIMESTAMP)) AS INTEGER) IN (2, 3, 4, 5, 6)
        GROUP BY CGI
    """)

    conn.execute("""
        CREATE TABLE _4g_day_weekend AS
        SELECT
            CGI,
            AVG(自忙时利用率) AS 周末自忙时利用率,
            AVG("日4G流量（GB）") AS 周末日均流量,
            AVG("自忙时RRC连接最大数") AS 周末自忙时RRC连接最大数
        FROM _4g_day_temp
        WHERE CAST(extract(dayofweek FROM CAST("记录开始时间" AS TIMESTAMP)) AS INTEGER) IN (1, 7)
        GROUP BY CGI
    """)

    logger.log("  [4G] 在数据库中进行零流量统计...")
    conn.execute("DROP TABLE IF EXISTS _4g_day_zero_stats")
    conn.execute("""
        CREATE TABLE _4g_day_zero_stats AS
        WITH day_raw AS (
            SELECT
                CGI,
                CAST("记录开始时间" AS TIMESTAMP) AS 记录开始时间,
                COALESCE("日4G流量（GB）", 0) AS 日流量
            FROM _4g_day_temp
        ),
        zero_stats AS (
            SELECT
                CGI,
                SUM(CASE WHEN CAST(extract(dayofweek FROM 记录开始时间) AS INTEGER) IN (2, 3, 4, 5, 6)
                         AND 日流量 = 0 THEN 1 ELSE 0 END) AS 工作日零流量天数,
                SUM(CASE WHEN CAST(extract(dayofweek FROM 记录开始时间) AS INTEGER) IN (1, 7)
                         AND 日流量 = 0 THEN 1 ELSE 0 END) AS 周末零流量天数
            FROM day_raw
            GROUP BY CGI
        ),
        top3 AS (
            SELECT
                CGI,
                AVG(日流量) AS 最大3天流量均值
            FROM (
                SELECT
                    CGI,
                    日流量,
                    ROW_NUMBER() OVER (PARTITION BY CGI ORDER BY 日流量 DESC) AS rn
                FROM day_raw
            ) ranked
            WHERE rn <= 3
            GROUP BY CGI
        )
        SELECT
            z.CGI,
            z.工作日零流量天数,
            z.周末零流量天数,
            t.最大3天流量均值
        FROM zero_stats z
        LEFT JOIN top3 t ON z.CGI = t.CGI
    """)

    logger.log("  [4G] 在数据库中进行 MR 表聚合...")
    conn.execute("DROP TABLE IF EXISTS _4g_mr_agg")
    conn.execute("""
        CREATE TABLE _4g_mr_agg AS
        SELECT
            CAST(cgi AS VARCHAR) AS CGI,
            AVG("MRO移动总采样点") AS MRO移动总采样点,
            SUM("MRO移动大于等于负110DBM的采样点数") AS MRO有效点合计,
            SUM("MRO移动总采样点") AS MRO总采样点合计,
            AVG("平均TA") AS 平均TA米,
            CASE 
                WHEN SUM("MRO移动总采样点") = 0 OR SUM("MRO移动总采样点") IS NULL THEN NULL 
                ELSE SUM("MRO移动大于等于负110DBM的采样点数") * 1.0 / SUM("MRO移动总采样点") 
            END AS MRO移动覆盖率
        FROM "4g_mr"
        GROUP BY CAST(cgi AS VARCHAR)
    """)

    logger.log("  [4G] 在数据库中进行周表指标聚合...")
    conn.execute("DROP TABLE IF EXISTS _4g_week_metrics")
    conn.execute("""
        CREATE TABLE _4g_week_metrics AS
        SELECT
            CAST(CGI AS VARCHAR) AS CGI,
            AVG("自忙时上行PRB平均利用率") AS week_上行PRB,
            AVG("自忙时下行PRB平均利用率") AS week_下行PRB,
            AVG("自忙时PDCCH信道CCE占用率") AS week_PDCCH
        FROM "4g_week"
        GROUP BY CAST(CGI AS VARCHAR)
    """)

    logger.log("  [4G] 加载周表并去重...")
    week_df = load_small_table(conn, "4g_week")
    if week_df.empty:
        return pd.DataFrame()

    week_df["CGI"] = week_df["CGI"].astype(str)
    week_unique = week_df.drop_duplicates(subset=["CGI"]).copy()

    logger.log("  [4G] 读取聚合结果并合并...")
    day_agg = db_to_dataframe("SELECT * FROM _4g_day_agg", conn)
    weekday_agg = db_to_dataframe("SELECT * FROM _4g_day_weekday", conn)
    weekend_agg = db_to_dataframe("SELECT * FROM _4g_day_weekend", conn)
    mr_agg = db_to_dataframe("SELECT * FROM _4g_mr_agg", conn)
    week_metrics = db_to_dataframe("SELECT * FROM _4g_week_metrics", conn)
    zero_stats = db_to_dataframe("SELECT * FROM _4g_day_zero_stats", conn)

    # Drop columns from week_unique that also exist in day_agg to prevent
    # pandas from creating _x/_y suffixed duplicates during the merge.
    _overlap = [c for c in day_agg.columns if c != "CGI" and c in week_unique.columns]
    if _overlap:
        week_unique = week_unique.drop(columns=_overlap)

    result = week_unique.merge(day_agg, on="CGI", how="left")
    result = result.merge(week_metrics, on="CGI", how="left")
    result = result.merge(weekday_agg, on="CGI", how="left")
    result = result.merge(weekend_agg, on="CGI", how="left")
    result = result.merge(mr_agg, on="CGI", how="left")
    result = result.merge(zero_stats, on="CGI", how="left")

    logger.log("  [4G] 计算流量系数、长尾分类等派生字段...")

    avg_traffic = result["日均流量"].mean(skipna=True)
    result["流量系数"] = result["日均流量"] / avg_traffic if pd.notna(avg_traffic) and avg_traffic != 0 else pd.NA
    result["流量排名升序"] = result["日均流量"].rank(method="min", ascending=True)
    tail_threshold = result["日均流量"].quantile(0.3)

    result["流量是否正常"] = pd.NA
    result.loc[result["流量系数"] < 0.2, "流量是否正常"] = "低流量系数小区"
    result.loc[(result["流量系数"] >= 0.2) & (result["流量系数"] < 3), "流量是否正常"] = "正常"
    result.loc[result["流量系数"] >= 3, "流量是否正常"] = "高流量系数小区"

    name = result["小区名称"].fillna("").astype(str)
    is_rdc_dc = name.str.contains("RDC|DC-|RGS|GS-", regex=True, na=False)
    is_rd = name.str.contains("RD-", regex=True, na=False)

    util = result["自忙时利用率"]
    result["负荷情况"] = "正常"
    result.loc[is_rdc_dc & (util > 90), "负荷情况"] = "负荷高小区"
    result.loc[is_rd & (util > 70) & ~is_rdc_dc, "负荷情况"] = "负荷高小区"
    result.loc[~is_rdc_dc & ~is_rd & (util > 50), "负荷情况"] = "负荷高小区"
    result.loc[util.isna(), "负荷情况"] = "正常"

    is_na_traffic = result["日均流量"].isna()
    is_tail = (result["日均流量"] <= tail_threshold) & ~is_na_traffic
    is_zero = result["日均流量"] == 0
    is_high_util = result["自忙时利用率"].notna() & (result["自忙时利用率"] > 20)

    result["长尾小区"] = pd.NA
    result.loc[is_tail & is_zero, "长尾小区"] = "具体原因待确认"
    result.loc[is_tail & ~is_zero & is_high_util, "长尾小区"] = "长尾待观察"
    result.loc[is_tail & ~is_zero & ~is_high_util, "长尾小区"] = "长尾需处理"

    result["记录开始时间"] = first_existing(result, ["记录开始时间"])
    result["记录结束时间"] = first_existing(result, ["记录结束时间"])
    result["地市"] = first_existing(result, ["所属地市"])
    result["网元状态"] = first_existing(result, ["网元状态"])
    result["小区名称"] = first_existing(result, ["小区名称"])
    result["band"] = first_existing(result, ["使用频段", "频点"])
    result["场景 V容量表"] = first_existing(result, ["场景"])
    result["TYPE"] = pd.NA
    result["语音话务量Erl （VOLTE/VoNR）"] = first_existing(result, ["VOLTE语音话务量"])
    result["是否全省高负荷预警小区（集团口径）"] = first_existing(result, ["是否高流量预警小区"])
    result["是否高负荷待扩容小区"] = first_existing(result, ["是否高负荷待扩容小区"])
    result["是否全省高负荷预警小区（省内口径）"] = first_existing(result, ["是否高流量预警小区"])
    result["物理站"] = first_existing(result, ["所属站点名称"])

    if "week_上行PRB" in result.columns:
        result["自忙时上行PRB平均利用率"] = result["自忙时上行PRB平均利用率"].combine_first(result["week_上行PRB"])
    if "week_下行PRB" in result.columns:
        result["自忙时下行PRB平均利用率"] = result["自忙时下行PRB平均利用率"].combine_first(result["week_下行PRB"])
    if "week_PDCCH" in result.columns:
        result["自忙时PDCCH信道CCE占用率"] = result["自忙时PDCCH信道CCE占用率"].combine_first(result["week_PDCCH"])

    ordered_columns = [
        "记录开始时间", "记录结束时间", "地市", "CGI", "网元状态", "小区名称", "扇区", "band",
        "场景 V容量表", "TYPE", "流量是否正常", "负荷情况", "流量排名升序", "长尾小区",
        "自忙时利用率", "日均流量", "语音话务量Erl （VOLTE/VoNR）", "MRO移动总采样点", "MRO移动覆盖率", "平均TA米",
        "工作日自忙时利用率", "工作日日均流量", "工作日自忙时RRC连接最大数",
        "工作日零流量天数", "周末零流量天数", "最大3天流量均值",
        "周末自忙时利用率", "周末日均流量", "周末自忙时RRC连接最大数",
        "自忙时上行PRB平均利用率", "自忙时下行PRB平均利用率", "自忙时PDCCH信道CCE占用率",
        "自忙时有效RRC连接最大数", "自忙时RRC连接最大数", "自忙时有效RRC连接平均数",
        "自忙时总流量", "自忙时上行流量", "自忙时下行流量",
        "是否全省高负荷预警小区（集团口径）", "是否高负荷待扩容小区", "是否全省高负荷预警小区（省内口径）",
        "流量系数", "物理站",
    ]
    return result.reindex(columns=ordered_columns)


def build_45g_table(table_5g: pd.DataFrame, table_4g: pd.DataFrame) -> pd.DataFrame:
    merged_5g = table_5g.copy()
    merged_4g = table_4g.copy()

    merged_5g.insert(0, "网络制式", "5G")
    merged_4g.insert(0, "网络制式", "4G")

    merged_5g = merged_5g.rename(columns={"NCGI": "CGI/NCGI", "VoNR语音话务量": "语音话务量Erl （VOLTE/VoNR）"})
    merged_4g = merged_4g.rename(columns={"CGI": "CGI/NCGI"})

    all_columns = ["网络制式"] + list(dict.fromkeys(column for column in merged_5g.columns if column != "网络制式"))
    for column in all_columns:
        if column not in merged_5g.columns:
            merged_5g[column] = pd.NA
        if column not in merged_4g.columns:
            merged_4g[column] = pd.NA

    merged = pd.concat(
        [merged_5g[all_columns], merged_4g[all_columns]],
        ignore_index=True,
        sort=False,
    )
    return merged



def _build_capacity_tables(conn, logger, progress: GuiProgress | None = None):
    if progress:
        progress.update(22, "数据导入完成，使用数据库进行聚合计算")

    timestamp = resolve_output_timestamp(conn)
    output_paths = build_output_paths(timestamp)
    if timestamp:
        logger.log(f"本次输出时间戳: {timestamp}")
    else:
        logger.log("未从周表中识别到开始/结束时间，输出文件将使用默认文件名")

    logger.log("开始生成 5G 容量表")
    start = time.perf_counter()
    table_5g = build_5g_table(conn, logger)
    elapsed = time.perf_counter() - start
    if progress:
        progress.update(45, f"5G表生成完成，共 {len(table_5g)} 条")
    logger.log(f"5G 容量表生成完成 (耗时 {elapsed:.1f}s)")

    cog_mapping = load_cog_coverage_mapping(conn, logger)
    if not cog_mapping.empty:
        table_5g = apply_sector_mapping(table_5g, cog_mapping, "NCGI", logger)

    logger.log("开始生成 4G 容量表")
    start = time.perf_counter()
    table_4g = build_4g_table(conn, logger)
    elapsed = time.perf_counter() - start
    if progress:
        progress.update(68, f"4G表生成完成，共 {len(table_4g)} 条")
    logger.log(f"4G 容量表生成完成 (耗时 {elapsed:.1f}s)")

    if not cog_mapping.empty:
        table_4g = apply_sector_mapping(table_4g, cog_mapping, "CGI", logger)

    return table_5g, table_4g, output_paths


def run_pipeline(
    progress_callback: ProgressCallback | None = None,
    log_callback: LogCallback | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    started_at = time.perf_counter()
    logger = GuiLogger(log_callback)
    logger.log("=" * 50)
    logger.log("容量表合成工具启动")
    logger.log("=" * 50)
    progress = GuiProgress(progress_callback, logger)
    logger.log("初始化数据库（低内存模式：使用 DuckDB 磁盘数据库）")
    init_db()
    conn = get_db_connection()
    try:
        progress.update(3, "开始导入数据到数据库")
        logger.log("准备扫描并导入源文件到 DuckDB 数据库")
        load_sources_to_db(conn, logger)
        table_5g, table_4g, output_paths = _build_capacity_tables(conn, logger, progress)
        logger.log("开始合并 45G 总表")
        start = time.perf_counter()
        table_45g = build_45g_table(table_5g, table_4g)
        elapsed = time.perf_counter() - start
        progress.update(80, f"45G总表生成完成，共 {len(table_45g)} 条")
        logger.log(f"45G 总表生成完成 (耗时 {elapsed:.1f}s)")
        logger.log("开始生成低效小区结果")
        # 延迟导入避免与 loweff 的循环依赖（loweff 反向依赖 capacity 的构建函数）
        from app.pipelines.loweff import build_low_efficiency_table

        loweff_5g, loweff_4g, loweff_4g_full, loweff_summary = build_low_efficiency_table(
            table_5g, table_4g, table_45g
        )
        logger.log(
            f"低效小区结果生成完成，5G {len(loweff_5g)} 条，4G {len(loweff_4g)} 条，"
            f"全量4G评估 {len(loweff_4g_full)} 条"
        )
        logger.log(f"开始写出文件: {output_paths['5g'].name}")
        start = time.perf_counter(); table_5g.to_excel(output_paths["5g"], index=False); elapsed = time.perf_counter() - start
        progress.update(88, f"已生成: {output_paths['5g'].name}"); logger.log(f"写出 {output_paths['5g'].name} 完成 (耗时 {elapsed:.1f}s)")
        logger.log(f"开始写出文件: {output_paths['4g'].name}")
        start = time.perf_counter(); table_4g.to_excel(output_paths["4g"], index=False); elapsed = time.perf_counter() - start
        progress.update(94, f"已生成: {output_paths['4g'].name}"); logger.log(f"写出 {output_paths['4g'].name} 完成 (耗时 {elapsed:.1f}s)")
        logger.log(f"开始写出文件: {output_paths['45g'].name}")
        start = time.perf_counter(); table_45g.to_excel(output_paths["45g"], index=False); elapsed = time.perf_counter() - start
        progress.update(96, f"已生成: {output_paths['45g'].name}"); logger.log(f"写出 {output_paths['45g'].name} 完成 (耗时 {elapsed:.1f}s)")
        with pd.ExcelWriter(LOWEFF_OUTPUT_PATH) as writer:
            loweff_5g.to_excel(writer, index=False, sheet_name="5G低效明细")
            loweff_4g.to_excel(writer, index=False, sheet_name="4G低效明细")
            loweff_4g_full.to_excel(writer, index=False, sheet_name="全量4G小区评估")
            loweff_summary.to_excel(writer, index=False, sheet_name="统计汇总")
        logger.log(f"写出 {LOWEFF_OUTPUT_PATH.name} 完成")

        logger.log("将容量表合成结果持久化到统一数据库...")
        start = time.perf_counter()
        try:
            with CapacityResultManager() as mgr:
                counts = mgr.save_results(table_5g, table_4g, table_45g)
            elapsed = time.perf_counter() - start
            logger.log(f"容量表结果已保存到统一数据库 (5G: {counts.get('5g', 0)} 条, "
                        f"4G: {counts.get('4g', 0)} 条, "
                        f"45G: {counts.get('45g', 0)} 条, 耗时 {elapsed:.1f}s)")
        except Exception as e:
            logger.log(f"持久化容量表结果失败（不影响 Excel 输出）: {e}")

        progress.update(100, f"已生成: {LOWEFF_OUTPUT_PATH.name}")
        elapsed_seconds = time.perf_counter() - started_at
        logger.log(f"5G表记录数: {len(table_5g)}")
        logger.log(f"4G表记录数: {len(table_4g)}")
        logger.log(f"45G总表记录数: {len(table_45g)}")
        logger.log(f"总耗时: {elapsed_seconds:.2f} 秒")
        logger.log("全部处理完成")
        logger.log("=" * 50)
        get_logger().info(f"处理完成，耗时 {elapsed_seconds:.2f} 秒")
        return table_5g, table_4g, table_45g
    finally:
        conn.close()
        try:
            if DB_PATH.exists():
                DB_PATH.unlink()
                logger.log("临时数据库已清理")
        except OSError:
            pass


def main() -> None:
    run_pipeline()



__all__ = [
    "FILE_PATTERNS",
    "import_cog_coverage_to_db",
    "load_cog_coverage_mapping",
    "load_sources_to_db",
    "apply_sector_mapping",
    "build_5g_table",
    "build_4g_table",
    "build_45g_table",
    "run_pipeline",
    "main",
]
