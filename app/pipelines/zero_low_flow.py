"""零/低流量风险小区分析 — 共用逻辑（4G + 5G）。

规则（按自然日计数，≥2 天即为"连续"）：
  4G：
    零流量：日流量 = 0，连续 ≥ 2 天
    低流量：日流量 ∈ (0, 0.1] GB，连续 ≥ 2 天
  5G：
    零流量：日流量 = 0 或为空，连续 ≥ 2 天
    低流量：日流量 ∈ (0, 3) GB，连续 ≥ 2 天

流程：
  1. 从 DuckDB 读 4G/5G 日表和周表
  2. 计算每小区每天的日流量
  3. 按规则标注零流量/低流量
  4. 输出全量和风险子集 Excel
"""

# 业务规则说明：
# 零低流量分析以“日期 × 小区”为粒度，按最新日期向历史日期回溯连续天数。
# 4G 低流量阈值为 0.1GB，5G 为 3GB；显式 file_paths 表示用户选择的输入集合，
# 未传入时才扫描 DATA_DIR。路径安全校验在 jobs.py 完成，管线只负责筛选和计算。

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.dataframe import dataframe_to_rows

from app.pipelines.core import (
    BASE_DIR,
    DATA_DIR,
    GuiLogger,
    GuiProgress,
    LogCallback,
    ProgressCallback,
    SourceFileError,
)

# 4G 低流量阈值
LOW_THRESHOLD_4G_GB = 0.1  # 100MB = 0.1GB
# 5G 低流量阈值
LOW_THRESHOLD_5G_GB = 3.0  # 3GB

# 输出文件名前缀
OUTPUT_PREFIX_4G = "4G日监控_零低流量风险小区"
OUTPUT_PREFIX_5G = "5G日监控_零低流量风险小区"

# 4G 天流量文件名模式
DAY_FLOW_PATTERN_4G = "重要场景-天*.xlsx"
# 5G 天流量文件名模式
DAY_FLOW_PATTERN_5G = "5G小区容量(天)*.xlsx"

PROBLEM_FILE_NAMES = ("问题小区问题归类.xlsx",)

RISK_COLORS = {
    "严重": ("C00000", "FFFFFF"),
    "高危": ("FF0000", "FFFFFF"),
    "中危": ("E36C0A", "FFFFFF"),
    "预警": ("FFC000", "000000"),
    "关注": ("FFFF00", "000000"),
    "正常": ("FFFFFF", "000000"),
}

RISK_ORDER = {"严重": 0, "高危": 1, "中危": 2, "预警": 3, "关注": 4, "正常": 5}


# ---------- 通用文件发现 ----------

def find_day_flow_files(data_dir: Path | None = None, pattern: str = DAY_FLOW_PATTERN_4G) -> list[Path]:
    """查找指定模式的天流量文件。"""
    root = Path(data_dir) if data_dir else DATA_DIR
    files = sorted(
        p for p in root.glob(pattern) if p.is_file() and not p.name.startswith(".~")
    )
    return files


def _select_day_flow_files(
    data_dir: Path | None,
    file_paths: list[Path] | None,
    pattern: str,
) -> list[Path]:
    if file_paths is not None:
        root = Path(data_dir) if data_dir else DATA_DIR
        files = sorted(
            path for path in file_paths
            if path.is_file()
            and path.parent == root
            and path.match(pattern)
            and not path.name.startswith(".~")
        )
        return files
    return find_day_flow_files(data_dir, pattern)


def find_day_flow_files_4g(data_dir: Path | None = None) -> list[Path]:
    """查找 4G 天流量文件。"""
    root = Path(data_dir) if data_dir else DATA_DIR
    files = find_day_flow_files(root, DAY_FLOW_PATTERN_4G)
    if not files:
        raise SourceFileError(f"未找到 4G 天流量文件: {root / DAY_FLOW_PATTERN_4G}")
    return files


def find_day_flow_files_5g(data_dir: Path | None = None) -> list[Path]:
    """查找 5G 天流量文件。"""
    files = find_day_flow_files(data_dir, DAY_FLOW_PATTERN_5G)
    return files  # 5G 文件不存在时返回空列表，不报错


def find_problem_file(data_dir: Path | None = None) -> Path | None:
    roots = [data_dir or DATA_DIR, BASE_DIR]
    for root in roots:
        for name in PROBLEM_FILE_NAMES:
            path = root / name
            if path.is_file():
                return path
    return None


def load_and_merge_4g(file_paths: list[Path], logger: GuiLogger) -> pd.DataFrame:
    """加载并合并 4G 天流量文件。"""
    frames: list[pd.DataFrame] = []
    for fp in file_paths:
        xl = pd.ExcelFile(fp)
        sheet = xl.sheet_names[0]
        df = pd.read_excel(fp, sheet_name=sheet)
        df["__源文件__"] = fp.name
        frames.append(df)
        cgi_n = df["CGI"].nunique() if "CGI" in df.columns else 0
        logger.log(f"  加载: {fp.name} => {len(df)} 行, {cgi_n} 个小区")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_and_merge_5g(file_paths: list[Path], logger: GuiLogger) -> pd.DataFrame:
    """加载并合并 5G 天流量文件。"""
    frames: list[pd.DataFrame] = []
    for fp in file_paths:
        xl = pd.ExcelFile(fp)
        sheet = xl.sheet_names[0]
        df = pd.read_excel(fp, sheet_name=sheet)
        df["__源文件__"] = fp.name
        frames.append(df)
        ncgi_n = df["NCGI"].nunique() if "NCGI" in df.columns else 0
        logger.log(f"  加载: {fp.name} => {len(df)} 行, {ncgi_n} 个小区")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_problems(problem_file: Path | None, logger: GuiLogger) -> pd.DataFrame | None:
    if problem_file is None or not problem_file.is_file():
        logger.log("[警告] 未找到问题小区归类文件，跳过关联")
        return None
    xl = pd.ExcelFile(problem_file)
    df = pd.read_excel(problem_file, sheet_name=xl.sheet_names[0])
    if "CGI" not in df.columns or "问题" not in df.columns:
        logger.log(f"[警告] 问题文件缺少 CGI/问题 列: {problem_file.name}")
        return None
    df = df.copy()
    df["问题"] = df["问题"].apply(
        lambda x: "" if pd.isna(x) or str(x).strip() == "0" else str(x).strip()
    )
    logger.log(
        f"  加载问题小区: {problem_file.name} => {len(df)} 行, "
        f"有效问题数: {(df['问题'] != '').sum()}"
    )
    return df[["CGI", "问题"]]


# ---------- 连续天数计算 ----------

def calc_consecutive(
    row: pd.Series,
    date_cols: list[str],
    mode: str = "zero",
    low_threshold_gb: float = LOW_THRESHOLD_4G_GB,
) -> int:
    """计算连续零/低流量天数（从最新一天往回数）。

    零流量：值为 0 或 NaN 为空，连续 ≥ 2 天
    低流量：值在 (0, low_threshold_gb) 之间，连续 ≥ 2 天
    """
    count = 0
    for d in reversed(date_cols):
        v = row.get(d)
        if mode == "zero":
            # 零流量：0 或为空(NaN)
            if pd.isna(v) or v == 0:
                count += 1
            else:
                break
        elif mode == "low":
            # 低流量：非空且非零，且小于阈值
            if pd.isna(v) or v == 0:
                break  # 遇到空或零则停止
            if v > 0 and v < low_threshold_gb:
                count += 1
            else:
                break
    return count


def risk_level(zd: int, ld: int) -> str:
    if zd >= 7:
        return "严重"
    if zd >= 5 or ld >= 7:
        return "高危"
    if zd >= 3 or ld >= 5:
        return "中危"
    if zd >= 1 or ld >= 3:
        return "预警"
    if ld >= 1:
        return "关注"
    return "正常"


# ---------- Excel 报告 ----------

def _style_header(cell) -> None:
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor="366092")
    cell.alignment = Alignment(horizontal="center")


def write_report(
    pivot: pd.DataFrame,
    dates: list,
    monitor_date,
    summary_stats: dict[str, Any],
    output_file: Path,
    id_col: str = "CGI",
) -> None:
    wb = Workbook()
    date_cols = [str(d) for d in dates]
    risk_counts = summary_stats["risk_counts"]

    # Sheet1: 监控汇总
    ws1 = wb.active
    ws1.title = "监控汇总"
    summary_rows = [
        ["监控指标", "数值"],
        ["监控日期", str(monitor_date)],
        ["数据范围", f"{dates[0]} 至 {dates[-1]}（共{len(dates)}天）"],
        ["小区总数", summary_stats["total"]],
        ["当日零流量小区数", summary_stats["zero_today"]],
        ["当日低流量小区数", summary_stats["low_today"]],
        ["当日零低流量小区数", summary_stats["zero_low_today"]],
        ["当日零低流量占比", f"{summary_stats['ratio']:.4%}"],
        ["已关联已知问题小区数", summary_stats.get("with_problem", 0)],
        ["", ""],
        ["风险等级", "小区数"],
    ]
    for lv in ["严重", "高危", "中危", "预警", "关注", "正常"]:
        summary_rows.append([lv, risk_counts.get(lv, 0)])

    for r_idx, row in enumerate(summary_rows, 1):
        for c_idx, val in enumerate(row, 1):
            cell = ws1.cell(r_idx, c_idx, val)
            if r_idx == 1 or (r_idx == 11 and c_idx == 1):
                _style_header(cell)
            elif c_idx == 1:
                cell.font = Font(bold=True)
            elif r_idx == 8 and c_idx == 2:
                cell.font = Font(bold=True, color="C00000")
                cell.fill = PatternFill("solid", fgColor="FFF2CC")
    ws1.column_dimensions["A"].width = 30
    ws1.column_dimensions["B"].width = 22

    # Sheet2: 风险小区明细
    ws2 = wb.create_sheet("风险小区明细")
    risk_df = pivot[pivot["当日状态"].isin(["零流量", "低流量"])].copy()
    risk_df["_risk_rank"] = risk_df["风险等级"].map(RISK_ORDER).fillna(99)
    risk_df = risk_df.sort_values(["_risk_rank", "连续零低流量天数"], ascending=[True, False])

    out_cols = [
        id_col,
        "小区名称",
        "所属地市",
        "当日流量_GB",
        "当日状态",
        "连续零流量天数",
        "连续低流量天数",
        "连续零低流量天数",
        "风险等级",
        "问题",
    ]
    risk_out = risk_df[out_cols].copy()
    risk_out.columns = [
        id_col,
        "小区名称",
        "所属地市",
        "当日流量(GB)",
        "当日状态",
        "连续零流量天数",
        "连续低流量天数",
        "连续零低流量天数",
        "风险等级",
        "已知问题",
    ]

    for r_idx, row in enumerate(dataframe_to_rows(risk_out, index=False, header=True), 1):
        for c_idx, val in enumerate(row, 1):
            cell = ws2.cell(r_idx, c_idx, val)
            if r_idx == 1:
                _style_header(cell)
            else:
                if c_idx == 9:
                    bg, fg = RISK_COLORS.get(val, ("FFFFFF", "000000"))
                    cell.fill = PatternFill("solid", fgColor=bg)
                    cell.font = Font(bold=True, color=fg)
                elif c_idx == 10 and val:
                    cell.font = Font(color="C00000")
                    cell.fill = PatternFill("solid", fgColor="FFF2CC")
                elif r_idx % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor="F7F9FC")

    for col in ["A", "B", "C"]:
        ws2.column_dimensions[col].width = 26
    for col in ["D", "E", "F", "G", "H", "I"]:
        ws2.column_dimensions[col].width = 16
    ws2.column_dimensions["J"].width = 40

    # Sheet3: 全量监控明细
    ws3 = wb.create_sheet("全量监控明细")
    all_out = pivot[
        [id_col, "小区名称", "所属地市"]
        + date_cols
        + [
            "当日流量_GB",
            "当日状态",
            "连续零流量天数",
            "连续低流量天数",
            "连续零低流量天数",
            "风险等级",
            "问题",
        ]
    ].copy()
    all_out.columns = (
        [id_col, "小区名称", "所属地市"]
        + [f"{d}流量(GB)" for d in dates]
        + [
            "当日流量(GB)",
            "当日状态",
            "连续零流量天数",
            "连续低流量天数",
            "连续零低流量天数",
            "风险等级",
            "已知问题",
        ]
    )

    risk_col_idx = len(all_out.columns) - 1
    problem_col_idx = len(all_out.columns)

    for r_idx, row in enumerate(dataframe_to_rows(all_out, index=False, header=True), 1):
        for c_idx, val in enumerate(row, 1):
            cell = ws3.cell(r_idx, c_idx, val)
            if r_idx == 1:
                _style_header(cell)
            else:
                if c_idx == risk_col_idx:
                    bg, fg = RISK_COLORS.get(val, ("FFFFFF", "000000"))
                    cell.fill = PatternFill("solid", fgColor=bg)
                    cell.font = Font(bold=True, color=fg)
                elif c_idx == problem_col_idx and val:
                    cell.font = Font(color="C00000")
                    cell.fill = PatternFill("solid", fgColor="FFF2CC")
                elif r_idx % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor="F7F9FC")

    for col in ["A", "B", "C"]:
        ws3.column_dimensions[col].width = 26
    for c in range(4, 4 + len(dates)):
        ws3.column_dimensions[get_column_letter(c)].width = 16
    for c in range(4 + len(dates), problem_col_idx + 1):
        ws3.column_dimensions[get_column_letter(c)].width = 16
    ws3.column_dimensions[get_column_letter(problem_col_idx)].width = 40

    wb.save(output_file)
    wb.close()


# ---------- 4G 管线 ----------

def run_4g_zero_low_flow_pipeline(
    progress_callback: ProgressCallback | None = None,
    log_callback: LogCallback | None = None,
    data_dir: Path | str | None = None,
    output_dir: Path | str | None = None,
    file_paths: list[Path] | None = None,
) -> Path:
    """运行 4G 零低流量风险小区分析，返回输出 Excel 路径。"""
    logger = GuiLogger(log_callback)
    progress = GuiProgress(progress_callback, logger)
    data_root = Path(data_dir) if data_dir else DATA_DIR
    out_root = Path(output_dir) if output_dir else BASE_DIR

    logger.log("=" * 50)
    logger.log("4G日监控 - 零低流量风险小区分析")
    logger.log("=" * 50)

    progress.update(5, f"读取数据目录: {data_root}")
    file_paths = _select_day_flow_files(data_root, file_paths, DAY_FLOW_PATTERN_4G)
    if not file_paths:
        raise SourceFileError(f"未找到 4G 天流量文件: {data_root / DAY_FLOW_PATTERN_4G}")
    raw_df = load_and_merge_4g(file_paths, logger)
    required = ["CGI", "小区名称", "所属地市", "记录开始时间", "日4G流量（GB）"]
    missing = [c for c in required if c not in raw_df.columns]
    if missing:
        raise SourceFileError(f"天流量文件缺少必要列: {', '.join(missing)}")
    logger.log(f"合并完成: {len(raw_df)} 行, {raw_df['CGI'].nunique()} 个唯一小区")

    progress.update(20, "读取问题小区归类...")
    problem_df = load_problems(find_problem_file(data_root), logger)

    progress.update(35, "数据预处理...")
    raw_df = raw_df.copy()
    raw_df["日期"] = pd.to_datetime(raw_df["记录开始时间"]).dt.date
    raw_df["流量_GB"] = pd.to_numeric(raw_df["日4G流量（GB）"], errors="coerce")
    dates = sorted(raw_df["日期"].unique())
    if not dates:
        raise SourceFileError("天流量数据中没有有效日期")
    monitor_date = dates[-1]
    output_file = out_root / f"{OUTPUT_PREFIX_4G}_{monitor_date}.xlsx"
    logger.log(f"数据日期范围: {dates[0]} 至 {dates[-1]}, 共 {len(dates)} 天")
    logger.log(f"监控日期(最新): {monitor_date}")

    progress.update(55, "构建透视表并计算风险...")
    pivot = (
        raw_df.pivot_table(
            index=["CGI", "小区名称", "所属地市"],
            columns="日期",
            values="流量_GB",
            aggfunc="first",
        )
        .reset_index()
    )
    pivot.columns.name = None
    date_cols = [str(d) for d in dates]
    for d in dates:
        pivot = pivot.rename(columns={d: str(d)})

    pivot["当日流量_GB"] = pivot[str(monitor_date)]
    pivot["当日状态"] = pivot["当日流量_GB"].apply(
        lambda x: "零流量"
        if pd.isna(x) or x == 0
        else ("低流量" if x < LOW_THRESHOLD_4G_GB else "正常")
    )
    pivot["连续零流量天数"] = pivot.apply(
        lambda r: calc_consecutive(r, date_cols, "zero", LOW_THRESHOLD_4G_GB), axis=1
    )
    pivot["连续低流量天数"] = pivot.apply(
        lambda r: calc_consecutive(r, date_cols, "low", LOW_THRESHOLD_4G_GB), axis=1
    )
    pivot["连续零低流量天数"] = pivot[["连续零流量天数", "连续低流量天数"]].max(axis=1)
    pivot["风险等级"] = pivot.apply(
        lambda r: risk_level(r["连续零流量天数"], r["连续低流量天数"]), axis=1
    )

    if problem_df is not None:
        pivot = pivot.merge(problem_df, on="CGI", how="left")
        pivot["问题"] = pivot["问题"].fillna("")
    else:
        pivot["问题"] = ""

    total = len(pivot)
    zero_today = int((pivot["当日状态"] == "零流量").sum())
    low_today = int((pivot["当日状态"] == "低流量").sum())
    zero_low_today = zero_today + low_today
    ratio = zero_low_today / total if total else 0.0
    risk_counts = pivot["风险等级"].value_counts().to_dict()
    with_problem = int((pivot["问题"] != "").sum())

    logger.log(f"小区总数: {total}")
    logger.log(f"当日零流量: {zero_today}, 当日低流量: {low_today}, 合计: {zero_low_today}")
    logger.log(f"当日零低流量占比: {ratio:.4%}")
    logger.log(f"风险分布: {dict(sorted(risk_counts.items(), key=lambda kv: -kv[1]))}")
    logger.log(f"已关联已知问题: {with_problem} 个小区")

    progress.update(85, "生成监控报表...")
    summary_stats = {
        "total": total,
        "zero_today": zero_today,
        "low_today": low_today,
        "zero_low_today": zero_low_today,
        "ratio": ratio,
        "with_problem": with_problem,
        "risk_counts": risk_counts,
    }
    write_report(pivot, dates, monitor_date, summary_stats, output_file, id_col="CGI")
    logger.log(f"报表已保存: {output_file.name}")
    progress.update(100, f"已生成: {output_file.name}")
    return output_file


# ---------- 5G 管线 ----------

def run_5g_zero_low_flow_pipeline(
    progress_callback: ProgressCallback | None = None,
    log_callback: LogCallback | None = None,
    data_dir: Path | str | None = None,
    output_dir: Path | str | None = None,
    file_paths: list[Path] | None = None,
) -> Path | None:
    """运行 5G 零低流量风险小区分析，返回输出 Excel 路径。

    5G 规则：
      零流量：日流量 = 0 或为空，连续 ≥ 2 天
      低流量：日流量 ∈ (0, 3) GB，连续 ≥ 2 天

    如果 data/ 下没有 5G 天流量文件，返回 None。
    """
    logger = GuiLogger(log_callback)
    progress = GuiProgress(progress_callback, logger)
    data_root = Path(data_dir) if data_dir else DATA_DIR
    out_root = Path(output_dir) if output_dir else BASE_DIR

    logger.log("=" * 50)
    logger.log("5G日监控 - 零低流量风险小区分析")
    logger.log("=" * 50)

    progress.update(5, f"读取数据目录: {data_root}")
    file_paths = _select_day_flow_files(data_root, file_paths, DAY_FLOW_PATTERN_5G)
    if not file_paths:
        logger.log("[提示] 未找到 5G 天流量文件，跳过 5G 分析")
        return None

    raw_df = load_and_merge_5g(file_paths, logger)
    required = ["NCGI", "记录开始时间"]
    # 5G 日流量列名可能为「日RLC层上下行总流量(G)」或「日RLC层上下行总流量」
    flow_col = None
    for candidate in ["日RLC层上下行总流量(G)", "日RLC层上下行总流量", "日流量(G)"]:
        if candidate in raw_df.columns:
            flow_col = candidate
            break
    if flow_col is None:
        raise SourceFileError("5G 天流量文件缺少流量列（如「日RLC层上下行总流量(G)」）")
    missing = [c for c in required if c not in raw_df.columns]
    if missing:
        raise SourceFileError(f"5G 天流量文件缺少必要列: {', '.join(missing)}")
    logger.log(f"合并完成: {len(raw_df)} 行, {raw_df['NCGI'].nunique()} 个唯一小区")

    # 尝试获取小区名称列（可选）
    name_col = "小区名称" if "小区名称" in raw_df.columns else None
    # 尝试获取地市列（可选，可能来自工参或周表）
    city_col = "所属地市" if "所属地市" in raw_df.columns else None

    progress.update(20, "读取问题小区归类...")
    problem_df = load_problems(find_problem_file(data_root), logger)

    progress.update(35, "数据预处理...")
    raw_df = raw_df.copy()
    raw_df["日期"] = pd.to_datetime(raw_df["记录开始时间"]).dt.date
    raw_df["流量_GB"] = pd.to_numeric(raw_df[flow_col], errors="coerce")
    dates = sorted(raw_df["日期"].unique())
    if not dates:
        raise SourceFileError("5G 天流量数据中没有有效日期")
    monitor_date = dates[-1]
    output_file = out_root / f"{OUTPUT_PREFIX_5G}_{monitor_date}.xlsx"
    logger.log(f"数据日期范围: {dates[0]} 至 {dates[-1]}, 共 {len(dates)} 天")
    logger.log(f"监控日期(最新): {monitor_date}")

    progress.update(55, "构建透视表并计算风险...")
    # 构建索引列
    index_cols = ["NCGI"]
    if name_col:
        index_cols.append(name_col)
    if city_col:
        index_cols.append(city_col)

    pivot = (
        raw_df.pivot_table(
            index=index_cols,
            columns="日期",
            values="流量_GB",
            aggfunc="first",
        )
        .reset_index()
    )
    pivot.columns.name = None
    date_cols = [str(d) for d in dates]
    for d in dates:
        pivot = pivot.rename(columns={d: str(d)})

    # 确保有小区名称和地市列
    if name_col not in pivot.columns:
        pivot["小区名称"] = ""
    if city_col not in pivot.columns:
        pivot["所属地市"] = ""

    pivot["当日流量_GB"] = pivot[str(monitor_date)]
    pivot["当日状态"] = pivot["当日流量_GB"].apply(
        lambda x: "零流量"
        if pd.isna(x) or x == 0
        else ("低流量" if x < LOW_THRESHOLD_5G_GB else "正常")
    )
    pivot["连续零流量天数"] = pivot.apply(
        lambda r: calc_consecutive(r, date_cols, "zero", LOW_THRESHOLD_5G_GB), axis=1
    )
    pivot["连续低流量天数"] = pivot.apply(
        lambda r: calc_consecutive(r, date_cols, "low", LOW_THRESHOLD_5G_GB), axis=1
    )
    pivot["连续零低流量天数"] = pivot[["连续零流量天数", "连续低流量天数"]].max(axis=1)
    pivot["风险等级"] = pivot.apply(
        lambda r: risk_level(r["连续零流量天数"], r["连续低流量天数"]), axis=1
    )

    if problem_df is not None:
        # 5G 使用 NCGI 列匹配，4G 使用 CGI
        pivot = pivot.merge(problem_df, left_on="NCGI", right_on="CGI", how="left")
        pivot["问题"] = pivot["问题"].fillna("")
        if "CGI_y" in pivot.columns:
            pivot.drop(columns=["CGI_y"], inplace=True)
            pivot.rename(columns={"CGI_x": "CGI"}, inplace=True)
    else:
        pivot["问题"] = ""

    total = len(pivot)
    zero_today = int((pivot["当日状态"] == "零流量").sum())
    low_today = int((pivot["当日状态"] == "低流量").sum())
    zero_low_today = zero_today + low_today
    ratio = zero_low_today / total if total else 0.0
    risk_counts = pivot["风险等级"].value_counts().to_dict()

    logger.log(f"小区总数: {total}")
    logger.log(f"当日零流量: {zero_today}, 当日低流量: {low_today}, 合计: {zero_low_today}")
    logger.log(f"当日零低流量占比: {ratio:.4%}")
    logger.log(f"风险分布: {dict(sorted(risk_counts.items(), key=lambda kv: -kv[1]))}")

    progress.update(85, "生成监控报表...")
    summary_stats = {
        "total": total,
        "zero_today": zero_today,
        "low_today": low_today,
        "zero_low_today": zero_low_today,
        "ratio": ratio,
        "with_problem": 0,
        "risk_counts": risk_counts,
    }
    write_report(pivot, dates, monitor_date, summary_stats, output_file, id_col="NCGI")
    logger.log(f"报表已保存: {output_file.name}")
    progress.update(100, f"已生成: {output_file.name}")
    return output_file


# ---------- 统一入口（向后兼容） ----------

def run_zero_low_flow_pipeline(
    progress_callback: ProgressCallback | None = None,
    log_callback: LogCallback | None = None,
    data_dir: Path | str | None = None,
    output_dir: Path | str | None = None,
    net_type: str = "4g",
    file_paths: list[Path] | None = None,
) -> Path | None:
    """统一入口：运行零低流量风险小区分析。

    net_type: "4g" | "5g" | "all"
      - "4g"：仅 4G 分析
      - "5g"：仅 5G 分析
      - "all"：同时运行 4G 和 5G
    """
    if net_type == "4g":
        return run_4g_zero_low_flow_pipeline(
            progress_callback=progress_callback,
            log_callback=log_callback,
            data_dir=data_dir,
            output_dir=output_dir,
            file_paths=file_paths,
        )
    elif net_type == "5g":
        return run_5g_zero_low_flow_pipeline(
            progress_callback=progress_callback,
            log_callback=log_callback,
            data_dir=data_dir,
            output_dir=output_dir,
            file_paths=file_paths,
        )
    else:  # "all"
        result_4g = run_4g_zero_low_flow_pipeline(
            progress_callback=progress_callback,
            log_callback=log_callback,
            data_dir=data_dir,
            output_dir=output_dir,
            file_paths=file_paths,
        )
        result_5g = run_5g_zero_low_flow_pipeline(
            progress_callback=progress_callback,
            log_callback=log_callback,
            data_dir=data_dir,
            output_dir=output_dir,
            file_paths=file_paths,
        )
        return result_5g or result_4g


# ---------- 最新输出文件查找 ----------

def latest_zero_low_flow_output(net_type: str = "4g") -> Path | None:
    """查找最新的零低流量分析输出文件。"""
    if net_type == "5g":
        prefix = OUTPUT_PREFIX_5G
    else:
        prefix = OUTPUT_PREFIX_4G
    files = sorted(
        BASE_DIR.glob(f"{prefix}_*.xlsx"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


__all__ = [
    "LOW_THRESHOLD_4G_GB",
    "LOW_THRESHOLD_5G_GB",
    "OUTPUT_PREFIX_4G",
    "OUTPUT_PREFIX_5G",
    "DAY_FLOW_PATTERN_4G",
    "DAY_FLOW_PATTERN_5G",
    "find_day_flow_files_4g",
    "find_day_flow_files_5g",
    "latest_zero_low_flow_output",
    "run_4g_zero_low_flow_pipeline",
    "run_5g_zero_low_flow_pipeline",
    "run_zero_low_flow_pipeline",
]
