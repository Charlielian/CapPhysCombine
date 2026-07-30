"""FastAPI application entrypoint.

Registers structured error handlers and mounts all routers with
dependency-injected singletons (``JobManager`` via ``Depends()``).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import STATIC_DIR, get_server_host, get_server_port
from app.pipelines.core import init_unified_database, setup_logging
from app.routers import cog, data, jobs_api, physical_extra, physical_query
from app.schemas import AppError, ErrorResponse, ErrorDetail


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    try:
        init_unified_database()
    except Exception:
        # DB may be locked by another process; API can still start
        pass
    yield


app = FastAPI(
    title="CapPhysCombine",
    description="容量表合成 / 物理表汇总 / 低效小区 / 45G工参查询 / 共站同覆盖",
    version="3.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Global exception handlers — consistent { "ok": false, "error": { … } } shape
# ---------------------------------------------------------------------------


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
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
    body = ErrorResponse(error=ErrorDetail(
        code="validation_error",
        message="请求参数校验失败",
        detail=detail,
    )).model_dump()
    return JSONResponse(status_code=422, content=body)


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
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
