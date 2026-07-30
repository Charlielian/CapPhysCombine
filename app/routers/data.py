from __future__ import annotations

import asyncio
from html import escape
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from app.config import BASE_DIR, DATA_DIR
from app.pipelines.common import get_data_file_status, list_output_files

router = APIRouter(prefix="/api/data", tags=["data"])

CAPACITY_LABELS = {
    "5g_week": "5G小区容量(周)",
    "5g_day": "5G小区容量(天)",
    "5g_mr": "5G MR覆盖",
    "5g_kpi": "5G KPI报表",
    "4g_week": "4G重要场景(周)",
    "4g_day": "4G重要场景(天)",
    "4g_mr": "4G MR覆盖",
    "cog_coverage": "共站同覆盖(可选)",
}

PHYSICAL_LABELS = {
    "nr_cellant": "5G工参 (*_nr_*.xlsx)",
    "lte_cellant": "4G工参 (*_lte_*.xlsx)",
}


def _write_file_sync(path: Path, content: bytes) -> None:
    """同步写文件（在工作线程中执行）。"""
    with path.open("wb") as f:
        f.write(content)


@router.get("/status")
def data_status():
    status = get_data_file_status()
    for item in status["capacity"]:
        item["label"] = CAPACITY_LABELS.get(item["key"], item["key"])
    for item in status["physical"]:
        item["label"] = PHYSICAL_LABELS.get(item["key"], item["key"])
    return status


def _status_item_html(item: dict) -> str:
    """单条状态项的 HTML（与前端 renderStatusList 结构一致）。"""
    found = bool(item.get("found"))
    optional = bool(item.get("optional"))
    cls = "ok" if found else ("warn" if optional else "bad")
    label = escape(str(item.get("label") or item.get("key", "")))
    if found:
        count = item.get("count", 0)
        value = f"已找到 {count} 个" if count and count > 1 else "已找到"
    else:
        value = "未找到(可选)" if optional else "未找到"
    return (
        f'<li><span class="status-dot {cls}"></span>'
        f'<span class="label">{label}</span>'
        f'<span class="value {cls}">{escape(value)}</span></li>'
    )


def _output_item_html(file_info: dict) -> str:
    """单条输出文件项的 HTML。"""
    name = escape(str(file_info.get("name", "")))
    href = f'/api/data/outputs/{escape(file_info.get("name", ""), quote=True)}'
    return (
        f'<li><span class="status-dot ok"></span>'
        f'<span class="label">{name}</span>'
        f'<a href="{href}" download>下载</a></li>'
    )


@router.get("/status-fragment", response_class=HTMLResponse)
def data_status_fragment():
    """返回右侧数据状态面板的 HTML 片段（供 HTMX 局部刷新）。

    与 /api/data/status + /api/data/outputs 等价，但直接返回 HTML，
    让前端通过 hx-get 局部刷新，无需 JS 重建 DOM。
    """
    status = get_data_file_status()
    for item in status["capacity"]:
        item["label"] = CAPACITY_LABELS.get(item["key"], item["key"])
    for item in status["physical"]:
        item["label"] = PHYSICAL_LABELS.get(item["key"], item["key"])

    cap_items = "".join(_status_item_html(i) for i in status["capacity"])
    phy_items = "".join(_status_item_html(i) for i in status["physical"])
    out_items = "".join(
        _output_item_html(f) for f in list_output_files()[:12]
    )

    parts = [
        '<h3>数据文件状态</h3>',
        '<h4>容量表源文件</h4>',
        f'<ul id="capacityStatus" class="status-list">{cap_items}</ul>',
        '<h4>物理表工参</h4>',
        f'<ul id="physicalStatus" class="status-list">{phy_items}</ul>',
        '<h4>可下载结果</h4>',
        f'<ul id="outputList" class="status-list">{out_items}</ul>',
    ]
    return "".join(parts)


@router.post("/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """上传文件：异步读取 + run_in_executor 写盘，避免阻塞事件循环。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    loop = asyncio.get_event_loop()
    for upload in files:
        if not upload.filename:
            continue
        name = Path(upload.filename).name
        if not name.lower().endswith((".xlsx", ".xls", ".csv")):
            raise HTTPException(status_code=400, detail=f"不支持的文件类型: {name}")
        content = await upload.read()  # 异步读取
        dest = DATA_DIR / name
        # 写盘放到工作线程，避免阻塞事件循环
        await loop.run_in_executor(None, _write_file_sync, dest, content)
        saved.append(name)
    return {"saved": saved, "count": len(saved)}


@router.get("/outputs")
def outputs():
    return {"files": list_output_files()}


@router.get("/outputs/{name}")
def download_output(name: str):
    safe_name = Path(name).name
    path = (BASE_DIR / safe_name).resolve()
    if path.parent != BASE_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if path.suffix.lower() not in {".xlsx", ".xls", ".csv"}:
        raise HTTPException(status_code=400, detail="仅允许下载表格文件")
    return FileResponse(
        path,
        filename=safe_name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/download/{name}")
def download_output_alias(name: str):
    """兼容旧前端路径 /api/data/download/{name}，与 /outputs/{name} 等价。"""
    return download_output(name)


@router.get("/files")
def list_data_files():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for path in sorted(DATA_DIR.iterdir()):
        if path.is_file() and not path.name.startswith("."):
            files.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "mtime": path.stat().st_mtime,
                }
            )
    return {"files": files}


@router.get("/cache/stats")
def parquet_cache_stats():
    """返回 Parquet 缓存统计信息。"""
    from app.pipelines.io import cache_stats

    return cache_stats()


@router.delete("/cache")
def clear_cache():
    """清空 Parquet 中间层缓存。"""
    from app.pipelines.io import clear_parquet_cache

    deleted = clear_parquet_cache()
    return {"deleted": deleted}
