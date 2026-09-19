"""网管配置（NRM Excel）解析，并同步 PCI / TAC / 频点到物理表。

5G: ssbFrequency(MHz) -> NR-ARFCN = round(MHz * 200)  （FR1 < 3GHz）
4G: earfcnDl(MHz) + freqBandInd -> EARFCN（3GPP TS 36.101）
"""

# 数据同步说明：
# NRM Excel 的字段名和单元格内容可能混有参数描述、单位和空值。本模块先清洗并抽取
# 小区配置，再用 CGI/频点等稳定键匹配统一物理表；匹配不到的记录只计入统计，不覆盖原表。

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

from app.pipelines.cog_db import init_unified_database
from app.pipelines.common import (
    BASE_DIR,
    DATA_DIR,
    GuiLogger,
    GuiProgress,
    LogCallback,
    ProgressCallback,
)
from app.pipelines.io import get_excel_engine, get_unified_db_connection

_META_RE = re.compile(
    r"(long:|double:|stringArray|该参数|默认值|小区复位|单位:|MHz|\[0\.\.|--)",
    re.I,
)

# 3GPP TS 36.101 Table 5.7.3-1（下行）：band -> (F_DL_low_MHz, N_Offs_DL)
LTE_DL_EARFCN_TABLE: dict[int, tuple[float, int]] = {
    1: (2110.0, 0),
    3: (1805.0, 1200),
    5: (869.0, 2400),
    7: (2620.0, 2750),
    8: (925.0, 3450),
    28: (758.0, 9210),
    34: (2010.0, 36200),
    38: (2570.0, 37750),
    39: (1880.0, 38250),
    40: (2300.0, 38650),
    41: (2496.0, 39650),
}

DEFAULT_PLMN = "46000"
NRM_DIR_CANDIDATES = (
    DATA_DIR / "网管配置",
    BASE_DIR / "网管配置",
)


def mhz_to_nr_arfcn(mhz: float | int | str | None) -> int | None:
    """FR1 频点(MHz) -> NR-ARFCN。700M/2.6G: N_REF = round(F_MHz * 200)。"""
    val = clean_num(mhz)
    if val is None:
        return None
    f = float(val)
    if f < 3000:
        return int(round(f * 200))
    return int(round(600000 + (f - 3000) / 0.015))


def mhz_to_lte_earfcn(mhz: float | int | str | None, band: int | str | None) -> int | None:
    """下行中心频点(MHz) + 频段指示 -> EARFCN。"""
    val = clean_num(mhz)
    b = clean_num(band)
    if val is None or b is None:
        return None
    try:
        bi = int(b)
    except (TypeError, ValueError):
        return None
    tip = LTE_DL_EARFCN_TABLE.get(bi)
    if not tip:
        return None
    f_low, n_offs = tip
    return int(round(n_offs + (float(val) - f_low) / 0.1))


def clean_num(s: Any) -> float | int | None:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    text = str(s).strip()
    if not text or text.lower() in ("nan", "none", "-", "--"):
        return None
    if ";" in text:
        text = text.split(";", 1)[0].strip()
    if text.lower().startswith("0x"):
        try:
            return int(text, 16)
        except ValueError:
            return None
    try:
        f = float(text)
        return int(f) if f.is_integer() else f
    except ValueError:
        return None


def plmn_norm(x: Any) -> str | None:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).strip().replace("-", "")
    if _META_RE.search(s) or "Array" in s:
        return None
    m = re.match(r"(\d{3})[-_]?(\d{2,3})", s)
    if m:
        return m.group(1) + m.group(2)[:2]
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 5:
        return digits[:5]
    return None


def make_cgi(plmn: Any, node: Any, cell: Any) -> str | None:
    plmn_s = plmn_norm(plmn) or DEFAULT_PLMN
    try:
        node_i = int(float(str(node)))
        cell_i = int(float(str(cell)))
    except (TypeError, ValueError):
        return None
    return f"{plmn_s[:3]}-{plmn_s[3:]}-{node_i}-{cell_i}"


def cgi_variants(cgi: str | None) -> set[str]:
    if not cgi or (isinstance(cgi, float) and pd.isna(cgi)):
        return set()
    s = str(cgi).strip().upper().replace(" ", "").replace("_", "-")
    out = {s, s.replace("-", "")}
    parts = re.split(r"-", s)
    if len(parts) == 4:
        mcc, mnc, node, cell = parts
        out.add(f"{mcc}-{mnc}-{node}-{cell}")
        out.add(f"{mcc}{mnc}-{node}-{cell}")
        if mnc.isdigit():
            out.add(f"{mcc}-{int(mnc)}-{node}-{cell}")
            out.add(f"{mcc}-{int(mnc):02d}-{node}-{cell}")
    return out


def _is_meta_row(row: tuple | list) -> bool:
    joined = " ".join(str(x) for x in row if x is not None)
    if not joined.strip():
        return True
    if _META_RE.search(joined):
        return True
    if re.search(r"[\u4e00-\u9fff]", joined) and not re.search(r"\d{2,}", joined):
        return True
    return False


def read_nrm_sheet(path: Path, sheet: str, cols: list[str]) -> pd.DataFrame:
    eng = get_excel_engine()
    try:
        if eng == "calamine":
            from python_calamine import CalamineWorkbook

            wb = CalamineWorkbook.from_path(str(path))
        else:
            from openpyxl import load_workbook

            wb = load_workbook(path, read_only=True, data_only=True)

        if sheet not in wb.sheet_names:
            if hasattr(wb, "close"):
                wb.close()
            return pd.DataFrame(columns=cols)

        ws = wb.get_sheet_by_name(sheet)
        # calamine 的 iter_rows 直接返回单元格值；openpyxl 需 values_only=True
        it = ws.iter_rows() if eng == "calamine" else ws.iter_rows(values_only=True)
        try:
            header = next(it)
        except StopIteration:
            if hasattr(wb, "close"):
                wb.close()
            return pd.DataFrame(columns=cols)

        idx = {str(h): i for i, h in enumerate(header) if h is not None}
        want = [c for c in cols if c in idx]
        out: dict[str, list] = {c: [] for c in want}

        for row in it:
            if _is_meta_row(row):
                continue
            for c in want:
                i = idx[c]
                out[c].append(row[i] if i < len(row) else None)
        if hasattr(wb, "close"):
            wb.close()
        return pd.DataFrame(out)
    except Exception:
        # fallback to openpyxl
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        if sheet not in wb.sheetnames:
            wb.close()
            return pd.DataFrame(columns=cols)
        ws = wb[sheet]
        it = ws.iter_rows(values_only=True)
        try:
            header = next(it)
        except StopIteration:
            wb.close()
            return pd.DataFrame(columns=cols)
        idx = {str(h): i for i, h in enumerate(header) if h is not None}
        want = [c for c in cols if c in idx]
        out_fallback: dict[str, list] = {c: [] for c in want}
        for row in it:
            if _is_meta_row(row):
                continue
            for c in want:
                i = idx[c]
                out_fallback[c].append(row[i] if i < len(row) else None)
        wb.close()
        return pd.DataFrame(out_fallback)


def resolve_nrm_dir(nrm_dir: Path | str | None = None) -> Path:
    if nrm_dir:
        p = Path(nrm_dir)
        if p.is_dir():
            return p
        raise FileNotFoundError(f"网管配置目录不存在: {p}")
    for cand in NRM_DIR_CANDIDATES:
        if cand.is_dir() and any(cand.glob("*.xlsx")):
            return cand
    raise FileNotFoundError(
        "未找到网管配置目录。请将 2.6G.xlsx / 700M.xlsx / sdr.xlsx 放到 data/网管配置/"
    )


def extract_nr_cells(path: Path, source: str) -> pd.DataFrame:
    ssb = read_nrm_sheet(path, "CellDefiningSSB", ["ManagedElement", "ldn", "pci", "ssbFrequency"])
    du = read_nrm_sheet(
        path,
        "NRCellDU",
        ["ManagedElement", "ldn", "moId", "cellLocalId", "nRTAC", "nrCarrierGroupId"],
    )
    cu = read_nrm_sheet(
        path,
        "NRCellCU",
        ["ManagedElement", "ldn", "moId", "cellLocalId", "frequency", "ssbFrequency", "nrCarrierGroupId", "userLabel"],
    )
    gnb = read_nrm_sheet(
        path, "GNBCUCPFunction", ["ManagedElement", "moId", "gNBId", "gNBIdLength", "pLMNId"]
    )
    phy = read_nrm_sheet(path, "NRPhysicalCellDU", ["ManagedElement", "moId", "nrCarrierGroupId"])

    empty_cols = [
        "CGI", "小区名", "网络制式", "PCI", "TAC", "频点", "频点_MHz",
        "SSB_SSB频率", "CU_SSB频率", "CU_载波频率",
        "来源文件",
    ]
    if ssb.empty or du.empty:
        return pd.DataFrame(columns=empty_cols)

    def parent_phys(ldn: Any) -> str | None:
        if ldn is None:
            return None
        m = re.search(r"NRPhysicalCellDU=([^,]+)", str(ldn))
        return m.group(1) if m else None

    ssb = ssb.copy()
    ssb["physMo"] = ssb["ldn"].map(parent_phys)
    ssb_g = ssb.groupby(["ManagedElement", "physMo"], as_index=False).agg(
        {"pci": "first", "ssbFrequency": "first"}
    )
    phy = phy.copy()
    phy["physMo"] = phy["moId"].astype(str)
    phy2 = phy.merge(ssb_g, on=["ManagedElement", "physMo"], how="left")

    gnb_map = (
        gnb.groupby("ManagedElement", as_index=False).agg({"gNBId": "first", "pLMNId": "first"})
        if not gnb.empty
        else pd.DataFrame(columns=["ManagedElement", "gNBId", "pLMNId"])
    )
    merged = du.merge(gnb_map, on="ManagedElement", how="left")
    merged = merged.merge(
        phy2[["ManagedElement", "nrCarrierGroupId", "pci", "ssbFrequency"]],
        on=["ManagedElement", "nrCarrierGroupId"],
        how="left",
    )

    # CellDefiningSSB 的 ssbFrequency → SSB_SSB频率
    merged.rename(columns={"ssbFrequency": "SSB_SSB频率"}, inplace=True)

    # NRCellCU 的 ssbFrequency / frequency / userLabel → 扩展列
    if not cu.empty:
        cu_cols = ["ManagedElement", "cellLocalId", "ssbFrequency", "frequency"]
        cu_rename = {"ssbFrequency": "CU_SSB频率", "frequency": "CU_载波频率"}
        if "userLabel" in cu.columns:
            cu_cols.append("userLabel")
            cu_rename["userLabel"] = "小区名"
        cu_renamed = cu[cu_cols].rename(columns=cu_rename)
        merged = merged.merge(cu_renamed, on=["ManagedElement", "cellLocalId"], how="left")
    else:
        merged["CU_SSB频率"] = None
        merged["CU_载波频率"] = None
        merged["小区名"] = None

    merged["PCI"] = merged["pci"].map(clean_num)
    merged["TAC"] = merged["nRTAC"].map(clean_num)
    # 频点_MHz：优先 CellDefiningSSB，回退 CU_SSB频率，再回退 CU_载波频率
    merged["频点_MHz"] = merged["SSB_SSB频率"].map(clean_num)
    cu_ssb = merged["CU_SSB频率"].map(clean_num)
    cu_freq = merged["CU_载波频率"].map(clean_num)
    merged["频点_MHz"] = merged["频点_MHz"].fillna(cu_ssb).fillna(cu_freq)
    merged["频点"] = merged["频点_MHz"].map(mhz_to_nr_arfcn)
    merged["CGI"] = [
        make_cgi(a, b, c)
        for a, b, c in zip(merged["pLMNId"], merged["gNBId"], merged["cellLocalId"])
    ]
    merged["网络制式"] = "5G"
    merged["来源文件"] = source
    return merged[empty_cols].dropna(subset=["CGI"])


def _extract_eNB_from_ldn(ldn: Any) -> str | None:
    """从 ldn 的 ENBCUCPFunction= 字段提取 eNB ID。

    支持格式:
      ENBCUCPFunction=306051          → 306051
      ENBCUCPFunction=460-00_305865   → 305865
    """
    if ldn is None:
        return None
    m = re.search(r"ENBCUCPFunction=([^,]+)", str(ldn))
    if not m:
        return None
    val = m.group(1).strip()
    val = re.sub(r"^460[-_]00[-_]?", "", val)
    return val or None


def extract_lte_cells(path: Path, source: str = "sdr.xlsx") -> pd.DataFrame:
    empty_cols = ["CGI", "小区名", "网络制式", "PCI", "TAC", "频点", "频点_MHz", "来源文件"]
    cell = read_nrm_sheet(
        path,
        "EUtranCellFDD",
        [
            "ManagedElement",
            "ldn",
            "moId",
            "cellLocalId",
            "refPlmn",
            "pci",
            "tac",
            "earfcnDl",
            "freqBandInd",
            "userLabel",
        ],
    )
    if cell.empty:
        return pd.DataFrame(columns=empty_cols)

    lte = cell.copy()
    lte["PCI"] = lte["pci"].map(clean_num)
    lte["TAC"] = lte["tac"].map(clean_num)
    lte["频点_MHz"] = lte["earfcnDl"].map(clean_num)
    band = lte["freqBandInd"] if "freqBandInd" in lte.columns else pd.Series([None] * len(lte))
    lte["频点"] = [mhz_to_lte_earfcn(m, b) for m, b in zip(lte["频点_MHz"], band)]
    # CGI = "460-00-" + ManagedElement(eNBID) + "-" + cellLocalId
    lte["CGI"] = [
        f"460-00-{int(float(str(me)))}-{int(float(str(cl)))}"
        if pd.notna(me) and pd.notna(cl) else None
        for me, cl in zip(lte["ManagedElement"], lte["cellLocalId"])
    ]
    lte["小区名"] = lte["userLabel"] if "userLabel" in lte.columns else None
    lte["网络制式"] = "4G"
    lte["来源文件"] = source
    return lte[empty_cols].dropna(subset=["CGI"])


def extract_lte_cu_cells(path: Path, source: str = "sdr.xlsx") -> pd.DataFrame:
    """从 CUEUtranCellFDDLTE / CUEUtranCellTDDLTE 读取4G反开站点。

    ldn 提取 CGI:
      ENBCUCPFunction=306051         → enbid=306051
      ENBCUCPFunction=460-00_305865  → enbid=305865
      CGI = "460-00-" + enbid + "-" + cellLocalId
    """
    empty_cols = ["CGI", "小区名", "网络制式", "PCI", "TAC", "频点", "频点_MHz", "来源文件"]

    fdd = read_nrm_sheet(
        path,
        "CUEUtranCellFDDLTE",
        ["ldn", "cellLocalId", "pci", "tac", "earfcnDl", "freqBandInd", "userLabel"],
    )
    tdd = read_nrm_sheet(
        path,
        "CUEUtranCellTDDLTE",
        ["ldn", "cellLocalId", "pci", "tac", "earfcn", "bandIndicator", "userLabel"],
    )

    # 过滤掉 cellLocalId 非数值的子表头行
    if not fdd.empty:
        fdd = fdd[fdd["cellLocalId"].map(lambda x: clean_num(x) is not None)].copy()
    if not tdd.empty:
        tdd = tdd[tdd["cellLocalId"].map(lambda x: clean_num(x) is not None)].copy()

    parts: list[pd.DataFrame] = []

    if not fdd.empty:
        fdd["enbid"] = fdd["ldn"].map(_extract_eNB_from_ldn)
        fdd["CGI"] = [
            f"460-00-{enb}-{int(float(str(cl)))}"
            if enb and pd.notna(cl) else None
            for enb, cl in zip(fdd["enbid"], fdd["cellLocalId"])
        ]
        fdd["PCI"] = fdd["pci"].map(clean_num)
        fdd["TAC"] = fdd["tac"].map(clean_num)
        fdd["频点_MHz"] = fdd["earfcnDl"].map(clean_num)
        fdd_band = fdd["freqBandInd"] if "freqBandInd" in fdd.columns else pd.Series([None] * len(fdd))
        fdd["频点"] = [mhz_to_lte_earfcn(m, b) for m, b in zip(fdd["频点_MHz"], fdd_band)]
        fdd["小区名"] = fdd["userLabel"] if "userLabel" in fdd.columns else None
        fdd["网络制式"] = "4G"
        fdd["来源文件"] = f"CUEUtranCellFDDLTE@{source}"
        parts.append(fdd[empty_cols])

    if not tdd.empty:
        tdd["enbid"] = tdd["ldn"].map(_extract_eNB_from_ldn)
        tdd["CGI"] = [
            f"460-00-{enb}-{int(float(str(cl)))}"
            if enb and pd.notna(cl) else None
            for enb, cl in zip(tdd["enbid"], tdd["cellLocalId"])
        ]
        tdd["PCI"] = tdd["pci"].map(clean_num)
        tdd["TAC"] = tdd["tac"].map(clean_num)
        tdd["频点_MHz"] = tdd["earfcn"].map(clean_num)
        tdd_band = tdd["bandIndicator"] if "bandIndicator" in tdd.columns else pd.Series([None] * len(tdd))
        tdd["频点"] = [mhz_to_lte_earfcn(m, b) for m, b in zip(tdd["频点_MHz"], tdd_band)]
        tdd["小区名"] = tdd["userLabel"] if "userLabel" in tdd.columns else None
        tdd["网络制式"] = "4G"
        tdd["来源文件"] = f"CUEUtranCellTDDLTE@{source}"
        parts.append(tdd[empty_cols])

    if not parts:
        return pd.DataFrame(columns=empty_cols)
    result = pd.concat(parts, ignore_index=True)
    return result.dropna(subset=["CGI"])


def extract_all_nrm(nrm_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for name in ("2.6G.xlsx", "700M.xlsx"):
        p = nrm_dir / name
        if p.is_file():
            frames.append(extract_nr_cells(p, name))
            frames.append(extract_lte_cu_cells(p, name))
    sdr = nrm_dir / "sdr.xlsx"
    if sdr.is_file():
        frames.append(extract_lte_cells(sdr))
    if not frames:
        raise FileNotFoundError(f"目录中未找到 2.6G/700M/sdr 网管文件: {nrm_dir}")
    df = pd.concat(frames, ignore_index=True)
    return df.drop_duplicates(subset=["CGI"], keep="first")


def ensure_pci_tac_columns(conn) -> None:
    for table in ("原始小区表", "物理表汇总"):
        exists = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
            [table],
        ).fetchone()
        if not exists:
            continue
        cols = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                [table],
            ).fetchall()
        }
        if "PCI" not in cols:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN PCI DOUBLE')
        if "TAC" not in cols:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN TAC DOUBLE')


def _build_cgi_lookup(nrm_df: pd.DataFrame) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for _, row in nrm_df.iterrows():
        payload = {
            "PCI": row.get("PCI"),
            "TAC": row.get("TAC"),
            "频点": row.get("频点"),
            "网络制式": row.get("网络制式"),
            "来源文件": row.get("来源文件"),
        }
        for v in cgi_variants(row.get("CGI")):
            lookup[v] = payload
    return lookup


def sync_nrm_to_physical(
    nrm_dir: Path | str | None = None,
    update_freq: bool = True,
    progress_callback: ProgressCallback | None = None,
    log_callback: LogCallback | None = None,
) -> dict[str, Any]:
    logger = GuiLogger(log_callback)
    progress = GuiProgress(progress_callback, logger)

    progress.update(5, "定位网管配置目录...")
    root = resolve_nrm_dir(nrm_dir)
    logger.log(f"网管目录: {root}")

    progress.update(15, "解析网管配置...")
    nrm_df = extract_all_nrm(root)
    logger.log(
        f"网管提取 {len(nrm_df)} 条: "
        f"5G={(nrm_df['网络制式']=='5G').sum()}, 4G={(nrm_df['网络制式']=='4G').sum()}, "
        f"PCI非空={nrm_df['PCI'].notna().sum()}, TAC非空={nrm_df['TAC'].notna().sum()}, "
        f"频点非空={nrm_df['频点'].notna().sum()}"
    )

    progress.update(45, "连接统一库并补齐 PCI/TAC 列...")
    conn = get_unified_db_connection()
    try:
        init_unified_database(conn)
        ensure_pci_tac_columns(conn)

        progress.update(55, "按 CGI 匹配并更新...")
        lookup = _build_cgi_lookup(nrm_df)

        stats: dict[str, Any] = {
            "nrm_rows": len(nrm_df),
            "matched": 0,
            "updated_pci": 0,
            "updated_tac": 0,
            "updated_freq": 0,
            "tables": {},
        }

        for table in ("原始小区表", "物理表汇总"):
            exists = conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
                [table],
            ).fetchone()
            if not exists:
                logger.log(f"跳过不存在的表: {table}")
                continue

            phys = conn.execute(f'SELECT CGI, 网络制式, PCI, TAC, 频点 FROM "{table}"').fetchdf()
            if phys.empty:
                stats["tables"][table] = {"rows": 0, "matched": 0}
                continue

            matched = 0
            upd_pci = upd_tac = upd_freq = 0
            new_pci: list = []
            new_tac: list = []
            new_freq: list = []
            flags: list[bool] = []

            for _, row in phys.iterrows():
                hit = None
                for v in cgi_variants(str(row["CGI"])):
                    if v in lookup:
                        hit = lookup[v]
                        break
                if not hit:
                    new_pci.append(row.get("PCI"))
                    new_tac.append(row.get("TAC"))
                    new_freq.append(row.get("频点"))
                    flags.append(False)
                    continue

                matched += 1
                pci = hit["PCI"] if pd.notna(hit["PCI"]) else row.get("PCI")
                tac = hit["TAC"] if pd.notna(hit["TAC"]) else row.get("TAC")
                freq = row.get("频点")
                if update_freq and pd.notna(hit["频点"]):
                    if pd.isna(freq) or float(freq) != float(hit["频点"]):
                        upd_freq += 1
                    freq = hit["频点"]

                old_pci = row.get("PCI")
                old_tac = row.get("TAC")
                if pd.notna(hit["PCI"]) and (pd.isna(old_pci) or float(old_pci) != float(hit["PCI"])):
                    upd_pci += 1
                if pd.notna(hit["TAC"]) and (pd.isna(old_tac) or float(old_tac) != float(hit["TAC"])):
                    upd_tac += 1

                new_pci.append(pci)
                new_tac.append(tac)
                new_freq.append(freq)
                flags.append(True)

            phys = phys.copy()
            phys["PCI"] = new_pci
            phys["TAC"] = new_tac
            phys["频点"] = new_freq
            phys["_hit"] = flags
            hit_df = phys.loc[phys["_hit"], ["CGI", "PCI", "TAC", "频点"]]

            if not hit_df.empty:
                conn.register("_nrm_upd", hit_df)
                conn.execute(
                    f"""
                    UPDATE "{table}" AS t
                    SET PCI = u.PCI,
                        TAC = u.TAC,
                        频点 = u.频点
                    FROM _nrm_upd AS u
                    WHERE t.CGI = u.CGI
                    """
                )
                conn.unregister("_nrm_upd")

            stats["matched"] = max(stats["matched"], matched)
            stats["updated_pci"] += upd_pci
            stats["updated_tac"] += upd_tac
            stats["updated_freq"] += upd_freq
            stats["tables"][table] = {
                "rows": len(phys),
                "matched": matched,
                "updated_pci": upd_pci,
                "updated_tac": upd_tac,
                "updated_freq": upd_freq,
            }
            logger.log(
                f"{table}: 命中 {matched}/{len(phys)}, "
                f"PCI更新 {upd_pci}, TAC更新 {upd_tac}, 频点更新 {upd_freq}"
            )

        out_xlsx = BASE_DIR / "网管PCI_TAC_频点提取.xlsx"
        nrm_df.to_excel(out_xlsx, index=False)
        stats["extract_file"] = out_xlsx.name
        logger.log(f"网管提取明细已写出: {out_xlsx.name}")

        progress.update(100, f"网管同步完成，命中 {stats['matched']} 条")
        return stats
    finally:
        conn.close()


def run_nrm_sync_pipeline(
    nrm_dir: Path | str | None = None,
    update_freq: bool = True,
    progress_callback: ProgressCallback | None = None,
    log_callback: LogCallback | None = None,
) -> dict[str, Any]:
    return sync_nrm_to_physical(
        nrm_dir=nrm_dir,
        update_freq=update_freq,
        progress_callback=progress_callback,
        log_callback=log_callback,
    )


__all__ = [
    "mhz_to_nr_arfcn",
    "mhz_to_lte_earfcn",
    "extract_all_nrm",
    "extract_lte_cu_cells",
    "sync_nrm_to_physical",
    "run_nrm_sync_pipeline",
    "ensure_pci_tac_columns",
    "resolve_nrm_dir",
]
