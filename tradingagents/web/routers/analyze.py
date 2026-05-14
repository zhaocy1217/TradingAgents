"""Analysis APIs for single symbol and Top-N."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Request

from tradingagents.web.db.session import db_session
from tradingagents.web.routers.common import open_db
from tradingagents.web.schemas import AnalyzeRequest, AnalyzeTopRequest
from tradingagents.web.services.analysis_service import (
    JobCancelledError,
    analyze_symbol_and_store,
    analyze_top_and_store,
)
from tradingagents.web.services.job_manager import JobManager

router = APIRouter(prefix="/api/analyze", tags=["analyze"])
_MAX_CONCURRENT_ANALYSIS = int(os.getenv("TRADINGAGENTS_WEB_MAX_CONCURRENT_ANALYSIS", "2"))
_ANALYSIS_SEMAPHORE = threading.BoundedSemaphore(max(1, _MAX_CONCURRENT_ANALYSIS))


@contextmanager
def _analysis_slot():
    acquired = _ANALYSIS_SEMAPHORE.acquire(blocking=False)
    if not acquired:
        raise HTTPException(
            status_code=429,
            detail="Analysis queue is full, please retry shortly.",
        )
    try:
        yield
    finally:
        _ANALYSIS_SEMAPHORE.release()


@router.post("")
def analyze_one(payload: AnalyzeRequest, request: Request):
    try:
        with _analysis_slot():
            with open_db(request) as conn:
                return analyze_symbol_and_store(
                    conn,
                    symbol=payload.symbol,
                    query=payload.query,
                    analysis_date=payload.analysis_date,
                )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/top")
def analyze_top(payload: AnalyzeTopRequest, request: Request):
    with _analysis_slot():
        with open_db(request) as conn:
            return analyze_top_and_store(
                conn,
                top_n=payload.top_n,
                analysis_date=payload.analysis_date,
                max_stocks=payload.max_stocks,
            )


def _run_async_job(
    *,
    job_manager: JobManager,
    job_id: str,
    db_path: str,
    kind: str,
    payload: AnalyzeRequest | AnalyzeTopRequest,
) -> None:
    def _progress(stage: str, message: str) -> None:
        job_manager.update_progress(job_id, stage=stage, message=message)

    def _should_cancel() -> bool:
        return job_manager.should_cancel(job_id)

    try:
        with _analysis_slot():
            if _should_cancel():
                job_manager.mark_cancelled(job_id, "任务在开始执行前已取消")
                return
            job_manager.mark_running(job_id, stage="running", message="任务开始执行")
            with db_session(db_path) as conn:
                if kind == "single":
                    req = payload
                    result = analyze_symbol_and_store(
                        conn,
                        symbol=req.symbol,
                        query=req.query,
                        analysis_date=req.analysis_date,
                        progress_cb=_progress,
                        should_cancel_cb=_should_cancel,
                    )
                else:
                    req = payload
                    result = analyze_top_and_store(
                        conn,
                        top_n=req.top_n,
                        analysis_date=req.analysis_date,
                        max_stocks=req.max_stocks,
                        progress_cb=_progress,
                        should_cancel_cb=_should_cancel,
                    )
            job_manager.mark_completed(job_id, result=result)
    except JobCancelledError as exc:
        job_manager.mark_cancelled(job_id, str(exc))
    except Exception as exc:
        job_manager.mark_failed(job_id, str(exc))


@router.post("/submit")
def submit_analyze_one(payload: AnalyzeRequest, request: Request):
    job_manager = request.app.state.job_manager
    job_id = job_manager.create_job(
        kind="single",
        payload={
            "symbol": payload.symbol,
            "query": payload.query,
            "analysis_date": payload.analysis_date,
        },
    )
    worker = threading.Thread(
        target=_run_async_job,
        kwargs={
            "job_manager": job_manager,
            "job_id": job_id,
            "db_path": request.app.state.db_path,
            "kind": "single",
            "payload": payload,
        },
        daemon=True,
    )
    worker.start()
    return {"job_id": job_id, "status": "queued"}


@router.post("/top/submit")
def submit_analyze_top(payload: AnalyzeTopRequest, request: Request):
    job_manager = request.app.state.job_manager
    job_id = job_manager.create_job(
        kind="top",
        payload={
            "top_n": payload.top_n,
            "analysis_date": payload.analysis_date,
            "max_stocks": payload.max_stocks,
        },
    )
    worker = threading.Thread(
        target=_run_async_job,
        kwargs={
            "job_manager": job_manager,
            "job_id": job_id,
            "db_path": request.app.state.db_path,
            "kind": "top",
            "payload": payload,
        },
        daemon=True,
    )
    worker.start()
    return {"job_id": job_id, "status": "queued"}


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str, request: Request):
    job_manager = request.app.state.job_manager
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request):
    job_manager = request.app.state.job_manager
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    accepted = job_manager.request_cancel(job_id)
    updated = job_manager.get_job(job_id)
    return {"accepted": accepted, "job": updated}
