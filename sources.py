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
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

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
# Market data.
#
# History, because it cost us weeks of silently broken briefs: the first
# version used Stooq's daily CSV for everything. Stooq answers HTTP 200 with
# a body like "Exceeded the daily hits limit", so every quote parsed to None,
# every market segment was skipped, and the episodes still sounded fine. The
# 2026-11-06 brief literally said "data feed's down -- I can't give you the
# scoreboard today" and nobody noticed for days.
#
# Two rules are baked in below: lead with a source known to work from CI, and
# make every failure loud, specific and counted.
#
#   equities / commodities / crypto -> Yahoo chart API  (Stooq as fallback)
#   Treasury yields                 -> US Treasury daily curve CSV
#
# The brief now runs AFTER the opening bell, so quotes are the live session
# move where the market is open, and the last completed close where it isn't.
# --------------------------------------------------------------------------

# (yahoo symbol, stooq symbol, label)
US_INDICES = [
    ("^GSPC", "^spx", "S&P 500"),
    ("^IXIC", "^ndq", "Nasdaq Composite"),
    ("^DJI", "^dji", "Dow Jones Industrial Average"),
    ("^RUT", "^rut", "Russell 2000"),
]

FOREIGN_INDICES = [
    ("^FTSE", "^ukx", "FTSE 100 (UK)"),
    ("^GDAXI", "^dax", "DAX (Germany)"),
    ("^FCHI", "^cac", "CAC 40 (France)"),
    ("^N225", "^nkx", "Nikkei 225 (Japan)"),
    ("^HSI", "^hsi", "Hang Seng (Hong Kong)"),
    ("000001.SS", "^shc", "Shanghai Composite (China)"),
    ("^KS11", "^kospi", "KOSPI (South Korea)"),
]

# The eleven SPDR sector funds -- the standard free proxy for sector moves.
SECTORS = [
    ("XLK", "xlk.us", "Technology"),
    ("XLF", "xlf.us", "Financials"),
    ("XLE", "xle.us", "Energy"),
    ("XLV", "xlv.us", "Health Care"),
    ("XLI", "xli.us", "Industrials"),
    ("XLY", "xly.us", "Consumer Discretionary"),
    ("XLP", "xlp.us", "Consumer Staples"),
    ("XLU", "xlu.us", "Utilities"),
    ("XLB", "xlb.us", "Materials"),
    ("XLRE", "xlre.us", "Real Estate"),
    ("XLC", "xlc.us", "Communication Services"),
]

OTHER = [
    ("^VIX", "^vix", "VIX volatility index"),
    ("CL=F", "cl.f", "WTI crude oil"),
    ("GC=F", "gc.f", "Gold"),
    ("BTC-USD", "btcusd", "Bitcoin"),
]

TREASURY_TENORS = [("2 Yr", "US 2-year Treasury yield"),
                   ("10 Yr", "US 10-year Treasury yield"),
                   ("30 Yr", "US 30-year Treasury yield")]

FOREIGN_ALERT_PCT = 3.0

_failures: list[str] = []


def market_session() -> dict:
    """Where we are in the US trading day, from the clock in New York.

    Holidays aren't modelled -- on Thanksgiving this says "open" and the
    quotes simply show the prior close, which reads correctly anyway because
    every quote carries its own as-of date.
    """
    now_et = datetime.now(ZoneInfo("America/New_York"))
    open_t = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    weekday = now_et.weekday() < 5
    is_open = weekday and open_t <= now_et <= close_t

    if is_open:
        mins = int((now_et - open_t).total_seconds() // 60)
        state = f"OPEN, {mins} minutes into the session"
    elif weekday and now_et < open_t:
        mins = int((open_t - now_et).total_seconds() // 60)
        state = f"PRE-MARKET, {mins} minutes before the opening bell"
    elif weekday:
        state = "CLOSED for the day"
    else:
        state = "CLOSED for the weekend"

    return {"is_open": is_open, "state": state,
            "now_et": now_et.strftime("%H:%M ET on %A")}


def _note_failure(label: str, detail: str) -> None:
    _failures.append(f"{label}: {detail}")


def _yahoo_quote(symbol: str) -> tuple[float, float, str, bool]:
    """Current price vs. the prior close, from Yahoo's chart endpoint.

    `meta.regularMarketPrice` is live during the session, which is the whole
    point of running after the bell. If the meta block is missing we fall back
    to diffing the last two daily bars.
    """
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(symbol)}"
           "?range=5d&interval=1d")
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:80]}")

    result = (resp.json().get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError(f"no result in payload: {resp.text[:80]}")

    meta = result[0].get("meta") or {}
    last = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")

    if isinstance(last, (int, float)) and isinstance(prev, (int, float)) and prev:
        stamp = meta.get("regularMarketTime")
        as_of = (datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d")
                 if stamp else datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        return float(last), float(prev), as_of, True

    stamps = result[0].get("timestamp") or []
    closes = ((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    pairs = [(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"), c)
             for t, c in zip(stamps, closes) if c is not None]
    if len(pairs) < 2:
        raise RuntimeError(f"no meta price and only {len(pairs)} usable closes")
    return pairs[-1][1], pairs[-2][1], pairs[-1][0], False


def _stooq_quote(symbol: str) -> tuple[float, float, str, bool]:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=21)
    url = ("https://stooq.com/q/d/l/"
           f"?s={symbol}&d1={start:%Y%m%d}&d2={today:%Y%m%d}&i=d")
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:80]}")

    closes = []
    for row in csv.DictReader(io.StringIO(resp.text)):
        try:
            closes.append((row["Date"], float(row["Close"])))
        except (KeyError, TypeError, ValueError):
            continue
    if len(closes) < 2:
        # The exact failure that went unnoticed for weeks. Say what came back.
        raise RuntimeError(
            f"only {len(closes)} usable rows; body began {resp.text[:70]!r}")
    return closes[-1][1], closes[-2][1], closes[-1][0], False


def _quote(yahoo_sym: str, stooq_sym: str, label: str, kind: str) -> dict | None:
    errors = []
    for provider, fn, symbol in (("yahoo", _yahoo_quote, yahoo_sym),
                                 ("stooq", _stooq_quote, stooq_sym)):
        try:
            last, prev, as_of, live = fn(symbol)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{provider} {exc}")
            continue

        change = last - prev
        return {
            "symbol": yahoo_sym, "label": label, "kind": kind,
            "provider": provider, "live": live,
            "last": round(last, 2), "change": round(change, 3),
            "pct_change": round((change / prev * 100) if prev else 0.0, 2),
            "as_of": as_of,
        }

    _note_failure(label, " | ".join(errors)[:170])
    return None


def _treasury_curve() -> list[dict]:
    """The official daily Treasury yield curve. Free, no key, no scraping."""
    year = datetime.now(timezone.utc).year
    rows: list[dict] = []
    for y in (year, year - 1):  # January needs last year's tail
        url = ("https://home.treasury.gov/resource-center/data-chart-center/"
               f"interest-rates/daily-treasury-rates.csv/{y}/all"
               f"?type=daily_treasury_yield_curve&field_tdr_date_value={y}"
               "&page&_format=csv")
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=25)
            resp.raise_for_status()
            rows.extend(list(csv.DictReader(io.StringIO(resp.text))))
        except Exception as exc:  # noqa: BLE001
            _note_failure(f"Treasury curve {y}", str(exc)[:120])
        if len(rows) >= 2:
            break

    def when(row):
        try:
            return datetime.strptime(row["Date"], "%m/%d/%Y")
        except (KeyError, ValueError):
            return None

    rows = sorted([r for r in rows if when(r)], key=when)
    if len(rows) < 2:
        _note_failure("Treasury curve", f"only {len(rows)} dated rows")
        return []

    latest, prior = rows[-1], rows[-2]
    out = []
    for column, label in TREASURY_TENORS:
        try:
            last, prev = float(latest[column]), float(prior[column])
        except (KeyError, TypeError, ValueError):
            _note_failure(label, f"column {column!r} missing or unparseable")
            continue
        out.append({"symbol": column, "label": label, "kind": "yield",
                    "provider": "treasury.gov", "live": False,
                    "last": round(last, 2), "change": round(last - prev, 3),
                    "bps_change": round((last - prev) * 100),
                    "as_of": latest["Date"]})
    return out


def fetch_market_snapshot() -> dict:
    """Every tracked market, grouped, fetched concurrently."""
    _failures.clear()
    groups = {
        "us_indices": (US_INDICES, "index"),
        "foreign_indices": (FOREIGN_INDICES, "index"),
        "sectors": (SECTORS, "sector"),
        "other": (OTHER, "other"),
    }
    jobs = [(g, y, s, lab, kind)
            for g, (syms, kind) in groups.items() for y, s, lab in syms]

    out: dict[str, list[dict]] = {g: [] for g in groups}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_quote, y, s, lab, kind): g
                   for g, y, s, lab, kind in jobs}
        for fut in as_completed(futures):
            quote = fut.result()
            if quote:
                out[futures[fut]].append(quote)

    out["rates"] = _treasury_curve()
    out["session"] = market_session()

    for group, (syms, _) in groups.items():
        order = {y: i for i, (y, _, _) in enumerate(syms)}
        out[group].sort(key=lambda q: order.get(q["symbol"], 99))
    out["sectors"].sort(key=lambda q: q["pct_change"], reverse=True)

    got = sum(len(out[g]) for g in list(groups) + ["rates"])
    want = len(jobs) + len(TREASURY_TENORS)
    print(f"  + {got} of {want} instruments returned data")

    providers: dict[str, int] = {}
    live = 0
    for g in list(groups) + ["rates"]:
        for q in out[g]:
            providers[q["provider"]] = providers.get(q["provider"], 0) + 1
            live += bool(q.get("live"))
    if providers:
        print("    via " + ", ".join(f"{k}: {v}" for k, v in sorted(providers.items()))
              + f"  ({live} live intraday)")
    for failure in _failures[:12]:
        print(f"  ! {failure}")
    if len(_failures) > 12:
        print(f"  ! ...and {len(_failures) - 12} more")
    return out


def has_quotes(snapshot: dict) -> bool:
    return any(snapshot.get(g) for g in
               ("us_indices", "foreign_indices", "sectors", "rates", "other"))


def _fmt_level(q: dict) -> str:
    return f"{q['label']}: {q['last']:,.2f} ({q['pct_change']:+.2f}%)"


def format_context(headlines: list[dict], snapshot: dict) -> str:
    """Flatten everything into the plain-text block handed to Claude.

    Laid out in the show's running order so the model doesn't have to hunt
    for the numbers belonging to each segment.
    """
    lines: list[str] = []
    snapshot = snapshot or {}
    session = snapshot.get("session") or market_session()

    lines.append(f"SESSION: US market is {session['state']} ({session['now_et']}).")
    if session["is_open"]:
        lines.append("Index, sector, commodity and crypto figures below are TODAY'S "
                     "move so far, live, not yesterday's close. Say so: these are "
                     "moves happening right now.")
    else:
        lines.append("Index, sector, commodity and crypto figures below are the last "
                     "completed session's close, not a live move.")
    lines.append("Treasury yields are always the last published daily curve.")
    lines.append("")

    def section(title, quotes, formatter=_fmt_level):
        lines.append(title)
        if not quotes:
            lines.append("  (unavailable -- skip this segment, do not mention the gap)")
        for q in quotes:
            lines.append("  " + formatter(q))
        lines.append("")

    section("US INDICES:", snapshot.get("us_indices", []))

    foreign = snapshot.get("foreign_indices", [])
    lines.append("FOREIGN INDICES:")
    if not foreign:
        lines.append("  (unavailable -- skip, do not mention the gap)")
    for q in foreign:
        flag = ("   <-- BIG MOVE, worth naming"
                if abs(q["pct_change"]) >= FOREIGN_ALERT_PCT else "")
        lines.append("  " + _fmt_level(q) + flag)
    lines.append("")

    section("EQUITY SECTORS, best to worst (SPDR sector ETFs):",
            snapshot.get("sectors", []),
            lambda q: f"{q['label']}: {q['pct_change']:+.2f}%")

    rates = snapshot.get("rates", [])
    lines.append("FIXED INCOME (US Treasury daily curve):")
    if not rates:
        lines.append("  (unavailable -- skip, do not mention the gap)")
    for q in rates:
        lines.append(f"  {q['label']}: {q['last']:.2f}% ({q['bps_change']:+d} bps)")
    by_tenor = {q["symbol"]: q for q in rates}
    if "2 Yr" in by_tenor and "10 Yr" in by_tenor:
        spread = by_tenor["10 Yr"]["last"] - by_tenor["2 Yr"]["last"]
        lines.append(f"  2s10s curve: {spread * 100:+.0f} bps "
                     f"({'positive' if spread > 0 else 'inverted'})")
    lines.append("")

    section("COMMODITIES, VOLATILITY, CRYPTO:", snapshot.get("other", []))

    lines.append(
        f"HEADLINES ({len(headlines)} from the last ~30 hours). "
        "Each is numbered -- cite the numbers you actually used:")
    for i, h in enumerate(headlines):
        stamp = (h["published"] or "")[:16].replace("T", " ")
        lines.append(f"[{i}] ({h['source']} {stamp}) {h['title']}")
        if h["summary"]:
            lines.append(f"     {h['summary']}")

    return "\n".join(lines)
