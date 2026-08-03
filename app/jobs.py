"""In-process job registry with asset dependency graph + single-flight lock + DuckDB persistence.

Inspired by Dagster's asset-based modeling:
- Each pipeline output is modeled as an "asset" with declared dependencies.
- ``validate_run_order()`` checks that prerequisite assets have been produced
  before allowing a dependent pipeline to start.
- ``JobManager.start()`` enforces these constraints automatically.
"""

from __future__ import annotations

import json
import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from app.jsonutil import df_records
from app.pipelines.capacity import run_pipeline
from app.pipelines.common import BASE_DIR, LOWEFF_OUTPUT_PATH, UNIFIED_DB_PATH
from app.pipelines.io import read_excel
from app.pipelines.loweff import run_low_efficiency_pipeline
from app.pipelines.nrm_sync import run_nrm_sync_pipeline
from app.pipelines.physical import run_physical_table_pipeline
from app.pipelines.sector import (
    detect_sector_conflicts,
    run_physical_table_sector_fix,
)
from app.pipelines.zero_low_flow import run_zero_low_flow_pipeline

PHYSICAL_OUTPUT = BASE_DIR / "物理表汇总结果.xlsx"
CONFLICT_DISPLAY_COLUMNS = [
    "物理站",
    "站点类型",
    "CGI",
    "小区名称",
    "BAND",
    "sectionid",
    "共站同覆盖名",
    "方位角",
    "网络制式",
]

JOB_HISTORY_LIMIT = 500


# ==============================================================================
# Asset dependency graph (Dagster-inspired)
# ==============================================================================

# Each key is a job kind; its value lists the kinds that must succeed first.
# Independent kinds (empty list) can always run.
ASSET_DEPS: dict[str, list[str]] = {
    "capacity": [],                    # reads raw Excel — no upstream jobs needed
    "physical": [],                    # reads raw Excel — no upstream jobs needed
    "nrm_sync": [],                    # standalone network-management sync
    "conflicts_check": ["physical"],   # needs physical table output
    "conflicts_fix": ["physical"],     # needs physical table output
    "loweff": ["capacity"],            # derives from capacity tables
    "zero_low_flow_4g": [],            # standalone (reads its own input files)
    "zero_low_flow_5g": [],            # standalone
}

ASSET_KIND_LABELS: dict[str, str] = {
    "capacity": "容量表合成",
    "physical": "物理表汇总",
    "nrm_sync": "网管数据同步",
    "conflicts_check": "扇区冲突检测",
    "conflicts_fix": "扇区冲突修正",
    "loweff": "低效小区分析",
    "zero_low_flow_4g": "4G零低流量分析",
    "zero_low_flow_5g": "5G零低流量分析",
}


def get_asset_graph() -> dict[str, Any]:
    """Return the full dependency graph as a serialisable dict."""
    assets = []
    for name, deps in ASSET_DEPS.items():
        assets.append({
            "name": name,
            "label": ASSET_KIND_LABELS.get(name, name),
            "depends_on": deps,
        })
    edges = []
    for name, deps in ASSET_DEPS.items():
        for dep in deps:
            edges.append({"from": dep, "to": name})
    return {"assets": assets, "edges": edges}


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    kind: str
    status: JobStatus = JobStatus.PENDING
    progress: int = 0
    message: str = ""
    logs: list[str] = field(default_factory=list)
    result_files: list[str] = field(default_factory=list)
    result_data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "logs": list(self.logs),
            "result_files": list(self.result_files),
            "result_data": dict(self.result_data),
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }

    def to_persist_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "result_files": json.dumps(self.result_files, ensure_ascii=False),
            "result_data": json.dumps(self.result_data, ensure_ascii=False, default=str),
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


# ==============================================================================
# DuckDB persistence helpers
# ==============================================================================


def _ensure_jobs_table(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_history (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            progress INTEGER DEFAULT 0,
            message TEXT DEFAULT '',
            result_files TEXT DEFAULT '[]',
            result_data TEXT DEFAULT '{}',
            error TEXT,
            created_at TEXT NOT NULL,
            finished_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_job_created ON job_history(created_at)")


def _persist_job(job: Job) -> None:
    try:
        with duckdb.connect(str(UNIFIED_DB_PATH)) as conn:
            _ensure_jobs_table(conn)
            d = job.to_persist_dict()
            conn.execute(
                """
                INSERT OR REPLACE INTO job_history
                (id, kind, status, progress, message, result_files, result_data,
                 error, created_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    d["id"], d["kind"], d["status"], d["progress"], d["message"],
                    d["result_files"], d["result_data"], d["error"],
                    d["created_at"], d["finished_at"],
                ],
            )
    except Exception as exc:  # pragma: no cover
        try:
            from app.pipelines.logging_util import get_logger
            get_logger().warning(f"job 持久化失败 [{job.id}]: {exc}")
        except Exception:
            pass


def _load_job_history(limit: int = JOB_HISTORY_LIMIT) -> list[dict[str, Any]]:
    try:
        with duckdb.connect(str(UNIFIED_DB_PATH)) as conn:
            _ensure_jobs_table(conn)
            rows = conn.execute(
                """
                SELECT id, kind, status, progress, message, result_files,
                       result_data, error, created_at, finished_at
                FROM job_history
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            result_files = json.loads(row[5]) if row[5] else []
            result_data = json.loads(row[6]) if row[6] else {}
        except (json.JSONDecodeError, TypeError):
            result_files, result_data = [], {}
        results.append({
            "id": row[0],
            "kind": row[1],
            "status": row[2],
            "progress": row[3],
            "message": row[4] or "",
            "logs": [],
            "result_files": result_files,
            "result_data": result_data,
            "error": row[7],
            "created_at": row[8],
            "finished_at": row[9],
        })
    return results


def _mark_interrupted_jobs_failed() -> int:
    try:
        with duckdb.connect(str(UNIFIED_DB_PATH)) as conn:
            _ensure_jobs_table(conn)
            conn.execute(
                """
                UPDATE job_history
                SET status = 'failed',
                    error = '服务重启，任务中断',
                    finished_at = ?
                WHERE status = 'running'
                """,
                [datetime.now().isoformat(timespec="seconds")],
            )
            cnt = conn.execute(
                "SELECT COUNT(*) FROM job_history WHERE error = '服务重启，任务中断'"
            ).fetchone()
            return cnt[0] if cnt else 0
    except Exception:
        return 0


def _prune_old_jobs() -> None:
    try:
        with duckdb.connect(str(UNIFIED_DB_PATH)) as conn:
            _ensure_jobs_table(conn)
            conn.execute(
                """
                DELETE FROM job_history
                WHERE id NOT IN (
                    SELECT id FROM job_history
                    ORDER BY created_at DESC
                    LIMIT ?
                )
                """,
                [JOB_HISTORY_LIMIT],
            )
    except Exception:
        pass


# ==============================================================================
# JobManager
# ==============================================================================


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._current_job_id: str | None = None
        self._interrupted_count = _mark_interrupted_jobs_failed()
        _prune_old_jobs()

    # -- query helpers -------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        if job is not None:
            return job
        history = _load_job_history(limit=JOB_HISTORY_LIMIT)
        for record in history:
            if record["id"] == job_id:
                return Job(
                    id=record["id"],
                    kind=record["kind"],
                    status=JobStatus(record["status"]),
                    progress=record["progress"],
                    message=record["message"],
                    logs=[],
                    result_files=record["result_files"],
                    result_data=record["result_data"],
                    error=record["error"],
                    created_at=record["created_at"],
                    finished_at=record["finished_at"],
                )
        return None

    def list_history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            in_memory = [j.to_dict() for j in self._jobs.values()]
        db_history = _load_job_history(limit=limit)
        seen_ids = {j["id"] for j in in_memory}
        merged = in_memory + [h for h in db_history if h["id"] not in seen_ids]
        merged.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return merged[:limit]

    def current(self) -> Job | None:
        if self._current_job_id:
            return self._jobs.get(self._current_job_id)
        return None

    def is_busy(self) -> bool:
        return self._run_lock.locked()

    @property
    def interrupted_count(self) -> int:
        return self._interrupted_count

    # -- dependency validation -----------------------------------------------

    def _last_success_of(self, kind: str) -> Job | None:
        """Return the most recent successful job of *kind* (memory + DB)."""
        # Memory first (current session)
        with self._lock:
            for job in sorted(
                self._jobs.values(), key=lambda j: j.created_at or "", reverse=True
            ):
                if job.kind == kind and job.status == JobStatus.SUCCESS:
                    return job
        # Fall back to DB history
        for rec in _load_job_history(limit=200):
            if rec["kind"] == kind and rec["status"] == "success":
                return Job(
                    id=rec["id"],
                    kind=rec["kind"],
                    status=JobStatus.SUCCESS,
                    created_at=rec["created_at"],
                    finished_at=rec["finished_at"],
                )
        return None

    def validate_run_order(self, kind: str) -> str | None:
        """Check that all upstream assets of *kind* have been produced.

        Returns ``None`` when the dependency graph is satisfied, or a
        human-readable error string describing which prerequisite is missing.
        """
        deps = ASSET_DEPS.get(kind)
        if deps is None:
            # Unknown kind — allow it (backward-compatible)
            return None

        missing: list[str] = []
        for dep_kind in deps:
            last = self._last_success_of(dep_kind)
            if last is None:
                label = ASSET_KIND_LABELS.get(dep_kind, dep_kind)
                missing.append(label)

        if missing:
            return f"前置任务未完成：{'、'.join(missing)}"
        return None

    def get_asset_graph_status(self) -> list[dict[str, Any]]:
        """Return the dependency graph enriched with last-run status."""
        result = []
        for kind, deps in ASSET_DEPS.items():
            last = self._last_success_of(kind)
            result.append({
                "name": kind,
                "label": ASSET_KIND_LABELS.get(kind, kind),
                "depends_on": deps,
                "last_run": last.created_at if last else None,
                "last_status": last.status.value if last else None,
            })
        return result

    # -- internal helpers ----------------------------------------------------

    def _append_log(self, job: Job, message: str) -> None:
        job.logs.append(message)
        if len(job.logs) > 2000:
            job.logs = job.logs[-1500:]

    def _set_progress(self, job: Job, value: int, message: str) -> None:
        job.progress = max(0, min(100, int(value)))
        job.message = message
        self._append_log(job, message)

    def start(self, kind: str, runner: Callable[[Job], None]) -> Job:
        # Enforce single-flight
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("已有任务正在运行，请等待完成后再试")

        # Validate dependency graph
        dep_error = self.validate_run_order(kind)
        if dep_error is not None:
            self._run_lock.release()
            raise RuntimeError(dep_error)

        job = Job(id=uuid.uuid4().hex[:12], kind=kind, status=JobStatus.RUNNING)
        with self._lock:
            self._jobs[job.id] = job
            self._current_job_id = job.id
        _persist_job(job)

        def _worker() -> None:
            try:
                runner(job)
                job.status = JobStatus.SUCCESS
                if not job.message:
                    job.message = "完成"
                job.progress = 100
            except Exception as exc:
                job.status = JobStatus.FAILED
                job.error = str(exc)
                job.message = f"失败: {exc}"
                self._append_log(job, traceback.format_exc())
            finally:
                job.finished_at = datetime.now().isoformat(timespec="seconds")
                _persist_job(job)
                _prune_old_jobs()
                with self._lock:
                    if self._current_job_id == job.id:
                        self._current_job_id = None
                self._run_lock.release()

        threading.Thread(target=_worker, daemon=True, name=f"job-{job.kind}").start()
        return job

    # -- convenience: typed start methods ------------------------------------

    def start_capacity(self) -> Job:
        def runner(job: Job) -> None:
            def on_progress(value: int, message: str) -> None:
                self._set_progress(job, value, message)

            def on_log(message: str) -> None:
                self._append_log(job, message)

            run_pipeline(progress_callback=on_progress, log_callback=on_log)
            job.result_files = [
                p.name
                for p in sorted(BASE_DIR.glob("合成_容量表_*.xlsx"))[-2:]
            ] + [
                p.name
                for p in sorted(BASE_DIR.glob("容量表_45G_*.xlsx"))[-1:]
            ]
            if LOWEFF_OUTPUT_PATH.exists():
                job.result_files.append(LOWEFF_OUTPUT_PATH.name)

        return self.start("capacity", runner)

    def start_physical(self) -> Job:
        def runner(job: Job) -> None:
            def on_progress(value: int, message: str) -> None:
                self._set_progress(job, value, message)

            def on_log(message: str) -> None:
                self._append_log(job, message)

            output = str(BASE_DIR / "物理表汇总结果.xlsx")
            run_physical_table_pipeline(
                base_dir=str(BASE_DIR),
                output_path=output,
                progress_callback=on_progress,
                log_callback=on_log,
            )
            job.result_files = ["物理表汇总结果.xlsx"]

        return self.start("physical", runner)

    def start_nrm_sync(self, nrm_dir: str | None = None, update_freq: bool = True) -> Job:
        def runner(job: Job) -> None:
            def on_progress(value: int, message: str) -> None:
                self._set_progress(job, value, message)

            def on_log(message: str) -> None:
                self._append_log(job, message)

            stats = run_nrm_sync_pipeline(
                nrm_dir=nrm_dir,
                update_freq=update_freq,
                progress_callback=on_progress,
                log_callback=on_log,
            )
            job.result_data = stats
            if stats.get("extract_file"):
                job.result_files = [stats["extract_file"]]
            self._set_progress(
                job,
                100,
                f"网管同步完成：命中 {stats.get('matched', 0)}，"
                f"PCI {stats.get('updated_pci', 0)}，"
                f"TAC {stats.get('updated_tac', 0)}，"
                f"频点 {stats.get('updated_freq', 0)}",
            )

        return self.start("nrm_sync", runner)

    def start_loweff(self) -> Job:
        def runner(job: Job) -> None:
            def on_progress(value: int, message: str) -> None:
                self._set_progress(job, value, message)

            def on_log(message: str) -> None:
                self._append_log(job, message)

            path = run_low_efficiency_pipeline(
                progress_callback=on_progress, log_callback=on_log
            )
            job.result_files = [Path(path).name]

        return self.start("loweff", runner)

    def _conflict_payload(self, conflicts: pd.DataFrame) -> dict[str, Any]:
        if conflicts.empty:
            columns = [c for c in CONFLICT_DISPLAY_COLUMNS]
            return {
                "conflict_count": 0,
                "columns": columns,
                "records": [],
            }
        columns = [c for c in CONFLICT_DISPLAY_COLUMNS if c in conflicts.columns]
        extra = [c for c in conflicts.columns if c not in columns]
        columns = columns + [str(c) for c in extra]
        return {
            "conflict_count": len(conflicts),
            "columns": columns,
            "records": df_records(conflicts[columns], limit=500),
        }

    def start_check_conflicts(self) -> Job:
        def runner(job: Job) -> None:
            if not PHYSICAL_OUTPUT.is_file():
                raise FileNotFoundError(
                    f"文件不存在: {PHYSICAL_OUTPUT.name}，请先运行物理表汇总"
                )

            self._set_progress(job, 10, f"读取 {PHYSICAL_OUTPUT.name}...")
            df = read_excel(PHYSICAL_OUTPUT)
            self._append_log(job, f"共 {len(df)} 条记录")

            self._set_progress(job, 55, "分析扇区冲突...")
            conflicts = detect_sector_conflicts(df)

            count = len(conflicts)
            if count:
                self._append_log(job, f"发现 {count} 条冲突记录")
            else:
                self._append_log(job, "未发现扇区冲突")

            job.result_data = self._conflict_payload(conflicts)
            self._set_progress(
                job,
                100,
                f"检测完成：{'发现 ' + str(count) + ' 条冲突' if count else '无冲突'}",
            )

        return self.start("conflicts_check", runner)

    def start_fix_conflicts(self) -> Job:
        def runner(job: Job) -> None:
            if not PHYSICAL_OUTPUT.is_file():
                raise FileNotFoundError(
                    f"文件不存在: {PHYSICAL_OUTPUT.name}，请先运行物理表汇总"
                )

            self._set_progress(job, 10, "读取物理表并检测冲突...")

            def on_progress(value: int, message: str) -> None:
                self._set_progress(job, value, message)

            def on_log(message: str) -> None:
                self._append_log(job, message)

            result = run_physical_table_sector_fix(
                input_path=str(PHYSICAL_OUTPUT),
                output_dir=str(BASE_DIR),
                auto_fix=True,
                progress_callback=on_progress,
                log_callback=on_log,
            )

            conflict_df = result.get("conflict_df", pd.DataFrame())
            fix_df = result.get("fix_df", pd.DataFrame())
            job.result_data = {
                **self._conflict_payload(conflict_df),
                "fix_count": len(fix_df),
            }

            base_name = PHYSICAL_OUTPUT.stem
            job.result_files = [
                name
                for name in (
                    f"{base_name}-已修正.xlsx",
                    f"{base_name}-扇区冲突明细.xlsx",
                    f"{base_name}-扇区修正明细.xlsx",
                )
                if (BASE_DIR / name).is_file()
            ]

            self._set_progress(
                job,
                100,
                f"修正完成：冲突 {len(conflict_df)} 条，修正 {len(fix_df)} 条",
            )

        return self.start("conflicts_fix", runner)

    def start_zero_low_flow(
        self,
        file_paths: list[str] | None = None,
        network: str = "4g",
    ) -> Job:
        def runner(job: Job) -> None:
            def on_progress(value: int, message: str) -> None:
                self._set_progress(job, value, message)

            def on_log(message: str) -> None:
                self._append_log(job, message)

            path = run_zero_low_flow_pipeline(
                progress_callback=on_progress,
                log_callback=on_log,
                data_dir=file_paths,
                net_type=network,
            )
            job.result_files = [Path(path).name]

        kind = f"zero_low_flow_{network}"
        return self.start(kind, runner)


job_manager = JobManager()
