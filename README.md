# CapPhysCombine

容量表合成 / 物理表汇总 / 低效小区 / 零低流量 / 共站同覆盖管理工具。

技术栈：**FastAPI + 静态页面**（端口 **4008**）。业务逻辑在 `app/pipelines/`（按功能分模块），统一 CLI 为 `CapPhysCombine.py`。

## 功能 ↔ 入口对照

| 功能 | 实现模块 | CLI | Web |
|------|----------|-----|-----|
| 容量表合成 | `app/pipelines/capacity.py` | `python CapPhysCombine.py capacity` | 任务页 |
| 物理表汇总 | `app/pipelines/physical.py` | `python CapPhysCombine.py physical` | 任务页 |
| 低效小区 | `app/pipelines/loweff.py` | `python CapPhysCombine.py loweff` | 任务页 / 查看 |
| 零低流量 | `app/pipelines/zero_low_flow.py` | `python CapPhysCombine.py zero-low-flow` | 任务页 / 查看 |
| 扇区冲突 | `app/pipelines/sector.py` | `python CapPhysCombine.py sector check|fix` | API |
| 共站同覆盖 | `app/pipelines/cog_db.py` | （Web 管理） | 共站同覆盖页 |
| 共享 IO/日志 | `app/pipelines/common.py` | — | — |

一次性工具与旧脚本见 `scripts/`（**正式流程不依赖「未命名文件夹」**）。

## 安装

```bash
pip install -r requirements.txt
```

## Web 启动（推荐）

```bash
uvicorn app.main:app --host 0.0.0.0 --port 4008
# 或
python CapPhysCombine.py serve
```

浏览器打开：<http://localhost:4008/>

将源数据 Excel 放入项目根目录下的 `data/`，也可在页面上直接上传。

## 命令行

```bash
# 启动 Web
python CapPhysCombine.py serve

# 容量表合成
python CapPhysCombine.py capacity

# 仅低效小区
python CapPhysCombine.py loweff

# 零低流量风险小区分析
python CapPhysCombine.py zero-low-flow

# 物理表汇总
python CapPhysCombine.py physical

# 扇区冲突检测 / 修正
python CapPhysCombine.py sector check -i 物理表汇总结果.xlsx
python CapPhysCombine.py sector fix -i 物理表汇总结果.xlsx
```

兼容旧写法：`python CapPhysCombine.py --mode capacity`（等同子命令）。

## 数据文件要求

将以下文件放入 `data/`：

| 类型 | 文件名模式 |
|------|------------|
| 5G 小区容量(周) | `5G小区容量-周*.xlsx` |
| 5G 小区容量(天) | `5G小区容量报表*.xlsx` |
| 5G MR 覆盖 | `5GMR覆盖-小区天*.xlsx` |
| 5G KPI 报表 | `5G小区性能KPI报表*.xlsx` |
| 4G 重要场景(周) | `重要场景-周*.xlsx` |
| 4G 重要场景(天) | `重要场景-天*.xlsx` |
| 问题小区归类(可选) | `问题小区问题归类.xlsx` |
| 4G MR 覆盖 | `4GMR覆盖-小区天*.xlsx` |
| 共站同覆盖(可选) | `共站同覆盖小区_4g_5g.xlsx` |
| 5G 工参(物理表) | `*_nr_*.xlsx` |
| 4G 工参(物理表) | `*_lte_*.xlsx` |

物理表还依赖项目根下的 `路测网格/`、`区域/`、`网格/`、`乡镇/` 等 GeoJSON 目录。

## 目录结构

```
app/
  main.py              # FastAPI 入口
  jobs.py              # 后台任务（直连各域模块）
  routers/             # HTTP API
  pipelines/
    common.py          # 路径 / 日志 / DuckDB 通用
    capacity.py        # 容量表
    loweff.py          # 低效小区
    physical.py        # 物理表汇总
    sector.py          # 扇区冲突
    cog_db.py          # 共站同覆盖
    zero_low_flow.py   # 零低流量
static/                # 前端静态页
data/                  # 输入 Excel
scripts/
  tools/               # 一次性辅助脚本
  legacy/              # 已归档旧脚本（不进主流程）
capphys_unified.db     # 共站同覆盖等持久化
CapPhysCombine.py      # 统一 CLI
```

## 说明

- 原 `app/pipelines/core.py` 已按域拆分删除；jobs/routers/CLI 直连各模块。
- 零低流量正式实现仅使用 `app/pipelines/zero_low_flow.py`，不再依赖「未命名文件夹」。
- 同时只允许一个长任务运行，避免 DuckDB 冲突。
