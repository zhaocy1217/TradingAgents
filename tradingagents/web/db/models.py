"""Typed records for web database payloads."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SymbolRecord:
    symbol: str
    company_name: str
    market: str
    source: str
    updated_at: str


@dataclass(slots=True)
class ReportRecord:
    id: int
    symbol: str
    company_name: str
    analysis_date: str
    signal: str
    report_markdown: str
    meta_json: str
    created_at: str


@dataclass(slots=True)
class FavoriteRecord:
    id: int
    symbol: str
    company_name: str
    note: str
    created_at: str


@dataclass(slots=True)
class HotStockRecord:
    id: int
    symbol: str
    company_name: str
    hot_score: float
    rank_date: str
    source: str
    extra_json: str
    created_at: str
