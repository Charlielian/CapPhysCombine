"""FastAPI application entrypoint.

Registers structured error handlers and mounts all routers with
dependency-injected singletons (``JobManager`` via ``Depends()``).
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.config import STATIC_DIR, get_server_host, get_server_port
from app.pipelines.core import init_unified_database
from app.pipelines.logging_util import setup_logging
from app.routers import capacity_results, cog, data, jobs_api, physical_extra, physical_query
from app.schemas import AppError, ErrorResponse, ErrorDetail

log = logging.getLogger("CapPhysCombine")


# ---------------------------------------------------------------------------
# HTTP request access logging middleware
# ---------------------------------------------------------------------------

_IGNORED_PATHS = {"/api/health", "/favicon.ico"}


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path in _IGNORED_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        client = request.client
        client_host = client.host if client else "-"
        log.info(
            "%s %s %d %.1fms %s",
            request.method,
            path,
            response.status_code,
            elapsed_ms,
            client_host,
        )
        return response


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.pipelines.core import LOG_DIR
    from datetime import datetime

    logger = setup_logging()
    logger.info(
        "CapPhysCombine 服务启动 (host=%s, port=%s, time=%s)",
        get_server_host(),
        get_server_port(),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    logger.info("日志目录: %s", LOG_DIR)
    try:
        init_unified_database()
        logger.info("统一数据库初始化完成")
    except Exception as exc:
        logger.warning("统一数据库初始化失败: %s (API 将使用已有数据)", exc)
    yield
    logger.info("CapPhysCombine 服务关闭")


# ---------------------------------------------------------------------------
# App creation
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CapPhysCombine",
    description="容量表合成 / 物理表汇总 / 低效小区 / 45G工参查询 / 共站同覆盖",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(AccessLogMiddleware)


# ---------------------------------------------------------------------------
# Global exception handlers — consistent { "ok": false, "error": { … } } shape
# ---------------------------------------------------------------------------


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    log.error("AppError [%s] %s: %s", exc.code, exc.status_code, exc.detail or exc)
    body = ErrorResponse(error=ErrorDetail(
        code=exc.code,
        message=exc.detail or str(exc),
    )).model_dump()
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    detail = "; ".join(
        f"{'.'.join(str(loc) for loc in e.get('loc', []))}: {e.get('msg', '')}"
        for e in exc.errors()
    )
    log.warning("请求参数校验失败: %s", detail)
    body = ErrorResponse(error=ErrorDetail(
        code="validation_error",
        message="请求参数校验失败",
        detail=detail,
    )).model_dump()
    return JSONResponse(status_code=422, content=body)


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    log.exception("未处理异常: %s", exc)
    body = ErrorResponse(error=ErrorDetail(
        code="internal_error",
        message="服务器内部错误",
        detail=str(exc),
    )).model_dump()
    return JSONResponse(status_code=500, content=body)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(data.router)
app.include_router(jobs_api.router)
app.include_router(cog.router)
app.include_router(physical_extra.router)
app.include_router(physical_query.router)
app.include_router(capacity_results.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "port": get_server_port()}


# Static assets under /static; index.html at /
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=get_server_host(),
        port=get_server_port(),
        reload=False,
    )


if __name__ == "__main__":
    main()
