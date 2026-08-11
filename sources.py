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

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# --------------------------------------------------------------------------
# News feeds. Add or remove freely -- each one is independent.
# --------------------------------------------------------------------------
FEEDS = [
    ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("MarketWatch Markets", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
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
# Market data via Stooq daily CSV (free, no key, no rate limit worth worrying
# about). We grab the last ~14 calendar days and diff the final two closes.
# --------------------------------------------------------------------------
SYMBOLS = [
    ("^spx", "S&P 500", "index"),
    ("^ndq", "Nasdaq Composite", "index"),
    ("^dji", "Dow Jones Industrial Average", "index"),
    ("^vix", "VIX volatility index", "index"),
    ("10usy.b", "US 10-year Treasury yield", "yield"),
    ("cl.f", "WTI crude oil", "commodity"),
    ("gc.f", "Gold", "commodity"),
    ("btcusd", "Bitcoin", "crypto"),
]


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


def fetch_market_snapshot() -> list[dict]:
    """Latest close and change vs. the prior close for each tracked symbol."""
    out: list[dict] = []
    for symbol, label, kind in SYMBOLS:
        result = _stooq_last_two_closes(symbol)
        if not result:
            continue
        last, prev, as_of = result
        change = last - prev
        pct = (change / prev * 100) if prev else 0.0
        out.append(
            {
                "symbol": symbol,
                "label": label,
                "kind": kind,
                "last": round(last, 2),
                "change": round(change, 2),
                "pct_change": round(pct, 2),
                "as_of": as_of,
            }
        )
        print(f"  + {label}: {last:,.2f} ({pct:+.2f}%)")
    return out


def format_context(headlines: list[dict], quotes: list[dict]) -> str:
    """Flatten everything into the plain-text block handed to Claude."""
    lines: list[str] = []

    if quotes:
        lines.append("MARKET DATA (most recent close vs. prior close):")
        for q in quotes:
            if q["kind"] == "yield":
                lines.append(
                    f"- {q['label']}: {q['last']}% "
                    f"({q['change']:+.2f} pts) as of {q['as_of']}"
                )
            else:
                lines.append(
                    f"- {q['label']}: {q['last']:,.2f} "
                    f"({q['pct_change']:+.2f}%) as of {q['as_of']}"
                )
        lines.append("")
    else:
        lines.append("MARKET DATA: unavailable this morning.\n")

    lines.append(f"HEADLINES ({len(headlines)} from the last ~30 hours):")
    for h in headlines:
        stamp = (h["published"] or "")[:16].replace("T", " ")
        lines.append(f"- [{h['source']} {stamp}] {h['title']}")
        if h["summary"]:
            lines.append(f"    {h['summary']}")

    return "\n".join(lines)
