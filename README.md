# CapPhysCombine - 容量表合成工具

一个基于 Python + Tkinter 的桌面工具，用于将 4G/5G 小区容量数据合成为统一的容量报表。

## 功能

- 自动扫描 `data/` 目录下的源数据文件
- 合成 5G 容量表、4G 容量表、45G 总表
- 提供图形界面，支持进度显示和日志输出

## 使用方式

### 方式一：直接运行 Python 脚本

```bash
pip install -r requirements.txt
python 合成流量表.py
```

### 方式二：下载已构建的 EXE

从 [Releases](https://github.com/Charlielian/CapPhysCombine/releases) 页面下载最新的 `CapPhysCombine.zip`，解压后运行 `CapPhysCombine.exe`。

> 注意：运行 EXE 时，请确保 `data/` 目录与 `CapPhysCombine.exe` 在同一目录下，且 `data/` 中包含所需的源数据文件。

## 数据文件要求

将以下 Excel 文件放入 `data/` 目录：

| 文件类型 | 文件名模式 |
|---------|-----------|
| 5G 小区容量(周) | `5G小区容量-周*.xlsx` |
| 5G 小区容量(天) | `5G小区容量报表*.xlsx` |
| 5G MR 覆盖 | `5GMR覆盖-小区天*.xlsx` |
| 5G KPI 报表 | `5G小区性能KPI报表*.xlsx` |
| 4G 重要场景(周) | `重要场景-周*.xlsx` |
| 4G 重要场景(天) | `重要场景-天*.xlsx` |
| 4G MR 覆盖 | `4GMR覆盖-小区天*.xlsx` |

## 构建 EXE

项目通过 GitHub Actions 自动构建。每次推送 tag（如 `v1.0.0`）时会自动触发构建并发布到 Releases。

手动构建：

```bash
pip install pyinstaller
pyinstaller CapPhysCombine.spec
```

构建产物在 `dist/CapPhysCombine/` 目录下。
