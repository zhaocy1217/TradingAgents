"""In-memory background job registry for long-running analysis tasks."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create_job(self, *, kind: str, payload: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "kind": kind,
                "status": "queued",
                "stage": "queued",
                "message": "任务已创建，等待执行",
                "cancel_requested": False,
                "payload": payload,
                "result": None,
                "error": None,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "started_at": None,
                "finished_at": None,
            }
        return job_id

    def mark_running(self, job_id: str, *, stage: str = "running", message: str = "任务开始执行") -> None:
        self._update(job_id, status="running", stage=stage, message=message, started_at=datetime.now().isoformat(timespec="seconds"))

    def update_progress(self, job_id: str, *, stage: str, message: str) -> None:
        self._update(job_id, stage=stage, message=message)

    def mark_completed(self, job_id: str, result: dict[str, Any]) -> None:
        self._update(
            job_id,
            status="completed",
            stage="completed",
            message="任务完成",
            result=result,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )

    def mark_failed(self, job_id: str, error: str) -> None:
        self._update(
            job_id,
            status="failed",
            stage="failed",
            message="任务失败",
            error=error,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )

    def request_cancel(self, job_id: str) -> bool:
        with self._lock:
            row = self._jobs.get(job_id)
            if row is None:
                return False
            if row.get("status") in {"completed", "failed", "cancelled"}:
                return False
            row["cancel_requested"] = True
            if row.get("status") in {"queued", "running"}:
                row["status"] = "cancelling"
                row["stage"] = "cancelling"
                row["message"] = "已收到取消请求，正在中止任务"
            row["updated_at"] = datetime.now().isoformat(timespec="seconds")
            return True

    def should_cancel(self, job_id: str) -> bool:
        with self._lock:
            row = self._jobs.get(job_id)
            if row is None:
                return False
            return bool(row.get("cancel_requested"))

    def mark_cancelled(self, job_id: str, message: str = "任务已取消") -> None:
        self._update(
            job_id,
            status="cancelled",
            stage="cancelled",
            message=message,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._jobs.get(job_id)
            if row is None:
                return None
            return dict(row)

    def _update(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            row = self._jobs.get(job_id)
            if row is None:
                return
            row.update(updates)
            row["updated_at"] = datetime.now().isoformat(timespec="seconds")
