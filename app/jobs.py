"""In-process job registry with single-flight lock."""

from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from app.jsonutil import df_records
from app.pipelines.core import (
    BASE_DIR,
    LOWEFF_OUTPUT_PATH,
    detect_sector_conflicts,
    run_low_efficiency_pipeline,
    run_physical_table_pipeline,
    run_physical_table_sector_fix,
    run_pipeline,
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


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._current_job_id: str | None = None

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def current(self) -> Job | None:
        if self._current_job_id:
            return self._jobs.get(self._current_job_id)
        return None

    def is_busy(self) -> bool:
        return self._run_lock.locked()

    def _append_log(self, job: Job, message: str) -> None:
        job.logs.append(message)
        if len(job.logs) > 2000:
            job.logs = job.logs[-1500:]

    def _set_progress(self, job: Job, value: int, message: str) -> None:
        job.progress = max(0, min(100, int(value)))
        job.message = message
        self._append_log(job, message)

    def start(self, kind: str, runner: Callable[[Job], None]) -> Job:
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("已有任务正在运行，请等待完成后再试")

        job = Job(id=uuid.uuid4().hex[:12], kind=kind, status=JobStatus.RUNNING)
        with self._lock:
            self._jobs[job.id] = job
            self._current_job_id = job.id

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
                with self._lock:
                    if self._current_job_id == job.id:
                        self._current_job_id = None
                self._run_lock.release()

        threading.Thread(target=_worker, daemon=True, name=f"job-{job.kind}").start()
        return job

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
            df = pd.read_excel(PHYSICAL_OUTPUT)
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

    def start_zero_low_flow(self) -> Job:
        def runner(job: Job) -> None:
            def on_progress(value: int, message: str) -> None:
                self._set_progress(job, value, message)

            def on_log(message: str) -> None:
                self._append_log(job, message)

            path = run_zero_low_flow_pipeline(
                progress_callback=on_progress, log_callback=on_log
            )
            job.result_files = [Path(path).name]

        return self.start("zero_low_flow", runner)


job_manager = JobManager()
