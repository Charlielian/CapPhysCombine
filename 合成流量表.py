from __future__ import annotations

import queue
import re
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

FILE_PATTERNS = {
    "5g_week": "5G小区容量-周*.xlsx",
    "5g_day": "5G小区容量报表*.xlsx",
    "5g_mr": "5GMR覆盖-小区天*.xlsx",
    "5g_kpi": "5G小区性能KPI报表*.xlsx",
    "4g_week": "重要场景-周*.xlsx",
    "4g_day": "重要场景-天*.xlsx",
    "4g_mr": "4GMR覆盖-小区天*.xlsx",
}

OUTPUT_5G = BASE_DIR / "合成_容量表_5G.xlsx"
OUTPUT_4G = BASE_DIR / "合成_容量表_4G.xlsx"
OUTPUT_45G = BASE_DIR / "容量表_45G.xlsx"
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
TIME_COLUMN_CANDIDATES = {
    "start": ["记录开始时间", "开始时间"],
    "end": ["记录结束时间", "结束时间"],
}

ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]


class SourceFileError(RuntimeError):
    pass


class GuiLogger:
    def __init__(self, callback: LogCallback | None = None) -> None:
        self.callback = callback

    def log(self, message: str) -> None:
        if self.callback:
            self.callback(message)
        else:
            print(message)


class GuiProgress:
    def __init__(self, callback: ProgressCallback | None = None, logger: GuiLogger | None = None) -> None:
        self.callback = callback
        self.logger = logger or GuiLogger()

    def update(self, value: int, message: str) -> None:
        value = max(0, min(100, value))
        if self.callback:
            self.callback(value, message)
        self.logger.log(message)


def pick_latest_file(pattern: str) -> Path:
    matches = sorted(DATA_DIR.glob(pattern))
    if not matches:
        raise SourceFileError(f"未找到匹配文件: {DATA_DIR / pattern}")

    def sort_key(path: Path) -> tuple[tuple[str, ...], str]:
        dates = tuple(DATE_RE.findall(path.name))
        return dates, path.name

    return max(matches, key=sort_key)


def read_excel(path: Path) -> pd.DataFrame:
    return pd.read_excel(path)


def first_existing(df: pd.DataFrame, columns: Iterable[str], default=None):
    for column in columns:
        if column in df.columns:
            return df[column]
    return pd.Series(default, index=df.index)


def to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.replace(0, pd.NA)
    return numerator.div(denominator)


def normalize_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def build_output_paths(timestamp: str | None = None) -> dict[str, Path]:
    suffix = f"_{timestamp}" if timestamp else ""
    return {
        "5g": BASE_DIR / f"合成_容量表_5G{suffix}.xlsx",
        "4g": BASE_DIR / f"合成_容量表_4G{suffix}.xlsx",
        "45g": BASE_DIR / f"容量表_45G{suffix}.xlsx",
    }


def first_valid_timestamp(df: pd.DataFrame, candidates: list[str]) -> pd.Timestamp | None:
    for column in candidates:
        if column not in df.columns:
            continue
        series = pd.to_datetime(df[column], errors="coerce").dropna()
        if not series.empty:
            return series.iloc[0]
    return None


def resolve_output_timestamp(sources: dict[str, pd.DataFrame]) -> str | None:
    for source_name in ["5g_week", "4g_week"]:
        source = sources.get(source_name)
        if source is None:
            continue
        start_time = first_valid_timestamp(source, TIME_COLUMN_CANDIDATES["start"])
        end_time = first_valid_timestamp(source, TIME_COLUMN_CANDIDATES["end"])
        if start_time is not None and end_time is not None:
            return f"{start_time:%Y%m%d}_{end_time:%Y%m%d}"
    return None


def default_output_paths() -> dict[str, Path]:
    return build_output_paths()


def load_sources(logger: GuiLogger | None = None) -> dict[str, pd.DataFrame]:
    logger = logger or GuiLogger()
    selected = {name: pick_latest_file(pattern) for name, pattern in FILE_PATTERNS.items()}
    logger.log("使用以下源文件：")
    for name, path in selected.items():
        logger.log(f"- {name}: {path.name}")

    sources: dict[str, pd.DataFrame] = {}
    total = len(selected)
    for index, (name, path) in enumerate(selected.items(), start=1):
        logger.log(f"开始解析 [{index}/{total}] {name}: {path.name}")
        frame = read_excel(path)
        sources[name] = frame
        logger.log(f"解析完成 [{index}/{total}] {name}: {len(frame)} 行 x {len(frame.columns)} 列")
    return sources


def build_5g_table(sources: dict[str, pd.DataFrame]) -> pd.DataFrame:
    week = sources["5g_week"].copy()
    day = sources["5g_day"].copy()
    mr = sources["5g_mr"].copy()
    kpi = sources["5g_kpi"].copy()

    week["NCGI"] = week["NCGI"].astype(str)
    day["NCGI"] = day["NCGI"].astype(str)
    mr["小区NCGI"] = mr["小区NCGI"].astype(str)
    kpi["NCGI"] = kpi["NCGI"].astype(str)

    day["记录开始时间"] = normalize_datetime(day["记录开始时间"])
    day["是否周末"] = day["记录开始时间"].dt.weekday >= 5

    util_col_5g = "忙时小区PRB利用率"
    if util_col_5g not in day.columns and "忙时小区PRB利用率(%)" in day.columns:
        util_col_5g = "忙时小区PRB利用率(%)"

    day_group = day.groupby("NCGI", dropna=False).agg(
        自忙时利用率=(util_col_5g, "mean"),
        日均流量=("日RLC层上下行总流量(G)", "mean"),
        自忙时上行PRB平均利用率=("忙时上行PRB平均利用率(%)", "mean"),
        自忙时下行PRB平均利用率=("忙时下行PRB平均利用率(%)", "mean"),
        自忙时PDCCH信道CCE占用率=("忙时PDCCH信道CCE占用率(%)", "mean"),
        自忙时RRC连接最大数=("RRC连接最大数-忙时", "mean"),
        自忙时有效RRC连接平均数=("RRC连接平均数-忙时", "mean"),
        自忙时上行流量=("忙时RLC层上行业务字节数(G)", "mean"),
        自忙时下行流量=("忙时RLC层下行业务字节数(G)", "mean"),
    ).reset_index()
    day_group["自忙时总流量"] = day_group["自忙时上行流量"] + day_group["自忙时下行流量"]
    day_group["自忙时有效RRC连接最大数"] = day_group["自忙时RRC连接最大数"]

    weekday_group = (
        day.loc[~day["是否周末"]]
        .groupby("NCGI", dropna=False)
        .agg(
            工作日自忙时利用率=(util_col_5g, "mean"),
            工作日日均流量=("日RLC层上下行总流量(G)", "mean"),
            工作日自忙时RRC连接最大数=("RRC连接最大数-忙时", "mean"),
        )
        .reset_index()
    )
    weekend_group = (
        day.loc[day["是否周末"]]
        .groupby("NCGI", dropna=False)
        .agg(
            周末自忙时利用率=(util_col_5g, "mean"),
            周末日均流量=("日RLC层上下行总流量(G)", "mean"),
            周末自忙时RRC连接最大数=("RRC连接最大数-忙时", "mean"),
        )
        .reset_index()
    )

    mr_group = mr.groupby("小区NCGI", dropna=False).agg(
        MRO移动总采样点=("移动RSRP采样的总采样点", "mean"),
        MRO强于110采样点合计=("移动RSRP采样强于-110采样点", "sum"),
        MRO总采样点合计=("移动RSRP采样的总采样点", "sum"),
        平均TA米=("移动平均TA(M)", "mean"),
    ).reset_index()
    mr_group["MRO移动覆盖率"] = safe_divide(mr_group["MRO强于110采样点合计"], mr_group["MRO总采样点合计"])

    kpi_group = kpi.groupby("NCGI", dropna=False).agg(
        VoNR语音话务量=("VoNR语音话务量", "mean"),
    ).reset_index()

    week_unique = week.drop_duplicates(subset=["NCGI"]).copy()
    result = week_unique.merge(day_group, on="NCGI", how="left")
    result = result.merge(weekday_group, on="NCGI", how="left")
    result = result.merge(weekend_group, on="NCGI", how="left")
    result = result.merge(mr_group, left_on="NCGI", right_on="小区NCGI", how="left")
    result = result.merge(kpi_group, on="NCGI", how="left")

    avg_traffic = result["日均流量"].mean(skipna=True)
    result["流量系数"] = result["日均流量"] / avg_traffic if pd.notna(avg_traffic) and avg_traffic != 0 else pd.NA
    result["流量是否正常"] = pd.cut(
        result["流量系数"],
        bins=[-float("inf"), 0.2, 3, float("inf")],
        labels=["低流量系数小区", "正常", "高流量系数小区"],
        right=False,
    )
    result.loc[result["流量系数"].isna(), "流量是否正常"] = pd.NA
    result["负荷情况"] = result["自忙时利用率"].apply(lambda x: "负荷高小区" if pd.notna(x) and x > 80 else "正常")
    result["流量排名升序"] = result["日均流量"].rank(method="min", ascending=True)

    tail_threshold = result["日均流量"].quantile(0.3)

    def classify_5g_tail(row: pd.Series) -> str | pd.NA:
        traffic = row["日均流量"]
        util = row["自忙时利用率"]
        if pd.isna(traffic):
            return pd.NA
        if traffic <= tail_threshold:
            if traffic == 0:
                return "长尾具体原因待确认"
            if pd.notna(util) and util > 20:
                return "长尾待观察"
            return "长尾需处理"
        return pd.NA

    result["长尾小区"] = result.apply(classify_5g_tail, axis=1)

    result["记录开始时间"] = first_existing(result, ["记录开始时间"])
    result["记录结束时间"] = first_existing(result, ["记录结束时间"])
    result["地市"] = first_existing(result, ["地市"])
    result["网元状态"] = first_existing(result, ["网元状态"])
    result["小区名称"] = first_existing(result, ["小区名称"])
    result["band"] = first_existing(result, ["使用频段"])
    result["场景 V容量表"] = first_existing(result, ["场景1", "一级场景"])
    result["TYPE"] = pd.NA
    result["是否全省高负荷预警小区（集团口径）"] = pd.NA
    result["是否高负荷待扩容小区"] = first_existing(result, ["是否高负荷待扩容小区", "是否高负荷"])
    result["是否全省高负荷预警小区（省内口径）"] = pd.NA
    result["物理站"] = first_existing(result, ["站点名称"])

    ordered_columns = [
        "记录开始时间",
        "记录结束时间",
        "地市",
        "NCGI",
        "网元状态",
        "小区名称",
        "扇区",
        "band",
        "场景 V容量表",
        "TYPE",
        "流量是否正常",
        "负荷情况",
        "流量排名升序",
        "长尾小区",
        "自忙时利用率",
        "日均流量",
        "VoNR语音话务量",
        "MRO移动总采样点",
        "MRO移动覆盖率",
        "平均TA米",
        "工作日自忙时利用率",
        "工作日日均流量",
        "工作日自忙时RRC连接最大数",
        "周末自忙时利用率",
        "周末日均流量",
        "周末自忙时RRC连接最大数",
        "自忙时上行PRB平均利用率",
        "自忙时下行PRB平均利用率",
        "自忙时PDCCH信道CCE占用率",
        "自忙时有效RRC连接最大数",
        "自忙时RRC连接最大数",
        "自忙时有效RRC连接平均数",
        "自忙时总流量",
        "自忙时上行流量",
        "自忙时下行流量",
        "是否全省高负荷预警小区（集团口径）",
        "是否高负荷待扩容小区",
        "是否全省高负荷预警小区（省内口径）",
        "流量系数",
        "物理站",
    ]
    result["扇区"] = pd.NA
    return result.reindex(columns=ordered_columns)


def build_4g_table(sources: dict[str, pd.DataFrame]) -> pd.DataFrame:
    week = sources["4g_week"].copy()
    day = sources["4g_day"].copy()
    mr = sources["4g_mr"].copy()

    week["CGI"] = week["CGI"].astype(str)
    day["CGI"] = day["CGI"].astype(str)
    mr["cgi"] = mr["cgi"].astype(str)

    day["记录开始时间"] = normalize_datetime(day["记录开始时间"])
    day["是否周末"] = day["记录开始时间"].dt.weekday >= 5
    day["自忙时利用率"] = day[["自忙时上行PRB平均利用率", "自忙时下行PRB平均利用率"]].max(axis=1)

    day_group = day.groupby("CGI", dropna=False).agg(
        自忙时利用率=("自忙时利用率", "mean"),
        日均流量=("日4G流量（GB）", "mean"),
        自忙时上行PRB平均利用率=("自忙时上行PRB平均利用率", "mean"),
        自忙时下行PRB平均利用率=("自忙时下行PRB平均利用率", "mean"),
        自忙时PDCCH信道CCE占用率=("自忙时PDCCH信道CCE占用率", "mean"),
        自忙时有效RRC连接最大数=("自忙时有效RRC连接最大数", "mean"),
        自忙时RRC连接最大数=("自忙时RRC连接最大数", "mean"),
        自忙时有效RRC连接平均数=("自忙时有效RRC连接平均数", "mean"),
        自忙时上行流量=("自忙时空口上行业务字节数", "mean"),
        自忙时下行流量=("自忙时空口下行业务字节数", "mean"),
    ).reset_index()
    day_group["自忙时总流量"] = day_group["自忙时上行流量"] + day_group["自忙时下行流量"]

    weekday_group = (
        day.loc[~day["是否周末"]]
        .groupby("CGI", dropna=False)
        .agg(
            工作日自忙时利用率=("自忙时利用率", "mean"),
            工作日日均流量=("日4G流量（GB）", "mean"),
            工作日自忙时RRC连接最大数=("自忙时RRC连接最大数", "mean"),
        )
        .reset_index()
    )
    weekend_group = (
        day.loc[day["是否周末"]]
        .groupby("CGI", dropna=False)
        .agg(
            周末自忙时利用率=("自忙时利用率", "mean"),
            周末日均流量=("日4G流量（GB）", "mean"),
            周末自忙时RRC连接最大数=("自忙时RRC连接最大数", "mean"),
        )
        .reset_index()
    )

    mr_group = mr.groupby("cgi", dropna=False).agg(
        MRO移动总采样点=("MRO移动总采样点", "mean"),
        MRO有效点合计=("MRO移动大于等于负110DBM的采样点数", "sum"),
        MRO总采样点合计=("MRO移动总采样点", "sum"),
        平均TA米=("平均TA", "mean"),
    ).reset_index()
    mr_group["MRO移动覆盖率"] = safe_divide(mr_group["MRO有效点合计"], mr_group["MRO总采样点合计"])

    week_unique = week.drop_duplicates(subset=["CGI"]).copy()
    week_metrics = week.groupby("CGI", dropna=False).agg(
        自忙时上行PRB平均利用率=("自忙时上行PRB平均利用率", "mean"),
        自忙时下行PRB平均利用率=("自忙时下行PRB平均利用率", "mean"),
        自忙时PDCCH信道CCE占用率=("自忙时PDCCH信道CCE占用率", "mean"),
    ).reset_index()
    result = week_unique.merge(day_group, on="CGI", how="left")
    result = result.merge(week_metrics, on="CGI", how="left")
    result = result.merge(weekday_group, on="CGI", how="left")
    result = result.merge(weekend_group, on="CGI", how="left")
    result = result.merge(mr_group, left_on="CGI", right_on="cgi", how="left")

    avg_traffic = result["日均流量"].mean(skipna=True)
    result["流量系数"] = result["日均流量"] / avg_traffic if pd.notna(avg_traffic) and avg_traffic != 0 else pd.NA
    result["流量是否正常"] = pd.cut(
        result["流量系数"],
        bins=[-float("inf"), 0.2, 3, float("inf")],
        labels=["低流量系数小区", "正常", "高流量系数小区"],
        right=False,
    )
    result.loc[result["流量系数"].isna(), "流量是否正常"] = pd.NA
    result["流量排名升序"] = result["日均流量"].rank(method="min", ascending=True)

    def classify_4g_load(row: pd.Series) -> str:
        name = str(row.get("小区名称", ""))
        util = row.get("自忙时利用率")
        if pd.isna(util):
            return "正常"
        if any(flag in name for flag in ["RDC", "DC-", "RGS", "GS-"]):
            return "负荷高小区" if util > 90 else "正常"
        if "RD-" in name:
            return "负荷高小区" if util > 70 else "正常"
        return "负荷高小区" if util > 50 else "正常"

    result["负荷情况"] = result.apply(classify_4g_load, axis=1)
    tail_threshold = result["日均流量"].quantile(0.3)

    def classify_4g_tail(row: pd.Series) -> str | pd.NA:
        traffic = row["日均流量"]
        util = row["自忙时利用率"]
        if pd.isna(traffic):
            return pd.NA
        if traffic <= tail_threshold:
            if traffic == 0:
                return "具体原因待确认"
            if pd.notna(util) and util > 20:
                return "长尾待观察"
            return "长尾需处理"
        return pd.NA

    result["长尾小区"] = result.apply(classify_4g_tail, axis=1)

    result["记录开始时间"] = first_existing(result, ["记录开始时间_x", "记录开始时间"])
    result["记录结束时间"] = first_existing(result, ["记录结束时间"])
    result["地市"] = first_existing(result, ["所属地市"])
    result["网元状态"] = first_existing(result, ["网元状态"])
    result["小区名称"] = first_existing(result, ["小区名称"])
    result["扇区"] = pd.NA
    result["band"] = first_existing(result, ["使用频段", "频点"])
    result["场景 V容量表"] = first_existing(result, ["场景"])
    result["TYPE"] = pd.NA
    result["语音话务量Erl （VOLTE/VoNR）"] = first_existing(result, ["VOLTE语音话务量"])
    result["是否全省高负荷预警小区（集团口径）"] = first_existing(result, ["是否高流量预警小区"])
    result["是否高负荷待扩容小区"] = first_existing(result, ["是否高负荷待扩容小区"])
    result["是否全省高负荷预警小区（省内口径）"] = first_existing(result, ["是否高流量预警小区"])
    result["物理站"] = first_existing(result, ["所属站点名称"])
    result["自忙时有效RRC连接最大数"] = first_existing(result, ["自忙时有效RRC连接最大数_x", "自忙时有效RRC连接最大数"])
    result["自忙时RRC连接最大数"] = first_existing(result, ["自忙时RRC连接最大数_x", "自忙时RRC连接最大数"])
    result["自忙时上行PRB平均利用率"] = first_existing(result, ["自忙时上行PRB平均利用率_y", "自忙时上行PRB平均利用率"])
    result["自忙时下行PRB平均利用率"] = first_existing(result, ["自忙时下行PRB平均利用率_y", "自忙时下行PRB平均利用率"])
    result["自忙时PDCCH信道CCE占用率"] = first_existing(result, ["自忙时PDCCH信道CCE占用率_y", "自忙时PDCCH信道CCE占用率"])

    ordered_columns = [
        "记录开始时间",
        "记录结束时间",
        "地市",
        "CGI",
        "网元状态",
        "小区名称",
        "扇区",
        "band",
        "场景 V容量表",
        "TYPE",
        "流量是否正常",
        "负荷情况",
        "流量排名升序",
        "长尾小区",
        "自忙时利用率",
        "日均流量",
        "语音话务量Erl （VOLTE/VoNR）",
        "MRO移动总采样点",
        "MRO移动覆盖率",
        "平均TA米",
        "工作日自忙时利用率",
        "工作日日均流量",
        "工作日自忙时RRC连接最大数",
        "周末自忙时利用率",
        "周末日均流量",
        "周末自忙时RRC连接最大数",
        "自忙时上行PRB平均利用率",
        "自忙时下行PRB平均利用率",
        "自忙时PDCCH信道CCE占用率",
        "自忙时有效RRC连接最大数",
        "自忙时RRC连接最大数",
        "自忙时有效RRC连接平均数",
        "自忙时总流量",
        "自忙时上行流量",
        "自忙时下行流量",
        "是否全省高负荷预警小区（集团口径）",
        "是否高负荷待扩容小区",
        "是否全省高负荷预警小区（省内口径）",
        "流量系数",
        "物理站",
    ]
    return result.reindex(columns=ordered_columns)


def build_45g_table(table_5g: pd.DataFrame, table_4g: pd.DataFrame) -> pd.DataFrame:
    merged_5g = table_5g.copy()
    merged_4g = table_4g.copy()

    merged_5g.insert(0, "网络制式", "5G")
    merged_4g.insert(0, "网络制式", "4G")

    merged_5g = merged_5g.rename(columns={"NCGI": "CGI/NCGI", "VoNR语音话务量": "语音话务量Erl （VOLTE/VoNR）"})
    merged_4g = merged_4g.rename(columns={"CGI": "CGI/NCGI"})

    all_columns = ["网络制式"] + [column for column in merged_5g.columns if column != "网络制式"]
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


def run_pipeline(
    progress_callback: ProgressCallback | None = None,
    log_callback: LogCallback | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    started_at = time.perf_counter()
    logger = GuiLogger(log_callback)
    progress = GuiProgress(progress_callback, logger)

    progress.update(3, "开始处理容量表数据")
    logger.log("准备扫描并解析源文件")
    sources = load_sources(logger)
    progress.update(18, f"源文件读取完成，共解析 {len(sources)} 份文件")

    timestamp = resolve_output_timestamp(sources)
    output_paths = build_output_paths(timestamp)
    if timestamp:
        logger.log(f"本次输出时间戳: {timestamp}")
    else:
        logger.log("未从周表中识别到开始/结束时间，输出文件将使用默认文件名")

    logger.log("开始生成 5G 容量表")
    table_5g = build_5g_table(sources)
    progress.update(42, f"5G表生成完成，共 {len(table_5g)} 条")

    logger.log("开始生成 4G 容量表")
    table_4g = build_4g_table(sources)
    progress.update(66, f"4G表生成完成，共 {len(table_4g)} 条")

    logger.log("开始合并 45G 总表")
    table_45g = build_45g_table(table_5g, table_4g)
    progress.update(80, f"45G总表生成完成，共 {len(table_45g)} 条")

    logger.log(f"开始写出文件: {output_paths['5g'].name}")
    table_5g.to_excel(output_paths["5g"], index=False)
    progress.update(88, f"已生成: {output_paths['5g'].name}")

    logger.log(f"开始写出文件: {output_paths['4g'].name}")
    table_4g.to_excel(output_paths["4g"], index=False)
    progress.update(94, f"已生成: {output_paths['4g'].name}")

    logger.log(f"开始写出文件: {output_paths['45g'].name}")
    table_45g.to_excel(output_paths["45g"], index=False)
    progress.update(100, f"已生成: {output_paths['45g'].name}")

    elapsed_seconds = time.perf_counter() - started_at
    logger.log(f"5G表记录数: {len(table_5g)}")
    logger.log(f"4G表记录数: {len(table_4g)}")
    logger.log(f"45G总表记录数: {len(table_45g)}")
    logger.log(f"总耗时: {elapsed_seconds:.2f} 秒")
    logger.log("全部处理完成")
    return table_5g, table_4g, table_45g


def main() -> None:
    run_pipeline()


class CapacityGuiApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("容量表合成工具")
        self.root.geometry("920x620")
        self.root.minsize(760, 520)

        self.message_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None

        self.status_var = tk.StringVar(value="就绪")
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text_var = tk.StringVar(value="0%")
        self.output_names_var = tk.StringVar(value=self._format_output_names())

        self._build_ui()
        self.root.after(100, self._poll_queue)

    def _format_output_names(self, timestamp: str | None = None) -> str:
        output_paths = build_output_paths(timestamp)
        return f"输出文件: {output_paths['5g'].name} / {output_paths['4g'].name} / {output_paths['45g'].name}"

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(3, weight=1)

        title = ttk.Label(container, text="容量表 / 物理表 合成工具", font=("PingFang SC", 18, "bold"))
        title.grid(row=0, column=0, sticky="w")

        desc = ttk.Label(
            container,
            text=f"数据目录: {DATA_DIR}",
        )
        desc.grid(row=1, column=0, sticky="we", pady=(8, 4))

        self.outputs_label = ttk.Label(
            container,
            textvariable=self.output_names_var,
        )
        self.outputs_label.grid(row=2, column=0, sticky="we", pady=(0, 14))

        controls = ttk.Frame(container)
        controls.grid(row=3, column=0, sticky="we")
        controls.columnconfigure(1, weight=1)

        self.start_button = ttk.Button(controls, text="开始生成", command=self.start)
        self.start_button.grid(row=0, column=0, sticky="w")

        self.progress_bar = ttk.Progressbar(
            controls,
            mode="determinate",
            maximum=100,
            variable=self.progress_var,
        )
        self.progress_bar.grid(row=0, column=1, sticky="we", padx=(12, 8))

        self.progress_percent_label = ttk.Label(controls, textvariable=self.progress_text_var, width=6, anchor="e")
        self.progress_percent_label.grid(row=0, column=2, sticky="e")

        self.status_label = ttk.Label(container, textvariable=self.status_var)
        self.status_label.grid(row=4, column=0, sticky="nw", pady=(12, 8))

        self.log_box = ScrolledText(container, wrap="word", font=("Menlo", 12), state="disabled")
        self.log_box.grid(row=5, column=0, sticky="nsew")
        container.rowconfigure(5, weight=1)

    def append_log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_progress(self, value: int, message: str) -> None:
        self.progress_var.set(value)
        self.progress_text_var.set(f"{int(value)}%")
        self.status_var.set(message)

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("提示", "任务正在执行中，请稍候。")
            return

        self.progress_var.set(0)
        self.progress_text_var.set("0%")
        self.status_var.set("准备开始")
        self.output_names_var.set(self._format_output_names())
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self.start_button.configure(state="disabled")

        self.worker = threading.Thread(target=self._run_task, daemon=True)
        self.worker.start()

    def _run_task(self) -> None:
        try:
            run_pipeline(
                progress_callback=lambda value, message: self.message_queue.put(("progress", (value, message))),
                log_callback=lambda message: self.message_queue.put(("log", message)),
            )
            self.message_queue.put(("done", None))
        except Exception as exc:
            self.message_queue.put(("error", str(exc)))

    def _poll_queue(self) -> None:
        while not self.message_queue.empty():
            message_type, payload = self.message_queue.get()
            if message_type == "progress":
                value, message = payload
                self.set_progress(value, message)
            elif message_type == "log":
                message = str(payload)
                self.append_log(message)
                if message.startswith("本次输出时间戳: "):
                    timestamp = message.removeprefix("本次输出时间戳: ").strip()
                    self.output_names_var.set(self._format_output_names(timestamp))
                elif message.startswith("未从周表中识别到开始/结束时间"):
                    self.output_names_var.set(self._format_output_names())
            elif message_type == "done":
                self.start_button.configure(state="normal")
                messagebox.showinfo("完成", "容量表已生成完成。")
            elif message_type == "error":
                self.start_button.configure(state="normal")
                self.status_var.set("执行失败")
                self.append_log(f"错误: {payload}")
                messagebox.showerror("执行失败", str(payload))
        self.root.after(100, self._poll_queue)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    CapacityGuiApp().run()
