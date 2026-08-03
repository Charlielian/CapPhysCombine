# syntax=docker/dockerfile:1
# CapPhysCombine 容器镜像
# 提供 FastAPI Web 服务，端口由 config.yaml 决定（默认 9008）

FROM python:3.11-slim AS base

# 避免 Python 写入 .pyc / 缓冲 stdout
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CAPPHYS_HOST=0.0.0.0 \
    CAPPHYS_PORT=9008

# geopandas / shapely / pyproj 运行期需要的系统库
# libgeos / libproj / libgdal 供空间运算；libexpat1 供 openpyxl 间接依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgeos-dev \
        libproj-dev \
        libgdal-dev \
        libexpat1 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖（利用层缓存）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app/ ./app/
COPY static/ ./static/
COPY config.yaml ./
COPY CapPhysCombine.py ./

# 运行期目录（由 compose 挂载卷覆盖）
RUN mkdir -p /app/data /app/logs /app/.cache/parquet

EXPOSE 9008

# 健康检查（容器自带 curl 较精简，用 python 标准库探测）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9008/api/health', timeout=3).status==200 else 1)" || exit 1

# 默认以 Web 模式启动；可被 CMD 覆盖以运行 CLI 子命令
CMD ["python", "CapPhysCombine.py", "serve"]
