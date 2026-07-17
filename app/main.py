"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import DEFAULT_HOST, DEFAULT_PORT, STATIC_DIR
from app.pipelines.core import init_unified_database, setup_logging
from app.routers import cog, data, jobs_api, physical_extra, physical_query


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

app.include_router(data.router)
app.include_router(jobs_api.router)
app.include_router(cog.router)
app.include_router(physical_extra.router)
app.include_router(physical_query.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "port": DEFAULT_PORT}


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
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
