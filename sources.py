"""Free, no-API-key data sources for the daily market brief.

Everything in here degrades gracefully: if a feed is down or a symbol
fails to fetch, the brief still gets built with whatever did come back.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# --------------------------------------------------------------------------
# News feeds. Add or remove freely -- each one is independent.
# --------------------------------------------------------------------------
FEEDS = [
    ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("Investing.com", "https://www.investing.com/rss/news_25.rss"),
    ("Seeking Alpha", "https://seekingalpha.com/market_currents.xml"),
]


def _entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        val = getattr(entry, key, None)
        if val:
            try:
                return datetime.fromtimestamp(time.mktime(val), tz=timezone.utc)
            except Exception:
                pass
    return None


def fetch_headlines(per_feed: int = 8, max_age_hours: int = 30) -> list[dict]:
    """Pull recent headlines from every feed. Returns newest-first."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items: list[dict] = []
    seen_titles: set[str] = set()

    for name, url in FEEDS:
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! feed failed: {name} ({exc.__class__.__name__}: {exc})")
            continue

        added = 0
        for entry in parsed.entries:
            if added >= per_feed:
                break
            title = (getattr(entry, "title", "") or "").strip()
            if not title:
                continue
            key = title.lower()[:90]
            if key in seen_titles:
                continue

            published = _entry_time(entry)
            if published and published < cutoff:
                continue

            summary = (getattr(entry, "summary", "") or "").strip()
            # strip any HTML the feed smuggled into the summary
            if "<" in summary:
                import re

                summary = re.sub(r"<[^>]+>", " ", summary)
            summary = " ".join(summary.split())[:400]

            seen_titles.add(key)
            items.append(
                {
                    "source": name,
                    "title": title,
                    "summary": summary,
                    "link": getattr(entry, "link", ""),
                    "published": published.isoformat() if published else None,
                }
            )
            added += 1

        print(f"  + {name}: {added} headlines")

    items.sort(key=lambda i: i["published"] or "", reverse=True)
    return items


# --------------------------------------------------------------------------
# Market data via Stooq daily CSV (free, no key). Grouped to match the running
# order of the show: what the US did, what the rest of the world did, which
# sectors led and lagged, then rates.
#
# Sectors are the SPDR sector ETFs -- the standard free proxy for sector
# performance. A symbol Stooq doesn't recognise is skipped with a warning
# rather than failing the build, so adding speculative tickers here is cheap.
# --------------------------------------------------------------------------

US_INDICES = [
    ("^spx", "S&P 500"),
    ("^ndq", "Nasdaq Composite"),
    ("^dji", "Dow Jones Industrial Average"),
    ("^rut", "Russell 2000"),
]

FOREIGN_INDICES = [
    ("^ukx", "FTSE 100 (UK)"),
    ("^dax", "DAX (Germany)"),
    ("^cac", "CAC 40 (France)"),
    ("^nkx", "Nikkei 225 (Japan)"),
    ("^hsi", "Hang Seng (Hong Kong)"),
    ("^shc", "Shanghai Composite (China)"),
    ("^kospi", "KOSPI (South Korea)"),
]

SECTORS = [
    ("xlk.us", "Technology"),
    ("xlf.us", "Financials"),
    ("xle.us", "Energy"),
    ("xlv.us", "Health Care"),
    ("xli.us", "Industrials"),
    ("xly.us", "Consumer Discretionary"),
    ("xlp.us", "Consumer Staples"),
    ("xlu.us", "Utilities"),
    ("xlb.us", "Materials"),
    ("xlre.us", "Real Estate"),
    ("xlc.us", "Communication Services"),
]

RATES = [
    ("2usy.b", "US 2-year Treasury yield"),
    ("10usy.b", "US 10-year Treasury yield"),
    ("30usy.b", "US 30-year Treasury yield"),
]

OTHER = [
    ("^vix", "VIX volatility index"),
    ("cl.f", "WTI crude oil"),
    ("gc.f", "Gold"),
    ("btcusd", "Bitcoin"),
]

# A foreign index moving this much is worth calling out by name.
FOREIGN_ALERT_PCT = 3.0


def _stooq_last_two_closes(symbol: str) -> tuple[float, float, str] | None:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=14)
    url = (
        "https://stooq.com/q/d/l/"
        f"?s={symbol}&d1={start:%Y%m%d}&d2={today:%Y%m%d}&i=d"
    )
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(resp.text)))
    except Exception as exc:  # noqa: BLE001
        print(f"  ! quote failed: {symbol} ({exc})")
        return None

    closes = []
    for row in rows:
        try:
            closes.append((row["Date"], float(row["Close"])))
        except (KeyError, TypeError, ValueError):
            continue

    if len(closes) < 2:
        return None
    prev_close = closes[-2][1]
    last_date, last_close = closes[-1]
    return last_close, prev_close, last_date


def _quote(symbol: str, label: str, kind: str) -> dict | None:
    result = _stooq_last_two_closes(symbol)
    if not result:
        return None
    last, prev, as_of = result
    change = last - prev
    return {
        "symbol": symbol,
        "label": label,
        "kind": kind,
        "last": round(last, 2),
        "change": round(change, 3),
        "pct_change": round((change / prev * 100) if prev else 0.0, 2),
        "bps_change": round(change * 100),      # only meaningful for yields
        "as_of": as_of,
    }


def fetch_market_snapshot() -> dict:
    """Every tracked market, grouped, fetched concurrently.

    Roughly thirty small CSV requests. Serially that's a slow minute of the
    build spent waiting on the network, so they go out in parallel.
    """
    groups = {
        "us_indices": (US_INDICES, "index"),
        "foreign_indices": (FOREIGN_INDICES, "index"),
        "sectors": (SECTORS, "sector"),
        "rates": (RATES, "yield"),
        "other": (OTHER, "other"),
    }

    jobs = []
    for group, (symbols, kind) in groups.items():
        for symbol, label in symbols:
            jobs.append((group, symbol, label, kind))

    out: dict[str, list[dict]] = {g: [] for g in groups}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_quote, sym, lab, kind): (group, lab)
            for group, sym, lab, kind in jobs
        }
        for fut in as_completed(futures):
            group, label = futures[fut]
            try:
                quote = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"  ! quote errored: {label} ({exc})")
                continue
            if quote:
                out[group].append(quote)

    # Stable, meaningful ordering: declared order for indices and rates,
    # best-to-worst for sectors since that is how they get read out.
    for group, (symbols, _) in groups.items():
        order = {s: i for i, (s, _) in enumerate(symbols)}
        out[group].sort(key=lambda q: order.get(q["symbol"], 99))
    out["sectors"].sort(key=lambda q: q["pct_change"], reverse=True)

    total = sum(len(v) for v in out.values())
    print(f"  + {total} of {len(jobs)} instruments returned data")
    for group in out:
        missing = len(groups[group][0]) - len(out[group])
        if missing:
            print(f"    ({missing} unavailable in {group})")
    return out


def has_quotes(snapshot: dict) -> bool:
    return any(snapshot.get(g) for g in snapshot)


def _fmt_level(q: dict) -> str:
    return f"{q['label']}: {q['last']:,.2f} ({q['pct_change']:+.2f}%)"


def format_context(headlines: list[dict], snapshot: dict) -> str:
    """Flatten everything into the plain-text block handed to Claude.

    Laid out in the show's running order so the model doesn't have to hunt
    for the numbers belonging to each segment.
    """
    lines: list[str] = []
    snapshot = snapshot or {}

    def section(title: str, quotes: list[dict], formatter=_fmt_level):
        lines.append(title)
        if not quotes:
            lines.append("  (unavailable this morning)")
        for q in quotes:
            lines.append("  " + formatter(q))
        lines.append("")

    section("US INDICES (last close vs. prior close):", snapshot.get("us_indices", []))

    foreign = snapshot.get("foreign_indices", [])
    lines.append("FOREIGN INDICES (last close vs. prior close):")
    if not foreign:
        lines.append("  (unavailable this morning)")
    for q in foreign:
        flag = ""
        if abs(q["pct_change"]) >= FOREIGN_ALERT_PCT:
            flag = "   <-- BIG MOVE, worth naming in the brief"
        lines.append("  " + _fmt_level(q) + flag)
    lines.append("")

    section(
        "EQUITY SECTORS, best to worst (SPDR sector ETFs):",
        snapshot.get("sectors", []),
        lambda q: f"{q['label']}: {q['pct_change']:+.2f}%",
    )

    rates = snapshot.get("rates", [])
    lines.append("FIXED INCOME:")
    if not rates:
        lines.append("  (unavailable this morning)")
    for q in rates:
        lines.append(f"  {q['label']}: {q['last']:.2f}% ({q['bps_change']:+d} bps)")
    by_symbol = {q["symbol"]: q for q in rates}
    if "2usy.b" in by_symbol and "10usy.b" in by_symbol:
        spread = by_symbol["10usy.b"]["last"] - by_symbol["2usy.b"]["last"]
        lines.append(f"  2s10s curve: {spread * 100:+.0f} bps "
                     f"({'steepening' if spread > 0 else 'inverted'})")
    lines.append("")

    section("COMMODITIES, VOLATILITY, CRYPTO:", snapshot.get("other", []))

    lines.append(
        f"HEADLINES ({len(headlines)} from the last ~30 hours). "
        "Each is numbered -- cite the numbers you actually used:"
    )
    for i, h in enumerate(headlines):
        stamp = (h["published"] or "")[:16].replace("T", " ")
        lines.append(f"[{i}] ({h['source']} {stamp}) {h['title']}")
        if h["summary"]:
            lines.append(f"     {h['summary']}")

    return "\n".join(lines)
