"""FastAPI application entrypoint for A-share web interface."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from tradingagents.web.db.session import default_db_path, init_db
from tradingagents.web.routers import analyze, favorites, hot, pages, reports, symbols
from tradingagents.web.services.job_manager import JobManager


def _cors_allow_origins() -> list[str]:
    raw = (os.getenv("TRADINGAGENTS_CORS_ORIGINS") or "").strip()
    if not raw:
        return ["*"]
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins if origins else ["*"]


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="TradingAgents A-share Web", version="0.1.0")
    origins = _cors_allow_origins()
    # Browsers reject Access-Control-Allow-Origin: * together with credentialed fetches;
    # keep credentials off when using wildcard (typical for this app — no cookie auth).
    use_credentials = origins != ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=use_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    root = Path(__file__).resolve().parent
    static_dir = root / "static"
    templates_dir = root / "templates"

    app.state.db_path = db_path or default_db_path()
    app.state.job_manager = JobManager()
    app.state.templates = None
    try:
        from fastapi.templating import Jinja2Templates

        app.state.templates = Jinja2Templates(directory=str(templates_dir))
    except AssertionError:
        # Jinja2 is optional in non-web API environments.
        app.state.templates = None

    init_db(app.state.db_path)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    if app.state.templates is not None:
        app.include_router(pages.router)
    app.include_router(symbols.router)
    app.include_router(analyze.router)
    app.include_router(reports.router)
    app.include_router(favorites.router)
    app.include_router(hot.router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


def run() -> None:
    import uvicorn

    uvicorn.run("tradingagents.web.app:create_app", factory=True, host="0.0.0.0", port=8000, reload=True)
