from __future__ import annotations

import sqlite3
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from tradingagents.web.app import create_app


@pytest.fixture()
def test_client(tmp_path):
    db_path = tmp_path / "web.sqlite3"
    app = create_app(db_path=str(db_path))
    return TestClient(app), str(db_path)


@pytest.mark.unit
def test_favorites_crud(test_client):
    client, _ = test_client

    created = client.post(
        "/api/favorites",
        json={"symbol": "600519.SS", "company_name": "贵州茅台", "note": "龙头"},
    )
    assert created.status_code == 200
    assert created.json()["symbol"] == "600519.SS"

    listed = client.get("/api/favorites")
    assert listed.status_code == 200
    assert any(row["symbol"] == "600519.SS" for row in listed.json()["items"])

    deleted = client.delete("/api/favorites/600519.SS")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


@pytest.mark.unit
def test_reports_query_returns_seeded_row(test_client):
    client, db_path = test_client
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO analysis_reports
            (symbol, company_name, analysis_date, signal, report_markdown, meta_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            ("600519.SS", "贵州茅台", "2026-05-13", "Buy", "# report", "{}"),
        )
        conn.commit()

    resp = client.get("/api/reports", params={"query": "茅台"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["symbol"] == "600519.SS"


@pytest.mark.unit
def test_symbols_search_uses_cache_first(test_client):
    client, db_path = test_client
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO symbols_cache (symbol, company_name, market, source, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            ("000001.SZ", "平安银行", "CN", "seed"),
        )
        conn.commit()

    resp = client.get("/api/symbols/search", params={"q": "平安"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items
    assert items[0]["symbol"] == "000001.SZ"


@pytest.mark.unit
def test_hot_week_query(test_client):
    client, db_path = test_client
    today = date.today().strftime("%Y-%m-%d")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO hot_stocks_weekly
            (symbol, company_name, hot_score, rank_date, source, extra_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            ("600519.SS", "贵州茅台", 95.2, today, "seed", "{}"),
        )
        conn.commit()

    resp = client.get("/api/hot/week")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["symbol"] == "600519.SS"


@pytest.mark.unit
def test_analyze_endpoint_invokes_service(monkeypatch, test_client):
    client, _ = test_client

    def _fake_analyze_symbol(conn, **kwargs):
        return {
            "report_id": 1,
            "symbol": kwargs["symbol"],
            "company_name": "贵州茅台",
            "analysis_date": "2026-05-13",
            "signal": "Buy",
            "report_markdown": "# mock",
        }

    monkeypatch.setattr(
        "tradingagents.web.routers.analyze.analyze_symbol_and_store",
        _fake_analyze_symbol,
    )

    resp = client.post(
        "/api/analyze",
        json={"symbol": "600519.SS", "analysis_date": "2026-05-13"},
    )
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "600519.SS"


@pytest.mark.unit
def test_async_analyze_job_status(monkeypatch, test_client):
    client, _ = test_client

    def _fake_analyze_symbol(conn, **kwargs):
        return {
            "report_id": 7,
            "symbol": kwargs["symbol"] or "600519.SS",
            "company_name": "贵州茅台",
            "analysis_date": "2026-05-13",
            "signal": "Buy",
            "report_markdown": "# async mock",
        }

    monkeypatch.setattr(
        "tradingagents.web.routers.analyze.analyze_symbol_and_store",
        _fake_analyze_symbol,
    )

    submit = client.post(
        "/api/analyze/submit",
        json={"symbol": "600519.SS", "analysis_date": "2026-05-13"},
    )
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]
    assert job_id

    deadline = time.time() + 2.0
    status = None
    while time.time() < deadline:
        check = client.get(f"/api/analyze/jobs/{job_id}")
        assert check.status_code == 200
        status = check.json()
        if status["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)

    assert status is not None
    assert status["status"] == "completed"
    assert status["result"]["symbol"] == "600519.SS"


@pytest.mark.unit
def test_async_analyze_job_can_receive_cancel(monkeypatch, test_client):
    client, _ = test_client

    def _fake_analyze_symbol(conn, **kwargs):
        time.sleep(0.2)
        return {
            "report_id": 8,
            "symbol": kwargs["symbol"] or "600519.SS",
            "company_name": "贵州茅台",
            "analysis_date": "2026-05-13",
            "signal": "Buy",
            "report_markdown": "# async mock",
        }

    monkeypatch.setattr(
        "tradingagents.web.routers.analyze.analyze_symbol_and_store",
        _fake_analyze_symbol,
    )

    submit = client.post(
        "/api/analyze/submit",
        json={"symbol": "600519.SS", "analysis_date": "2026-05-13"},
    )
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]

    cancel = client.post(f"/api/analyze/jobs/{job_id}/cancel")
    assert cancel.status_code == 200
    assert "accepted" in cancel.json()

    check = client.get(f"/api/analyze/jobs/{job_id}")
    assert check.status_code == 200
    assert check.json()["id"] == job_id
