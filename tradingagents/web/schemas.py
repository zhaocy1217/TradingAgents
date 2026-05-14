"""Pydantic request/response models for web API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FavoriteCreate(BaseModel):
    symbol: str = Field(min_length=3, max_length=16)
    company_name: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=200)


class AnalyzeRequest(BaseModel):
    query: str | None = None
    symbol: str | None = None
    analysis_date: str | None = None


class AnalyzeTopRequest(BaseModel):
    top_n: int = Field(default=10, ge=1, le=300)
    analysis_date: str | None = None
    max_stocks: int | None = Field(default=None, ge=1, le=300)
