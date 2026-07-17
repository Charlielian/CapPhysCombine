# CapPhysCombine

容量表合成 / 物理表汇总 / 低效小区 / 共站同覆盖管理工具。

技术栈：**FastAPI + 静态页面**（端口 **4008**）。业务逻辑在 `app/pipelines/`，Web 入口在 `app/main.py`。

## 功能

- 扫描 `data/` 下源 Excel，合成 5G / 4G / 45G 容量表
- 物理表汇总（工参 + GeoJSON 空间关联）
- 低效小区分析与在线查看
- 4G 零低流量风险小区日监控
- 共站同覆盖表 CRUD / Excel 导入导出
- 进度与日志实时展示；结果文件一键下载

## 安装

```bash
pip install -r requirements.txt
```

## Web 启动（推荐）

```bash
uvicorn app.main:app --host 0.0.0.0 --port 4008
# 或
python CapPhysCombine.py --serve
```

浏览器打开：<http://localhost:4008/>

将源数据 Excel 放入项目根目录下的 `data/`，也可在页面上直接上传。

## 命令行批处理

```bash
# 容量表合成（同时写出低效结果）
python CapPhysCombine.py --mode capacity

# 仅低效小区
python CapPhysCombine.py --mode loweff

# 零低流量风险小区分析
python CapPhysCombine.py --mode zero_low_flow

# 物理表汇总
python CapPhysCombine.py --mode physical --physical-dir .

# 扇区冲突检测 / 修正
python CapPhysCombine.py --check-conflicts 物理表汇总结果.xlsx
python CapPhysCombine.py --fix-conflicts 物理表汇总结果.xlsx
```

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
  main.py          # FastAPI 入口
  jobs.py          # 后台任务
  routers/         # HTTP API
  pipelines/       # 业务流水线（无 UI）
static/            # 前端静态页
data/              # 输入 Excel
capphys_unified.db # 共站同覆盖等持久化
CapPhysCombine.py  # CLI / --serve 入口
```

## 说明

- 原 Tkinter 桌面 GUI 已移除，请使用 Web 界面。
- 基于 PyInstaller 的 Windows EXE 打包流程已不适用当前 Web 形态；如需本地部署，直接安装依赖并用 uvicorn 启动即可。
- 同时只允许一个长任务运行，避免 DuckDB 冲突。
