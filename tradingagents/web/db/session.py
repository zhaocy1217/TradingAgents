"""SQLite connection and bootstrap utilities for web app."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def default_db_path() -> str:
    override = (os.getenv("TRADINGAGENTS_WEB_DB_PATH") or "").strip()
    if override:
        return override
    project_root = Path(__file__).resolve().parents[3]
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "web.sqlite3")


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    # Concurrent web workers: allow waiting for locks; WAL coordinates readers/writers better.
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(db_path: str | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS symbols_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                company_name TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'CN',
                source TEXT NOT NULL DEFAULT 'unknown',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS analysis_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                company_name TEXT NOT NULL,
                analysis_date TEXT NOT NULL,
                signal TEXT NOT NULL,
                report_markdown TEXT NOT NULL,
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                company_name TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hot_stocks_weekly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                company_name TEXT NOT NULL,
                hot_score REAL NOT NULL,
                rank_date TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'eastmoney',
                extra_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_reports_symbol_date
                ON analysis_reports(symbol, analysis_date);
            CREATE INDEX IF NOT EXISTS idx_reports_company_name
                ON analysis_reports(company_name);
            CREATE INDEX IF NOT EXISTS idx_hot_rank_date
                ON hot_stocks_weekly(rank_date);
            CREATE INDEX IF NOT EXISTS idx_hot_symbol_date
                ON hot_stocks_weekly(symbol, rank_date);
            """
        )
        conn.commit()


@contextmanager
def db_session(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
