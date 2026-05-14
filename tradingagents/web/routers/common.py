"""Common helpers for routers."""

from __future__ import annotations

from fastapi import Request

from tradingagents.web.db.session import db_session


def get_db_path(request: Request) -> str:
    return request.app.state.db_path


def open_db(request: Request):
    return db_session(get_db_path(request))
