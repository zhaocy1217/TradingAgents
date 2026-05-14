"""A-share utilities shared by scripts and web APIs."""

from .report_formatter import compose_stock_report_md
from .universe import ListedName, resolve_top_universe

__all__ = [
    "ListedName",
    "compose_stock_report_md",
    "resolve_top_universe",
]
