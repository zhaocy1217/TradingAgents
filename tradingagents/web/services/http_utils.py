"""HTTP helpers for web services."""

from __future__ import annotations

import os

import requests


def _env_flag(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def build_http_session(*, use_system_proxy: bool | None = None) -> requests.Session:
    """Build a session with explicit proxy-env behavior.

    Default is not to trust system proxy env vars because many local Windows
    setups expose stale proxy settings that break Eastmoney connectivity.
    """
    session = requests.Session()
    if use_system_proxy is None:
        use_system_proxy = _env_flag("TRADINGAGENTS_WEB_USE_SYSTEM_PROXY", default=False)
    session.trust_env = bool(use_system_proxy)
    return session
