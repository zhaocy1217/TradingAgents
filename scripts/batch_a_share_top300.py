#!/usr/bin/env python3
"""
Batch-run TradingAgents for A-share symbols ranked by total market cap (top N).

Universe sources (first that succeeds):
  1) Eastmoney push2 API (no extra deps; needs network access to Eastmoney)
  2) akshare ``stock_zh_a_spot_em`` (optional: pip install -e ".[china-universe]")
  3) Wikipedia CSI 300 constituent table (Yahoo-style symbols; weight-sorted fallback)
  4) ``--symbols-file`` one Yahoo-style ticker per line (e.g. 600519.SS)

Reports are written under ``<project>/reports/a_share_<analysis_date>/`` as Markdown.

Examples:
  # Only resolve the universe and write symbol list (no LLM calls)
  python scripts/batch_a_share_top300.py --fetch-only --top 300

  # Analyze first 2 names from a hand-maintained list (DeepSeek etc. via .env)
  python scripts/batch_a_share_top300.py --symbols-file data/examples/a_share_symbols_sample.txt --max-stocks 2

Environment:
  Same API keys as the main CLI (``.env`` / TRADINGAGENTS_*). By default this
  script sets ``output_language`` to **Chinese** when ``TRADINGAGENTS_OUTPUT_LANGUAGE``
  is unset (CLI Step 3 “Chinese (中文)”). Use ``--output-language English`` or
  set ``TRADINGAGENTS_OUTPUT_LANGUAGE`` in ``.env`` to override.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

# Project root: .../TradingAgents
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

EASTMONEY_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
EASTMONEY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/center/gridlist.html",
}


@dataclass(frozen=True)
class ListedName:
    symbol: str
    name: str


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass


def latest_weekday(d: date) -> date:
    """Most recent weekday on or before ``d`` (CN holidays not subtracted)."""
    cur = d
    while cur.weekday() >= 5:
        cur -= timedelta(days=1)
    return cur


def cn_six_digit_to_yahoo(code: str) -> str:
    """Map mainland 6-digit stock code to Yahoo Finance style (``.SS`` / ``.SZ``)."""
    c = str(code).strip().zfill(6)
    if not c.isdigit() or len(c) != 6:
        raise ValueError(f"expected 6-digit A-share code, got {code!r}")
    if c.startswith(("5", "6", "9")):
        return f"{c}.SS"
    return f"{c}.SZ"


def read_symbols_file(path: Path) -> list[ListedName]:
    rows: list[ListedName] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        sym = line.split()[0]
        name = line[len(sym) :].strip() or sym
        rows.append(ListedName(symbol=sym, name=name))
    return rows


def fetch_universe_eastmoney(
    top_n: int,
    *,
    page_size: int = 100,
    use_system_proxy: bool = False,
    timeout: float = 30.0,
) -> list[ListedName]:
    import requests

    session = requests.Session()
    session.trust_env = use_system_proxy
    session.headers.update(EASTMONEY_HEADERS)

    out: list[ListedName] = []
    pn = 1
    while len(out) < top_n:
        params = {
            "pn": pn,
            "pz": min(page_size, top_n - len(out)),
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f20",
            "fs": EASTMONEY_FS,
            "fields": "f12,f14,f20",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        r = session.get(EASTMONEY_URL, params=params, timeout=timeout)
        r.raise_for_status()
        payload = r.json()
        diff = (payload.get("data") or {}).get("diff") or []
        if not diff:
            break
        for row in diff:
            code = str(row.get("f12", "")).strip()
            name = str(row.get("f14", "")).strip() or code
            if not code:
                continue
            out.append(ListedName(symbol=cn_six_digit_to_yahoo(code), name=name))
            if len(out) >= top_n:
                break
        pn += 1
        if len(diff) < params["pz"]:
            break
    return out[:top_n]


@contextlib.contextmanager
def _requests_ignore_proxy_env():
    """akshare uses ``requests.Session()`` with default ``trust_env=True``; some
    Windows setups advertise a broken system HTTP proxy and Eastmoney calls fail.
    Force new sessions to ignore proxy environment for the duration of the block.
    """
    import requests

    orig_init = requests.Session.__init__

    def patched_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        self.trust_env = False

    requests.Session.__init__ = patched_init  # type: ignore[method-assign]
    try:
        yield
    finally:
        requests.Session.__init__ = orig_init  # type: ignore[method-assign]


def _normalize_a_share_listing_code(value: object) -> str:
    if isinstance(value, (int, float)):
        return str(int(value)).zfill(6)
    s = str(value).strip()
    if s.isdigit():
        return s.zfill(6)
    try:
        return str(int(float(s))).zfill(6)
    except ValueError:
        digits = "".join(ch for ch in s if ch.isdigit())
        if len(digits) >= 6:
            return digits[-6:].zfill(6)
        raise ValueError(f"cannot parse listing code from {value!r}") from None


def fetch_universe_wikipedia_csi300(top_n: int) -> list[ListedName]:
    """CSI 300 constituents from English Wikipedia (weight order ≈ large-cap bias).

    Not identical to same-day Eastmoney market-cap sort, but gives 300 liquid
    Yahoo-format tickers when China data APIs are unreachable.
    """
    import io
    import urllib.request

    import pandas as pd

    url = "https://en.wikipedia.org/wiki/CSI_300_Index"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TradingAgents/1.0 (batch script; +https://github.com/TauricResearch/TradingAgents)"},
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        html = resp.read().decode("utf-8", "replace")

    tables = pd.read_html(io.StringIO(html))
    df = None
    for candidate in tables:
        cols = {str(c).strip() for c in candidate.columns}
        if "Ticker" in candidate.columns and "Company" in candidate.columns:
            df = candidate
            break
    if df is None:
        raise RuntimeError("Could not find CSI 300 constituents table on Wikipedia")

    weight_col = "Weighting (%)"
    if weight_col in df.columns:
        w = pd.to_numeric(df[weight_col], errors="coerce").fillna(0.0)
        df = df.assign(_w=w).sort_values("_w", ascending=False).drop(columns=["_w"])

    out: list[ListedName] = []
    for _, row in df.head(top_n).iterrows():
        cell = str(row["Ticker"]).replace("\xa0", " ").strip()
        upper = cell.upper()
        if "SSE" in upper:
            code_part = cell.split(":")[-1].strip()
        elif "SZSE" in upper:
            code_part = cell.split(":")[-1].strip()
        else:
            raise RuntimeError(f"Unexpected Wikipedia ticker cell: {cell!r}")
        code = _normalize_a_share_listing_code(code_part)
        name = str(row["Company"]).strip()
        out.append(ListedName(symbol=cn_six_digit_to_yahoo(code), name=name))
    return out


def fetch_universe_akshare(top_n: int) -> list[ListedName]:
    with _requests_ignore_proxy_env():
        import akshare as ak

        df = ak.stock_zh_a_spot_em()
        mcap_col = "总市值"
        code_col = "代码"
        name_col = "名称"
        if mcap_col not in df.columns:
            raise RuntimeError(f"akshare spot table missing {mcap_col!r}, columns={list(df.columns)}")
        df = df.sort_values(mcap_col, ascending=False, na_position="last")
        out: list[ListedName] = []
        for _, row in df.head(top_n).iterrows():
            code = _normalize_a_share_listing_code(row[code_col])
            name = str(row[name_col])
            out.append(ListedName(symbol=cn_six_digit_to_yahoo(code), name=name))
        return out


def resolve_universe(args: argparse.Namespace) -> list[ListedName]:
    if args.symbols_file:
        p = Path(args.symbols_file).expanduser()
        if not p.is_file():
            raise SystemExit(f"symbols file not found: {p}")
        rows = read_symbols_file(p)
        return rows[: args.top]

    errors: list[str] = []
    if not args.skip_eastmoney:
        try:
            return fetch_universe_eastmoney(
                args.top,
                use_system_proxy=args.use_system_proxy,
            )
        except Exception as e:
            errors.append(f"Eastmoney: {e}")

    if not args.skip_akshare:
        try:
            return fetch_universe_akshare(args.top)
        except ImportError:
            errors.append(
                "akshare not installed (optional). Install with: pip install -e \".[china-universe]\""
            )
        except Exception as e:
            errors.append(f"akshare: {e}")

    if not args.skip_wikipedia:
        try:
            return fetch_universe_wikipedia_csi300(args.top)
        except Exception as e:
            errors.append(f"Wikipedia CSI-300: {e}")

    msg = "Could not load A-share universe.\n" + "\n".join(f"  - {x}" for x in errors)
    msg += (
        "\n\nFix: use a mainland-friendly network, install akshare, or pass "
        "`--symbols-file` with one ticker per line (e.g. 600519.SS)."
    )
    raise SystemExit(msg)


def _md_section(title: str, body: object) -> str:
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2)
    text = (text or "").strip()
    if not text:
        return ""
    return f"## {title}\n\n{text}\n\n"


def compose_stock_report_md(final_state: dict, processed_signal: str) -> str:
    inv = final_state.get("investment_debate_state") or {}
    risk = final_state.get("risk_debate_state") or {}
    parts = [
        f"# {final_state.get('company_of_interest')} — TradingAgents 报告\n\n",
        f"- **分析日期**: {final_state.get('trade_date')}\n\n",
        "---\n\n",
        _md_section("市场分析", final_state.get("market_report")),
        _md_section("情绪 / 社交", final_state.get("sentiment_report")),
        _md_section("新闻与资讯", final_state.get("news_report")),
        _md_section("基本面", final_state.get("fundamentals_report")),
        _md_section("研究团队结论", inv.get("judge_decision") or inv.get("history")),
        _md_section("交易员计划", final_state.get("trader_investment_plan")),
        _md_section("风控辩论结论", risk.get("judge_decision") or risk.get("history")),
        _md_section("投资组合最终决策", final_state.get("final_trade_decision")),
        _md_section("信号摘要（解析后）", processed_signal or ""),
    ]
    return "".join(p for p in parts if p)


def safe_report_filename(symbol: str, analysis_date: str) -> str:
    return f"{symbol.replace('.', '_')}_{analysis_date}.md"


def write_index_md(
    path: Path,
    *,
    analysis_date: str,
    rows: list[tuple[ListedName, str | None]],
) -> None:
    lines = [
        f"# A-share batch — {analysis_date}\n\n",
        "| Rank | Symbol | Name | Report |\n",
        "| --- | --- | --- | --- |\n",
    ]
    for i, (ln, rel) in enumerate(rows, start=1):
        link = f"[打开](./{rel})" if rel else "—（失败或跳过）"
        lines.append(f"| {i} | `{ln.symbol}` | {ln.name} | {link} |\n")
    path.write_text("".join(lines), encoding="utf-8")


def main() -> None:
    _load_dotenv()

    p = argparse.ArgumentParser(description="Batch A-share analysis → reports/*.md")
    p.add_argument("--top", type=int, default=300, help="How many names to take by market cap (default 300)")
    p.add_argument(
        "--analysis-date",
        type=str,
        default=None,
        help="YYYY-MM-DD (default: latest weekday on or before today, local calendar)",
    )
    p.add_argument(
        "--max-stocks",
        type=int,
        default=None,
        help="Cap how many stocks to analyze after universe resolution (default: all)",
    )
    p.add_argument(
        "--symbols-file",
        type=str,
        default=None,
        help="Optional file of Yahoo tickers (overrides Eastmoney/akshare universe)",
    )
    p.add_argument(
        "--fetch-only",
        action="store_true",
        help="Only resolve symbols and write universe markdown; do not call the LLM graph",
    )
    p.add_argument(
        "--use-system-proxy",
        action="store_true",
        help="Honor HTTP(S)_PROXY from the environment for Eastmoney requests",
    )
    p.add_argument(
        "--skip-eastmoney",
        action="store_true",
        help="Do not try Eastmoney (go straight to akshare / fail)",
    )
    p.add_argument(
        "--skip-akshare",
        action="store_true",
        help="Do not try akshare fallback",
    )
    p.add_argument(
        "--skip-wikipedia",
        action="store_true",
        help="Do not try Wikipedia CSI-300 table fallback",
    )
    p.add_argument(
        "--reports-subdir",
        type=str,
        default=None,
        help="Under project reports/ (default: a_share_<analysis_date>)",
    )
    p.add_argument(
        "--output-language",
        type=str,
        default=None,
        metavar="LANG",
        help="LLM report language, e.g. Chinese or English (default: Chinese if TRADINGAGENTS_OUTPUT_LANGUAGE unset)",
    )
    args = p.parse_args()

    analysis_date = args.analysis_date
    if not analysis_date:
        analysis_date = latest_weekday(date.today()).strftime("%Y-%m-%d")

    reports_root = PROJECT_ROOT / "reports"
    sub = args.reports_subdir or f"a_share_{analysis_date}"
    out_dir = reports_root / sub
    out_dir.mkdir(parents=True, exist_ok=True)

    universe = resolve_universe(args)
    if not universe:
        raise SystemExit("Universe is empty.")

    universe_path = out_dir / f"universe_top{args.top}_{analysis_date}.md"
    uni_lines = [
        f"# A-share universe (top {len(universe)})\n\n",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}\n",
        f"- Analysis date: {analysis_date}\n\n",
        "| # | Symbol | Name |\n",
        "| --- | --- | --- |\n",
    ]
    for i, ln in enumerate(universe, start=1):
        uni_lines.append(f"| {i} | `{ln.symbol}` | {ln.name} |\n")
    universe_path.write_text("".join(uni_lines), encoding="utf-8")
    print(f"Wrote universe list: {universe_path}")

    if args.fetch_only:
        print("fetch-only: done.")
        return

    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    import os

    config = DEFAULT_CONFIG.copy()
    # If .env has DeepSeek but no OpenAI key, default_config still says "openai" — switch.
    _openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    _deepseek_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if (config.get("llm_provider") or "").lower() == "openai" and not _openai_key and _deepseek_key:
        config["llm_provider"] = "deepseek"
        config["deep_think_llm"] = "deepseek-reasoner"
        config["quick_think_llm"] = "deepseek-v4-flash"
    if args.output_language:
        config["output_language"] = args.output_language.strip()
    elif not (os.environ.get("TRADINGAGENTS_OUTPUT_LANGUAGE") or "").strip():
        # Match CLI “Chinese (中文)” when user has not pinned language via .env.
        config["output_language"] = "Chinese"

    # Match interactive CLI “Deep” depth (see cli/utils.select_research_depth).
    config["max_debate_rounds"] = 5
    config["max_risk_discuss_rounds"] = 5
    config["checkpoint_enabled"] = False
    # Full analyst stack: market, social, news, fundamentals
    selected = ["market", "social", "news", "fundamentals"]

    graph = TradingAgentsGraph(
        selected_analysts=selected,
        config=config,
        debug=False,
    )

    cap = args.max_stocks if args.max_stocks is not None else len(universe)
    to_run = universe[:cap]

    index_rows: list[tuple[ListedName, str | None]] = []
    for ln in universe:
        index_rows.append((ln, None))

    for idx, ln in enumerate(to_run):
        print(f"[{idx + 1}/{len(to_run)}] Running {ln.symbol} ({ln.name}) …")
        fname = safe_report_filename(ln.symbol, analysis_date)
        rel_name: str | None = fname
        try:
            final_state, sig = graph.propagate(ln.symbol, analysis_date)
            md = compose_stock_report_md(final_state, str(sig))
            (out_dir / fname).write_text(md, encoding="utf-8")
        except Exception as e:
            print(f"  ERROR {ln.symbol}: {e}")
            rel_name = None
            err_path = out_dir / (fname.replace(".md", "_ERROR.txt"))
            err_path.write_text(str(e), encoding="utf-8")

        # Patch index row for this symbol
        for j, (u, _) in enumerate(index_rows):
            if u.symbol == ln.symbol:
                index_rows[j] = (u, rel_name)
                break

    write_index_md(out_dir / "index.md", analysis_date=analysis_date, rows=index_rows)
    print(f"Done. Index: {out_dir / 'index.md'}")


if __name__ == "__main__":
    main()
