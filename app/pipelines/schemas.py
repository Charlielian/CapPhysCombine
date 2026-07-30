"""源 Excel 列校验：在导入 DuckDB 前给出清晰的缺列报告。

pandera 是可选依赖；未安装时退化为简单的列存在性检查。
所有 schema 只声明「必需列」，不约束类型（Excel 来源类型不稳定）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import pandas as pd

from app.pipelines.logging_util import GuiLogger
from app.pipelines.paths import FILE_PATTERNS, PHYSICAL_FILE_PATTERNS

try:  # 可选：pandera 提供更丰富的错误信息
    import pandera.pandas as pa  # type: ignore

    _HAS_PANDERA = True
except ImportError:  # pragma: no cover
    try:
        import pandera as pa  # type: ignore

        _HAS_PANDERA = True
    except ImportError:
        pa = None  # type: ignore
        _HAS_PANDERA = False


@dataclass(frozen=True)
class SourceSchema:
    """单个源表的列约束。"""

    key: str  # FILE_PATTERNS / PHYSICAL_FILE_PATTERNS 中的 key
    label: str
    required: tuple[str, ...]  # 必需列（缺失即报错）
    optional: tuple[str, ...] = field(default_factory=tuple)  # 可选但常出现的列
    # 列名等价别名：源 Excel 可能用任一名字
    aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)


# ==============================================================================
# 容量表源 schema
# ==============================================================================

_5G_WEEK_SCHEMA = SourceSchema(
    key="5g_week",
    label="5G 小区容量(周)",
    required=("NCGI",),
    optional=("记录开始时间", "记录结束时间", "小区名称", "使用频段", "覆盖类型", "网元状态"),
)

_5G_DAY_SCHEMA = SourceSchema(
    key="5g_day",
    label="5G 小区容量(天)",
    required=(
        "NCGI",
        "记录开始时间",
    ),
    aliases={
        "忙时小区PRB利用率": ("忙时小区PRB利用率(%)",),
    },
    optional=(
        "日RLC层上下行总流量(G)",
        "忙时上行PRB平均利用率(%)",
        "忙时下行PRB平均利用率(%)",
        "忙时PDCCH信道CCE占用率(%)",
        "RRC连接最大数-忙时",
        "RRC连接平均数-忙时",
        "忙时RLC层上行业务字节数(G)",
        "忙时RLC层下行业务字节数(G)",
    ),
)

_5G_MR_SCHEMA = SourceSchema(
    key="5g_mr",
    label="5G MR 覆盖",
    required=(
        "小区NCGI",
        "移动RSRP采样的总采样点",
        "移动RSRP采样强于-110采样点",
        "移动平均TA(M)",
    ),
)

_5G_KPI_SCHEMA = SourceSchema(
    key="5g_kpi",
    label="5G KPI 报表",
    required=("NCGI", "VoNR语音话务量"),
)

_4G_WEEK_SCHEMA = SourceSchema(
    key="4g_week",
    label="4G 重要场景(周)",
    required=("CGI",),
    aliases={"自忙时上行PRB平均利用率": ("自忙时上行PRB平均利用率(%)",)},
    optional=(
        "记录开始时间",
        "记录结束时间",
        "自忙时上行PRB平均利用率",
        "自忙时下行PRB平均利用率",
        "自忙时PDCCH信道CCE占用率",
        "自忙时RRC连接最大数",
        "自忙时有效RRC连接最大数",
        "自忙时有效RRC连接平均数",
        "自忙时空口上行业务字节数",
        "自忙时空口下行业务字节数",
        "日4G流量（GB）",
    ),
)

_4G_DAY_SCHEMA = SourceSchema(
    key="4g_day",
    label="4G 重要场景(天)",
    required=(
        "CGI",
        "记录开始时间",
        "自忙时上行PRB平均利用率",
        "自忙时下行PRB平均利用率",
        "日4G流量（GB）",
    ),
)

_4G_MR_SCHEMA = SourceSchema(
    key="4g_mr",
    label="4G MR 覆盖",
    required=(
        "cgi",
        "MRO移动总采样点",
        "MRO移动大于等于负110DBM的采样点数",
        "平均TA",
    ),
)

_COG_COVERAGE_SCHEMA = SourceSchema(
    key="cog_coverage",
    label="共站同覆盖(可选)",
    required=("CGI", "共站同覆盖名"),
    optional=(
        "物理站名",
        "小区名称",
        "使用频段",
        "是否覆盖层",
        "覆盖层",
        "小区所属区域",
        "路测网格",
        "经度",
        "纬度",
    ),
)

# ==============================================================================
# 物理表源 schema
# ==============================================================================

_NR_CELLANT_SCHEMA = SourceSchema(
    key="nr_cellant",
    label="5G 工参 (*_nr_*.xlsx)",
    required=(
        "CGI",
        "小区名称",
        "所属局站",
        "所属局站ID",
        "经度",
        "纬度",
        "方位角",
        "天线名称",
        "挂高",
        "厂家",
        "使用频段",
        "详细使用频段",
        "站点类型",
        "网元状态",
        "覆盖类型",
        "乡镇街道",
        "一级标签",
        "路测网格",
    ),
)

_LTE_CELLANT_SCHEMA = SourceSchema(
    key="lte_cellant",
    label="4G 工参 (*_lte_*.xlsx)",
    required=(
        "CGI",
        "小区名称",
        "所属站点名称",
        "站点ID",
        "经度",
        "纬度",
        "方位角",
        "天线名称",
        "挂高",
        "厂家",
        "网络制式",
        "详细使用频段",
        "站点类型",
        "网元状态",
        "覆盖类型",
        "乡镇街道",
        "一级标签",
        "路测网格",
    ),
    aliases={"中心载频的信道号": ("中心载频的信道号",), "下行中心载频的信道号": ("下行中心载频的信道号",)},
)

CAPACITY_SCHEMAS: dict[str, SourceSchema] = {
    s.key: s
    for s in (
        _5G_WEEK_SCHEMA,
        _5G_DAY_SCHEMA,
        _5G_MR_SCHEMA,
        _5G_KPI_SCHEMA,
        _4G_WEEK_SCHEMA,
        _4G_DAY_SCHEMA,
        _4G_MR_SCHEMA,
        _COG_COVERAGE_SCHEMA,
    )
}

PHYSICAL_SCHEMAS: dict[str, SourceSchema] = {
    s.key: s
    for s in (
        _NR_CELLANT_SCHEMA,
        _LTE_CELLANT_SCHEMA,
    )
}

ALL_SCHEMAS: dict[str, SourceSchema] = {**CAPACITY_SCHEMAS, **PHYSICAL_SCHEMAS}


# ==============================================================================
# 校验逻辑
# ==============================================================================


def _resolve_required(
    schema: SourceSchema, available: Iterable[str]
) -> tuple[list[str], list[str]]:
    """返回 (缺失的必需列, 命中别名后的必需列)。"""
    available_set = set(available)
    missing: list[str] = []
    resolved: list[str] = []
    for col in schema.required:
        if col in available_set:
            resolved.append(col)
            continue
        # 尝试别名
        alts = schema.aliases.get(col, ())
        hit = next((a for a in alts if a in available_set), None)
        if hit is not None:
            resolved.append(hit)
        else:
            missing.append(col)
    return missing, resolved


@dataclass
class ValidationResult:
    schema_key: str
    label: str
    file: str
    ok: bool
    missing_required: list[str] = field(default_factory=list)
    available_columns: list[str] = field(default_factory=list)


def validate_dataframe(
    df: pd.DataFrame, schema: SourceSchema, file_name: str = "<df>"
) -> ValidationResult:
    """校验 DataFrame 是否满足 schema 的必需列约束。"""
    available = [str(c) for c in df.columns]
    missing, _ = _resolve_required(schema, available)
    return ValidationResult(
        schema_key=schema.key,
        label=schema.label,
        file=file_name,
        ok=not missing,
        missing_required=missing,
        available_columns=available,
    )


def validate_excel(
    path, schema: SourceSchema
) -> ValidationResult:
    """读 Excel 第一行做列名校验（不全量加载）。优先用 calamine。"""
    from app.pipelines.io import get_excel_engine

    exc_to_return = None
    try:
        eng = get_excel_engine()
        if eng == "calamine":
            from python_calamine import CalamineWorkbook

            wb = CalamineWorkbook.from_path(str(path))
            ws = wb.get_sheet_by_index(0)
            rows_iter = ws.iter_rows(values_only=True)
            headers = next(rows_iter, None) or ()
        else:
            from openpyxl import load_workbook

            wb = load_workbook(filename=str(path), read_only=True, data_only=True)
            ws = wb.active
            headers = next(ws.iter_rows(values_only=True), None) or ()
            wb.close()
    except Exception as exc:
        exc_to_return = exc

    if exc_to_return is not None:
        return ValidationResult(
            schema_key=schema.key,
            label=schema.label,
            file=str(path),
            ok=False,
            missing_required=list(schema.required),
        ).__class__(
            schema_key=schema.key,
            label=schema.label,
            file=str(path),
            ok=False,
            missing_required=[f"读取失败: {exc}"],
        )

    available = [str(h) if h is not None else "" for h in headers]
    missing, _ = _resolve_required(schema, available)
    return ValidationResult(
        schema_key=schema.key,
        label=schema.label,
        file=str(path),
        ok=not missing,
        missing_required=missing,
        available_columns=available,
    )


def validate_data_dir(logger: GuiLogger | None = None) -> dict[str, list[ValidationResult]]:
    """扫描 data/ 目录，对每个匹配的源文件做 schema 校验。"""
    from pathlib import Path

    from app.pipelines.paths import DATA_DIR

    logger = logger or GuiLogger()
    results: dict[str, list[ValidationResult]] = {"capacity": [], "physical": []}

    for key, pattern in FILE_PATTERNS.items():
        schema = CAPACITY_SCHEMAS.get(key)
        if schema is None:
            continue
        for path in sorted(Path(DATA_DIR).glob(pattern)):
            if path.name.startswith(".~"):
                continue
            res = validate_excel(path, schema)
            results["capacity"].append(res)
            if not res.ok:
                logger.log(
                    f"[schema] {res.file} 缺列: {', '.join(res.missing_required)}"
                )

    for key, pattern in PHYSICAL_FILE_PATTERNS.items():
        schema = PHYSICAL_SCHEMAS.get(key)
        if schema is None:
            continue
        for path in sorted(Path(DATA_DIR).glob(pattern)):
            if path.name.startswith(".~"):
                continue
            res = validate_excel(path, schema)
            results["physical"].append(res)
            if not res.ok:
                logger.log(
                    f"[schema] {res.file} 缺列: {', '.join(res.missing_required)}"
                )

    return results


def ensure_columns(df: pd.DataFrame, schema: SourceSchema) -> None:
    """硬性断言：DataFrame 必须满足 schema，否则抛 ValueError。"""
    missing, _ = _resolve_required(schema, df.columns)
    if missing:
        raise ValueError(
            f"[{schema.label}] 缺少必需列: {', '.join(missing)}。"
            f"当前列: {list(df.columns)}"
        )


__all__ = [
    "SourceSchema",
    "ValidationResult",
    "CAPACITY_SCHEMAS",
    "PHYSICAL_SCHEMAS",
    "ALL_SCHEMAS",
    "validate_dataframe",
    "validate_excel",
    "validate_data_dir",
    "ensure_columns",
]
