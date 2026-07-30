"""低效小区分析流水线。"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from app.pipelines.capacity import (
    _build_capacity_tables,
    build_45g_table,
    load_sources_to_db,
)
from app.pipelines.common import (
    DB_PATH,
    LOWEFF_OUTPUT_PATH,
    LTE_BANDS,
    GuiLogger,
    GuiProgress,
    LogCallback,
    ProgressCallback,
    build_band_aggregations,
    get_db_connection,
    init_db,
    normalize_text,
)


def _safe_div(numerator, denominator):
    if denominator in (0, 0.0, None) or pd.isna(denominator):
        return pd.NA
    return numerator / denominator


CAPACITY_BAND_TO_STANDARD = {
    "D频段": "D",
    "F频段": "F",
    "E频段": "E",
    "A频段": "A",
    "5G-3DMIMO": "5G-3Dmimo",
    "5G-3Dmimo": "5G-3Dmimo",
    "3D-MIMO": "5G-3Dmimo",
}


def _normalize_capacity_band(value) -> str:
    band = normalize_text(value)
    if not band:
        return ""
    return CAPACITY_BAND_TO_STANDARD.get(band, band)


def _build_loweff_band_lookups(
    table_4g: pd.DataFrame, table_5g: pd.DataFrame
) -> tuple[dict, dict, dict, dict]:
    """汇总同扇区/物理站的纯4G频段与含5G频段字符串。"""
    frames: list[pd.DataFrame] = []
    for table in (table_4g, table_5g):
        if table is None or table.empty:
            continue
        n = len(table)
        phys = (
            table["物理站"].map(normalize_text)
            if "物理站" in table.columns
            else pd.Series([""] * n, index=table.index)
        )
        sector = (
            table["扇区"].map(normalize_text)
            if "扇区" in table.columns
            else pd.Series([""] * n, index=table.index)
        )
        band = (
            table["band"].map(_normalize_capacity_band)
            if "band" in table.columns
            else pd.Series([""] * n, index=table.index)
        )
        frames.append(pd.DataFrame({"物理站": phys, "扇区": sector, "BAND": band}))
    if not frames:
        return {}, {}, {}, {}
    work = pd.concat(frames, ignore_index=True)
    work = work[work["BAND"].astype(bool)]
    sector_all: dict = {}
    sector_lte: dict = {}
    station_all: dict = {}
    station_lte: dict = {}
    sector_work = work[work["扇区"].astype(bool)]
    if not sector_work.empty:
        sector_all, sector_lte = build_band_aggregations(sector_work, "扇区", LTE_BANDS)
    station_work = work[work["物理站"].astype(bool)]
    if not station_work.empty:
        station_all, station_lte = build_band_aggregations(station_work, "物理站", LTE_BANDS)
    return sector_lte, sector_all, station_lte, station_all


FULL_4G_EVAL_COLUMNS = [
    "网络制式",
    "CGI/NCGI",
    "小区名称",
    "物理站",
    "扇区",
    "同扇区纯4G频段",
    "同扇区包含5G频段",
    "物理站纯4G频段",
    "物理站包含5G频段",
    "地市",
    "忙时利用率",
    "忙时流量",
    "自忙时有效RRC连接平均数",
    "扇区等效利用率_拆除前",
    "扇区等效单载波流量_拆除前",
    "小区拆除后扇区等效利用率（<40%）",
    "扇区等效单载波流量_拆除后",
    "能否减容",
]

LTE_BAND_M_VALUES = {
    "3D-MIMO": 2.5,
    "5G-3Dmimo": 2.5,
    "5G-3DMIMO": 2.5,
    "FDD1800": 1.5,
    "E": 1.0,
    "E频段": 1.0,
    "D": 1.0,
    "D频段": 1.0,
    "F": 1.0,
    "F频段": 1.0,
    "F1": 1.0,
    "A": 0.75,
    "A频段": 0.75,
    "F2": 0.5,
    "FDD900": 0.75,
}


def _resolve_lte_band_m(row) -> float | None:
    raw = normalize_text(row.get("band")) or normalize_text(row.get("BAND_A"))
    if not raw:
        return None
    if raw in LTE_BAND_M_VALUES:
        return float(LTE_BAND_M_VALUES[raw])
    std = _normalize_capacity_band(raw)
    if std in LTE_BAND_M_VALUES:
        return float(LTE_BAND_M_VALUES[std])
    return None


def build_low_efficiency_table(
    table_5g: pd.DataFrame, table_4g: pd.DataFrame, table_45g: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows_5g: list[dict[str, object]] = []
    rows_4g: list[dict[str, object]] = []
    rows_4g_full: list[dict[str, object]] = []
    sector_lte_map, sector_all_map, station_lte_map, station_all_map = _build_loweff_band_lookups(
        table_4g, table_5g
    )

    nr_eval_bands = {"2.6GHz", "4.9GHz"}
    if not table_5g.empty:
        for _, row in table_5g.iterrows():
            band = normalize_text(row.get("band"))
            if band not in nr_eval_bands:
                continue

            max3_avg = pd.to_numeric(row.get("最大3天流量均值"), errors="coerce")
            work_zero_days = pd.to_numeric(row.get("工作日零流量天数"), errors="coerce")
            weekend_zero_days = pd.to_numeric(row.get("周末零流量天数"), errors="coerce")
            coverage = normalize_text(row.get("覆盖类型"))

            # 5G零效益：工作日零流量>=2 且 周末零流量>=1
            zero_trigger = (
                pd.notna(work_zero_days)
                and work_zero_days >= 2
                and pd.notna(weekend_zero_days)
                and weekend_zero_days >= 1
            )

            # 5G低效益：最大3天流量均值，宏站/室外<3GB，室分/室内<2GB
            if coverage in {"室内", "室分"}:
                site_kind = "室分"
                low_threshold = 2.0
            else:
                site_kind = "宏站"
                low_threshold = 3.0
            low_trigger = pd.notna(max3_avg) and max3_avg < low_threshold

            if not (zero_trigger or low_trigger):
                continue

            if zero_trigger:
                low_type = "零效益小区"
                low_reason = (
                    f"工作日零流量{int(work_zero_days)}天+周末零流量{int(weekend_zero_days)}天"
                )
            else:
                low_type = "低效益小区"
                low_reason = (
                    f"{site_kind}最大3天流量均值<{low_threshold:g}GB（{max3_avg:.2f}GB）"
                )

            rows_5g.append({
                "网络制式": "5G",
                "CGI/NCGI": row.get("NCGI"),
                "小区名称": row.get("小区名称"),
                "物理站": row.get("物理站"),
                "扇区": row.get("扇区"),
                "地市": row.get("地市"),
                "band": band,
                "覆盖类型": coverage or pd.NA,
                "站点类型判定": site_kind,
                "忙时利用率": row.get("自忙时利用率"),
                "忙时流量": row.get("日均流量"),
                "自忙时有效RRC连接平均数": row.get("自忙时有效RRC连接平均数"),
                "工作日零流量天数": work_zero_days,
                "周末零流量天数": weekend_zero_days,
                "最大3天流量均值": max3_avg,
                "低效类型": low_type,
                "低效原因": low_reason,
                "优先级": 1 if zero_trigger else 2,
            })

    if not table_4g.empty:
        for sector, group in table_4g.groupby(table_4g["扇区"].fillna("").astype(str), dropna=False):
            sector_df = group.copy()
            has_sector = bool(sector)

            denom = 0.0
            ps = 0.0
            traffic_sum = 0.0
            if has_sector:
                for _, srow in sector_df.iterrows():
                    m = _resolve_lte_band_m(srow)
                    if m is None:
                        continue
                    util = pd.to_numeric(srow.get("自忙时利用率"), errors="coerce")
                    traffic = pd.to_numeric(srow.get("日均流量"), errors="coerce")
                    if pd.notna(util):
                        ps += m * util
                    if pd.notna(traffic):
                        traffic_sum += traffic
                    denom += m

            before_util = round(float(ps / denom), 2) if has_sector and denom > 0 else pd.NA
            before_traffic = round(float(traffic_sum / denom), 2) if has_sector and denom > 0 else pd.NA
            sector_candidates: list[dict[str, object]] = []

            for _, row in sector_df.iterrows():
                station = normalize_text(row.get("物理站"))
                this_m = _resolve_lte_band_m(row)
                after_util = "[-]"
                after_traffic = "[-]"
                low_reason = ""
                impact = None
                if has_sector and this_m is not None and denom > this_m:
                    new_denom = denom - this_m
                    if new_denom > 0:
                        after_util = round(float(ps / new_denom), 2)
                        after_traffic = round(float(traffic_sum / new_denom), 2)
                        if after_util < 40 and after_traffic < 20:
                            low_reason = "拆除后扇区等效利用率<40% 且 拆除后扇区等效单载波流量<20GB"
                            impact = float(after_util) + float(after_traffic)

                base_row = {
                    "网络制式": "4G",
                    "CGI/NCGI": row.get("CGI"),
                    "小区名称": row.get("小区名称"),
                    "物理站": row.get("物理站"),
                    "扇区": row.get("扇区"),
                    "同扇区纯4G频段": sector_lte_map.get(sector, "") if has_sector else "",
                    "同扇区包含5G频段": sector_all_map.get(sector, "") if has_sector else "",
                    "物理站纯4G频段": station_lte_map.get(station, ""),
                    "物理站包含5G频段": station_all_map.get(station, ""),
                    "地市": row.get("地市"),
                    "忙时利用率": row.get("自忙时利用率"),
                    "忙时流量": row.get("日均流量"),
                    "自忙时有效RRC连接平均数": row.get("自忙时有效RRC连接平均数"),
                    "扇区等效利用率_拆除前": before_util,
                    "扇区等效单载波流量_拆除前": before_traffic,
                    "小区拆除后扇区等效利用率（<40%）": after_util,
                    "扇区等效单载波流量_拆除后": after_traffic,
                    "能否减容": "是" if low_reason else "否",
                    "低效原因": low_reason,
                }
                rows_4g_full.append(base_row)

                if low_reason and impact is not None:
                    candidate = dict(base_row)
                    candidate["优先级"] = 1
                    candidate["影响值"] = impact
                    sector_candidates.append(candidate)

            if sector_candidates:
                best = sorted(
                    sector_candidates,
                    key=lambda x: (
                        x["影响值"],
                        x["小区拆除后扇区等效利用率（<40%）"],
                        x["扇区等效单载波流量_拆除后"],
                    ),
                )[0]
                best.pop("影响值", None)
                rows_4g.append(best)

    df5 = pd.DataFrame(rows_5g)
    if not df5.empty:
        df5 = df5.sort_values(by=["优先级", "低效类型", "最大3天流量均值"], ascending=[True, True, True])

    df4 = pd.DataFrame(rows_4g)
    if not df4.empty:
        df4 = df4.sort_values(
            by=["优先级", "小区拆除后扇区等效利用率（<40%）", "扇区等效单载波流量_拆除后"],
            ascending=[True, True, True],
        )

    df4_full = pd.DataFrame(rows_4g_full, columns=FULL_4G_EVAL_COLUMNS)
    if not df4_full.empty:
        df4_full = df4_full.reindex(columns=FULL_4G_EVAL_COLUMNS)

    nr_denom = 0
    if not table_5g.empty and "band" in table_5g.columns:
        bands = table_5g["band"].map(normalize_text)
        nr_denom = int(bands.isin({"2.6GHz", "4.9GHz"}).sum())
    zero_cnt = int((df5["低效类型"] == "零效益小区").sum()) if not df5.empty else 0
    low_cnt = int((df5["低效类型"] == "低效益小区").sum()) if not df5.empty else 0
    ratio = round(len(df5) / nr_denom, 6) if nr_denom > 0 else pd.NA

    summary_rows = [
        {"指标": "45G总数", "数值": len(table_45g)},
        {"指标": "2.6G/4.9G 5G小区总数", "数值": nr_denom},
        {"指标": "5G零效益数", "数值": zero_cnt},
        {"指标": "5G低效益数", "数值": low_cnt},
        {"指标": "5G低效数", "数值": len(df5)},
        {"指标": "5G低效占比", "数值": ratio},
        {"指标": "4G低效数", "数值": len(df4)},
        {"指标": "全量4G评估数", "数值": len(df4_full)},
        {"指标": "低效总数", "数值": len(df5) + len(df4)},
    ]
    summary = pd.DataFrame(summary_rows)
    return df5, df4, df4_full, summary



def run_low_efficiency_pipeline(
    progress_callback: ProgressCallback | None = None,
    log_callback: LogCallback | None = None,
) -> Path:
    """仅生成低效小区结果（含容量中间表计算，但不写出容量表）。"""
    logger = GuiLogger(log_callback)
    logger.log("=" * 50)
    logger.log("低效小区分析工具启动")
    logger.log("=" * 50)
    progress = GuiProgress(progress_callback, logger)
    init_db()
    conn = get_db_connection()
    try:
        progress.update(3, "开始导入数据到数据库")
        logger.log("准备扫描并导入源文件到 DuckDB 数据库")
        load_sources_to_db(conn, logger)
        table_5g, table_4g, _output_paths = _build_capacity_tables(conn, logger, progress)
        logger.log("开始合并 45G 总表")
        start = time.perf_counter()
        table_45g = build_45g_table(table_5g, table_4g)
        elapsed = time.perf_counter() - start
        progress.update(60, f"45G总表生成完成，共 {len(table_45g)} 条")
        logger.log(f"45G 总表生成完成 (耗时 {elapsed:.1f}s)")
        logger.log("开始生成低效小区结果")
        loweff_5g, loweff_4g, loweff_4g_full, loweff_summary = build_low_efficiency_table(
            table_5g, table_4g, table_45g
        )
        logger.log(
            f"低效小区结果生成完成，5G {len(loweff_5g)} 条，4G {len(loweff_4g)} 条，"
            f"全量4G评估 {len(loweff_4g_full)} 条"
        )
        with pd.ExcelWriter(LOWEFF_OUTPUT_PATH) as writer:
            loweff_5g.to_excel(writer, index=False, sheet_name="5G低效明细")
            loweff_4g.to_excel(writer, index=False, sheet_name="4G低效明细")
            loweff_4g_full.to_excel(writer, index=False, sheet_name="全量4G小区评估")
            loweff_summary.to_excel(writer, index=False, sheet_name="统计汇总")
        logger.log(f"写出 {LOWEFF_OUTPUT_PATH.name} 完成")
        progress.update(100, f"已生成: {LOWEFF_OUTPUT_PATH.name}")
        logger.log("=" * 50)
        return LOWEFF_OUTPUT_PATH
    finally:
        conn.close()
        try:
            if DB_PATH.exists():
                DB_PATH.unlink()
                logger.log("临时数据库已清理")
        except OSError:
            pass



__all__ = [
    "LOWEFF_OUTPUT_PATH",
    "build_low_efficiency_table",
    "run_low_efficiency_pipeline",
]
