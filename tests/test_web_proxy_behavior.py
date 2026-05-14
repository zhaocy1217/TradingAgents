from __future__ import annotations

import pytest


@pytest.mark.unit
def test_http_session_disables_system_proxy_by_default(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_WEB_USE_SYSTEM_PROXY", raising=False)
    from tradingagents.web.services.http_utils import build_http_session

    session = build_http_session()
    assert session.trust_env is False


@pytest.mark.unit
def test_http_session_can_enable_system_proxy(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_WEB_USE_SYSTEM_PROXY", "1")
    from tradingagents.web.services.http_utils import build_http_session

    session = build_http_session()
    assert session.trust_env is True
