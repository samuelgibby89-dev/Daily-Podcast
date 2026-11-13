#!/usr/bin/env python3
"""Build one day of the brief -- every topic, at both lengths -- into ./public.

Run locally:        python build.py
Dry run (no API):   python build.py --dry-run
One topic only:     python build.py --topics markets
Check the feeds:    python build.py --check-feeds
Backfill a date:    python build.py --date 2026-08-11

Each weekday this produces, for every topic in topics.py:

    a ~45-second cut   stitched with other topics into one continuous brief
    a ~3-minute cut    played when that topic was picked on its own

Both come from a single model call per topic, so six topics cost six calls,
not twelve. The twelve speech passes run in parallel.

The output directory is a complete static site: index.html, episodes.json,
feed.xml, audio/, transcripts/, covers/. Anything that can serve static files
can host it -- GitHub Pages is just the free option.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape

import cover
import sources
import topics as topics_cfg
import tts

ROOT = Path(__file__).parent
WEB = ROOT / "web"
PUBLIC = Path(os.getenv("PUBLIC_DIR", ROOT / "public"))

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
RETAIN = int(os.getenv("RETAIN_EPISODES", "30"))
SHOW_TITLE = os.getenv("SHOW_TITLE", "The Morning Brief")
SHOW_DESC = os.getenv(
    "SHOW_DESCRIPTION",
    "A daily brief you build yourself: markets, tech, sport, business, "
    "the world, science and health.",
)
SHOW_AUTHOR = os.getenv("SHOW_AUTHOR", "The Morning Brief")

# How many speech passes to run at once. Microsoft's endpoint hands out 403s
# when leaned on, and twelve simultaneous websockets is leaning on it; four
# keeps the whole batch inside about ninety seconds anyway.
TTS_WORKERS = int(os.getenv("TTS_WORKERS", "4"))

# episodes.json schema version. The player checks this, so an old home-screen
# app that hasn't refreshed its shell shows a "reload" prompt rather than
# silently rendering an empty archive.
SCHEMA = 2

_print_lock = Lock()


class _ThreadRouter(io.TextIOBase):
    """A stdout stand-in that gives each worker thread its own buffer.

    `contextlib.redirect_stdout` cannot do this: it replaces the single
    process-wide `sys.stdout`, so four threads entering it concurrently
    clobber each other's buffers and swallow the main thread's output too. The
    first version of this build did exactly that and lost eleven of twelve log
    lines -- which, in a project whose worst bugs were all invisible failures,
    is precisely the mistake not to make in the logging.

    Writes go to the calling thread's registered buffer if it has one, and
    straight through to the real stream otherwise.
    """

    def __init__(self, real):
        self._real = real
        self._local = threading.local()

    def register(self, buf) -> None:
        self._local.buf = buf

    def unregister(self) -> None:
        self._local.buf = None

    @property
    def real(self):
        return self._real

    def write(self, s: str) -> int:
        buf = getattr(self._local, "buf", None)
        return (buf if buf is not None else self._real).write(s)

    def flush(self) -> None:
        if getattr(self._local, "buf", None) is None:
            self._real.flush()

    def isatty(self) -> bool:
        return self._real.isatty()


_OUT = _ThreadRouter(sys.stdout)
_ERR = _ThreadRouter(sys.stderr)
sys.stdout = _OUT
sys.stderr = _ERR


def say(block: str) -> None:
    """Print a whole block atomically, bypassing any thread capture.

    Twelve threads printing concurrently interleave into unreadable mush, and
    this project has lost real time to logs that couldn't be trusted.
    """
    with _print_lock:
        _OUT.real.write(block + ("" if block.endswith("\n") else "\n"))
        _OUT.real.flush()


def captured(fn, *args, **kwargs):
    """Run fn with this thread's stdout/stderr captured.

    Returns (result, text, error). Never raises: a topic that blows up must
    not take the other five down with it.
    """
    buf = io.StringIO()
    _OUT.register(buf)
    _ERR.register(buf)
    try:
        return fn(*args, **kwargs), buf.getvalue(), None
    except Exception as exc:  # noqa: BLE001
        return None, buf.getvalue(), exc
    finally:
        _OUT.unregister()
        _ERR.unregister()


def site_url() -> str:
    """Public base URL, auto-derived from the repo when running in Actions."""
    explicit = os.getenv("SITE_URL")
    if explicit:
        return explicit.rstrip("/")
    repo = os.getenv("GITHUB_REPOSITORY")
    if repo and "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}"
    return ""


# --------------------------------------------------------------------------
# Script generation
# --------------------------------------------------------------------------

# The segment comes back as a forced tool call rather than as JSON in a text
# reply. A 500-word script is full of quotes, apostrophes and newlines, and
# hand-parsing that out of prose is how you end up shipping an empty episode.
SEGMENT_TOOL = {
    "name": "publish_segment",
    "description": "Publish this topic's finished segment, at both lengths.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Six to eight words capturing this topic's main story today.",
            },
            "teaser": {
                "type": "string",
                "description": "One sentence under 25 words, shown under the play button.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Three to five short tags for this segment.",
            },
            "sources": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Numbers of the headlines you actually drew on, from the "
                    "numbered list you were given. Six to ten is typical."
                ),
            },
            "script_long": {
                "type": "string",
                "description": (
                    "The ~3-minute version, 420-480 words, hard maximum 500. "
                    "Plain spoken prose only: no markdown, no headers, no "
                    "bullets, no stage directions."
                ),
            },
            "script_short": {
                "type": "string",
                "description": (
                    "The ~45-second cut, 100-125 words, hard maximum 135. "
                    "No greeting, no sign-off, no reference to other segments "
                    "-- it gets stitched between them. Plain spoken prose only."
                ),
            },
            "weight": {
                "type": "number",
                "description": (
                    "How big this topic's day was, 0 to 1, judged against a "
                    "normal day for this topic. Decides whose photo becomes "
                    "the cover when several topics are stacked."
                ),
            },
            "cover_story": {
                "type": "integer",
                "description": (
                    "The headline number whose article photo should represent "
                    "this segment."
                ),
            },
            "cover_is_sensitive": {
                "type": "boolean",
                "description": (
                    "True if that story's photo is likely to show something "
                    "graphic or distressing. When true a designed cover is "
                    "used instead. Err towards true when unsure."
                ),
            },
        },
        "required": ["title", "teaser", "tags", "sources", "script_long",
                     "script_short", "weight", "cover_story",
                     "cover_is_sensitive"],
    },
}


def system_prompt_for(topic) -> str:
    template = (ROOT / "segment_prompt.md").read_text(encoding="utf-8")
    extra = topic.extra_prompt.strip()
    return (
        template
        .replace("{LABEL}", topic.label)
        .replace("{LONG_ORDER}", topic.long_order.strip())
        .replace("{SHORT_ORDER}", topic.short_order.strip())
        .replace("{EXTRA}", ("\n" + extra) if extra else "")
    )


def write_segment(topic, context: str, date_label: str) -> dict:
    from anthropic import Anthropic

    # The SDK defaults to a 10-minute timeout and retries on top of it, which
    # on its own can outlast the whole job. A segment that takes three minutes
    # to write is already wrong.
    client = Anthropic(timeout=180.0, max_retries=2)  # reads ANTHROPIC_API_KEY

    user_msg = (
        f"Today is {date_label}.\n\n"
        f"Here is everything gathered this morning for the {topic.label} "
        "segment. Write both versions and publish them with the "
        "publish_segment tool.\n\n"
        f"{context}"
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=6000,
        system=system_prompt_for(topic),
        messages=[{"role": "user", "content": user_msg}],
        tools=[SEGMENT_TOOL],
        tool_choice={"type": "tool", "name": "publish_segment"},
    )

    for block in resp.content:
        if getattr(block, "type", "") == "tool_use" and block.name == "publish_segment":
            return normalize_segment(block.input)

    raise RuntimeError("model did not call publish_segment despite a forced tool choice")


def as_list(value) -> list:
    """Treat a field as a list, even when the model sent something else.

    Tool schemas guide generation, they don't guarantee it. A `tags` field
    declared as an array of strings can still come back as the single string
    "AI infrastructure, Oracle, Oil" -- and iterating that yields
    one-character tags, which is exactly how "A", "I", " ", "i", "n" ended up
    rendered as tags on the live site.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,;|]|\band\b", value) if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def normalize_segment(data: dict) -> dict:
    """Coerce a tool payload into the shape the rest of the build wants."""
    long_script = (data.get("script_long") or "").strip()
    short_script = (data.get("script_short") or "").strip()
    if not long_script:
        raise RuntimeError("segment came back with no long script")
    if not short_script:
        raise RuntimeError("segment came back with no short script")

    cited = []
    for n in as_list(data.get("sources")):
        try:
            cited.append(int(n))
        except (TypeError, ValueError):
            continue

    tags = [str(t).strip() for t in as_list(data.get("tags"))]
    tags = [t for t in tags if 1 < len(t) <= 40][:5]

    try:
        cover_story = int(data.get("cover_story"))
    except (TypeError, ValueError):
        cover_story = cited[0] if cited else -1

    try:
        weight = float(data.get("weight"))
    except (TypeError, ValueError):
        weight = 0.5
    weight = min(1.0, max(0.0, weight))

    return {
        "title": (data.get("title") or "Brief").strip(),
        "teaser": (data.get("teaser") or "").strip(),
        "tags": tags,
        "cited": cited,
        "weight": weight,
        "cover_story": cover_story,
        "cover_is_sensitive": bool(data.get("cover_is_sensitive")),
        "script_long": long_script,
        "script_short": short_script,
    }


def resolve_sources(cited: list[int], headlines: list[dict]) -> list[dict]:
    """Turn the headline numbers the model cited into linkable sources.

    Falls back to the freshest headlines if the model cited nothing usable,
    so the Sources panel is never mysteriously empty.
    """
    picked, seen = [], set()
    for i in cited:
        if 0 <= i < len(headlines) and i not in seen:
            seen.add(i)
            picked.append(headlines[i])

    if not picked:
        print("  ! cited no usable sources; falling back to top headlines")
        picked = headlines[:8]

    return [
        {"title": h["title"], "source": h["source"], "link": h["link"]}
        for h in picked[:12]
        if h.get("link")
    ]


# --------------------------------------------------------------------------
# One topic, end to end (headlines -> scripts -> cover)
# --------------------------------------------------------------------------

def build_topic(topic, date_str: str, date_label: str,
                snapshot: dict | None) -> dict | None:
    """Everything for one topic except the audio. Returns None if skipped."""
    print(f"[{topic.slug}] fetching {len(topic.feeds)} feeds ...")
    headlines = sources.fetch_headlines(topic.feeds, quiet=True)
    usable = [h for h in headlines if h.get("link")]
    print(f"[{topic.slug}] {len(headlines)} headlines ({len(usable)} with links)")

    if len(usable) < topics_cfg.MIN_HEADLINES:
        print(f"[{topic.slug}] SKIPPED: only {len(usable)} usable headlines, "
              f"need {topics_cfg.MIN_HEADLINES}. Check the feeds with "
              f"--check-feeds.")
        return None

    if topic.needs_market_data:
        if not sources.has_quotes(snapshot or {}):
            # Three of the markets running order's six beats are data. A
            # markets brief with none of it still sounds completely plausible
            # -- that is exactly how a dead quote feed went unnoticed for
            # weeks, until an episode said "data feed's down" out loud. Skip
            # the segment rather than ship a hollow one that reads as fine.
            print(f"[{topic.slug}] SKIPPED: no market data at all. Half this "
                  f"topic's running order depends on it.")
            return None
        context = sources.format_context(headlines, snapshot or {})
    else:
        context = sources.format_headlines(
            headlines, f"Material for the {topic.label} segment.")

    print(f"[{topic.slug}] writing with {MODEL} ...")
    t0 = time.monotonic()
    seg = write_segment(topic, context, date_label)
    long_words = len(seg["script_long"].split())
    short_words = len(seg["script_short"].split())
    print(f'[{topic.slug}] "{seg["title"]}" '
          f'({long_words}w long / {short_words}w short, '
          f'weight {seg["weight"]:.2f}, {time.monotonic() - t0:.0f}s)')

    if long_words < 150:
        print(f"[{topic.slug}] SKIPPED: long script came back at only "
              f"{long_words} words; refusing to publish a broken segment "
              f"over yesterday's good one.")
        return None
    if short_words < 50:
        print(f"[{topic.slug}] SKIPPED: short cut came back at only "
              f"{short_words} words.")
        return None
    if long_words > 560:
        print(f"[{topic.slug}] ! long ran to {long_words} words -- tighten "
              f"long_order in topics.py if this keeps happening")
    if short_words > 170:
        print(f"[{topic.slug}] ! short cut ran to {short_words} words, which "
              f"will push a four-topic brief past three minutes")

    seg["slug"] = topic.slug
    seg["label"] = topic.label
    seg["sources"] = resolve_sources(seg.pop("cited", []), headlines)
    print(f"[{topic.slug}] {len(seg['sources'])} sources cited")

    seg["cover"] = cover.build_cover(seg, headlines, date_str, PUBLIC,
                                     topic, snapshot)
    return seg


# --------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------

def load_episodes() -> list[dict]:
    path = PUBLIC / "episodes.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("episodes", []) if isinstance(data, dict) else data
    except Exception as exc:  # noqa: BLE001
        print(f"  ! episodes.json unreadable ({exc}), starting a fresh index")
        return []


def prune(episodes: list[dict]) -> list[dict]:
    """Keep the newest RETAIN days and delete files nothing points at."""
    episodes.sort(key=lambda e: e["date"], reverse=True)
    keep, drop = episodes[:RETAIN], episodes[RETAIN:]

    for ep in drop:
        for folder in ("audio", "transcripts"):
            shutil.rmtree(PUBLIC / folder / ep["date"], ignore_errors=True)
        print(f"  - pruned {ep['date']}")

    kept_dates = {ep["date"] for ep in keep}
    for folder in ("audio", "transcripts"):
        d = PUBLIC / folder
        if not d.exists():
            continue
        for child in d.iterdir():
            # Per-day directories are the current layout; the loose
            # "<date>.mp3" files are from the single-episode era.
            name = child.name
            stem = name.split(".")[0]
            if stem not in kept_dates:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                print(f"  - removed orphan {folder}/{name}")

    covers = PUBLIC / "covers"
    if covers.exists():
        for f in covers.iterdir():
            if f.is_file() and f.name.split("-")[0:3]:
                day = "-".join(f.name.split("-")[:3])
                if day not in kept_dates:
                    f.unlink(missing_ok=True)

    return keep


def primary_segment(episode: dict) -> dict | None:
    """The segment whose story leads the day, by the model's own weighting."""
    segs = list((episode.get("segments") or {}).values())
    if not segs:
        return None
    return max(segs, key=lambda s: (s.get("weight", 0), s.get("slug", "")))


def write_feed(episodes: list[dict]) -> None:
    """RSS for podcast apps.

    A feed is a single linear show, so it carries the long markets cut -- the
    original brief, unchanged. The stitched multi-topic experience only exists
    inside the app, because only the app knows what this listener picked.
    """
    base = site_url()
    if not base:
        print("  ! SITE_URL unknown, skipping RSS feed")
        return

    items = []
    for ep in episodes:
        seg = (ep.get("segments") or {}).get("markets") or primary_segment(ep)
        if not seg or not seg.get("long"):
            continue
        audio = seg["long"]
        pub = datetime.fromisoformat(ep["published"])
        items.append(f"""    <item>
      <title>{escape(seg['title'])}</title>
      <description>{escape(seg.get('teaser', ''))}</description>
      <pubDate>{format_datetime(pub)}</pubDate>
      <guid isPermaLink="false">{escape(ep['date'])}</guid>
      <enclosure url="{escape(base + '/' + audio['audio'])}" length="{audio.get('bytes', 0)}" type="audio/mpeg"/>
      <itunes:duration>{audio.get('duration', 0)}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>""")

    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(SHOW_TITLE)}</title>
    <link>{escape(base)}/</link>
    <description>{escape(SHOW_DESC)}</description>
    <language>en-us</language>
    <lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>
    <atom:link href="{escape(base)}/feed.xml" rel="self" type="application/rss+xml"/>
    <itunes:author>{escape(SHOW_AUTHOR)}</itunes:author>
    <itunes:summary>{escape(SHOW_DESC)}</itunes:summary>
    <itunes:explicit>false</itunes:explicit>
    <itunes:category text="Business"><itunes:category text="Investing"/></itunes:category>
    <itunes:image href="{escape(base)}/icons/icon-512.png"/>
{chr(10).join(items)}
  </channel>
</rss>
"""
    (PUBLIC / "feed.xml").write_text(feed, encoding="utf-8")
    print(f"  + feed.xml ({len(items)} items)")


def copy_web_assets() -> None:
    """Refresh the player shell on every build so UI changes ship automatically."""
    for item in WEB.iterdir():
        dest = PUBLIC / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)
    (PUBLIC / ".nojekyll").touch()  # stop Pages from eating underscore paths


# --------------------------------------------------------------------------
# Feed health
# --------------------------------------------------------------------------

def check_feeds() -> int:
    """Fetch every configured feed and report what came back.

    Exists because this cannot be verified from anywhere but a machine with
    open egress, and because the expensive failure in this project has always
    been a data path that was broken for weeks while the output still looked
    plausible. Run it on the runner, not your laptop.
    """
    print(f"\n=== Feed check: {len(topics_cfg.TOPICS)} topics, "
          f"{len(topics_cfg.all_feeds())} feeds ===\n")

    worst = 0
    for topic in topics_cfg.TOPICS:
        print(f"{topic.label} ({topic.slug})")
        rows = []
        got = sources.fetch_all_feeds(topic.feeds)

        for name, _url in topic.feeds:
            entries, err = got.get(name, ([], "not fetched"))
            if err:
                rows.append(f"  DEAD  {name:24s} {err[:60]}")
                continue
            n = len(entries)
            linked = sum(1 for e in entries if getattr(e, "link", ""))
            flag = "ok  " if n >= 3 and linked else "THIN"
            first = (getattr(entries[0], "title", "") or "")[:46] if n else ""
            rows.append(f"  {flag}  {name:24s} {n:3d} entries  | {first}")
        print("\n".join(rows))

        # Reuse what was just fetched rather than hitting every feed twice.
        headlines = sources.merge_entries(topic.feeds, got, quiet=True,
                                          report_failures=False)
        usable = [h for h in headlines if h.get("link")]
        verdict = "OK" if len(usable) >= topics_cfg.MIN_HEADLINES else "WOULD SKIP"
        if verdict != "OK":
            worst = 1
        print(f"  => {len(usable)} usable headlines after de-duplication "
              f"-- {verdict}\n")

    if worst:
        print("At least one topic would be skipped today. Replace its dead "
              "feeds in topics.py.")
    else:
        print("Every topic has enough material.")
    return worst


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="use canned scripts and silent audio; no API calls")
    parser.add_argument("--date", help="episode date as YYYY-MM-DD (default: today)")
    parser.add_argument("--skip-audio", action="store_true")
    parser.add_argument("--check-feeds", action="store_true",
                        help="fetch every configured feed and report its health")
    parser.add_argument("--topics", default="",
                        help="comma-separated slugs to build (default: all)")
    parser.add_argument(
        "--skip-if-published", action="store_true",
        help="exit 0 without building if today's brief already exists. "
             "GitHub's scheduled events fire hours late and unpredictably, so "
             "the backstop cron can land long after the real trigger already "
             "produced the morning brief. Without this it would overwrite a "
             "9:45am brief with an 8pm one.")
    args = parser.parse_args()

    if args.check_feeds:
        return check_feeds()

    started = time.monotonic()
    now = datetime.now(timezone.utc)
    # Date the brief by the US market day, not UTC. A forced run on a Monday
    # evening in Denver is already Tuesday in UTC, so UTC dating labelled it
    # as the next day's brief AND consumed that slot, which would have made
    # the real Tuesday-morning run skip as "already published".
    market_today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    date_str = args.date or market_today
    date_label = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %-d, %Y")

    wanted = [s.strip() for s in args.topics.split(",") if s.strip()]
    if wanted:
        unknown = [s for s in wanted if s not in topics_cfg.BY_SLUG]
        if unknown:
            print(f"ERROR: unknown topic(s): {', '.join(unknown)}. "
                  f"Known: {', '.join(topics_cfg.BY_SLUG)}", file=sys.stderr)
            return 1
        selected = [topics_cfg.BY_SLUG[s] for s in wanted]
    else:
        selected = list(topics_cfg.TOPICS)

    PUBLIC.mkdir(parents=True, exist_ok=True)
    for folder in ("audio", "transcripts", "covers"):
        (PUBLIC / folder).mkdir(exist_ok=True)

    print(f"\n=== {SHOW_TITLE} — {date_label} ===")
    print(f"Topics: {', '.join(t.slug for t in selected)}\n")

    if args.skip_if_published:
        already = next((e for e in load_episodes() if e.get("date") == date_str), None)
        if already:
            built = (already.get("published") or "")[:16].replace("T", " ")
            n = len(already.get("segments") or {})
            print(f"Today's brief already exists ({n} segments, built "
                  f"{built} UTC). Nothing to do.\n"
                  "Pass force=true on a manual run to rebuild it.")
            return 0

    # --- gather ----------------------------------------------------------
    segments: dict[str, dict] = {}

    if args.dry_run:
        for t in selected:
            segments[t.slug] = {
                "slug": t.slug, "label": t.label,
                "title": f"Dry Run — {t.label}",
                "teaser": "A local smoke test of the full build pipeline.",
                "tags": ["testing"], "weight": 0.5,
                "sources": [{"title": "Example headline", "source": "Test Feed",
                             "link": "https://example.com/story"}],
                "cover": {"type": "generated",
                          "url": f"covers/{date_str}-{t.slug}.png",
                          "credit": "", "link": "", "story": ""},
                "script_long": (
                    f"This is a dry run of the {t.label} segment. No news was "
                    "fetched and no model was called. " * 8),
                "script_short": (
                    f"This is the short {t.label} cut. No news was fetched and "
                    "no model was called. " * 3),
            }
            cover.generate_cover(PUBLIC / f"covers/{date_str}-{t.slug}.png",
                                 t, date_str, None)
    else:
        if not os.getenv("ANTHROPIC_API_KEY"):
            print("ERROR: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
            return 1

        snapshot = None
        if any(t.needs_market_data for t in selected):
            print("Fetching market data (29 instruments, in parallel)...")
            snapshot = sources.fetch_market_snapshot()
            if not sources.has_quotes(snapshot):
                print("\nERROR: no market data at all — every price source "
                      "failed. The markets segment will be skipped; other "
                      "topics are unaffected.\n", file=sys.stderr)

        print(f"\nBuilding {len(selected)} topics in parallel...\n")
        with ThreadPoolExecutor(max_workers=len(selected)) as pool:
            futures = {
                pool.submit(captured, build_topic, t, date_str, date_label, snapshot): t
                for t in selected
            }
            for fut in as_completed(futures):
                topic = futures[fut]
                seg, log, err = fut.result()
                say(log.rstrip())
                if err:
                    say(f"[{topic.slug}] FAILED: {err.__class__.__name__}: {err}")
                elif seg:
                    segments[topic.slug] = seg

    if not segments:
        print("\nERROR: no topic produced a segment; refusing to publish an "
              "empty day.", file=sys.stderr)
        return 1

    skipped = [t.slug for t in selected if t.slug not in segments]
    if skipped:
        print(f"\n! skipped: {', '.join(skipped)}")

    # --- audio -----------------------------------------------------------
    jobs: list[tuple[str, str, str, Path]] = []
    for slug, seg in segments.items():
        for cut in ("short", "long"):
            rel = f"audio/{date_str}/{slug}-{cut}.mp3"
            jobs.append((slug, cut, rel, PUBLIC / rel))

    if not args.skip_audio:
        print(f"\nGenerating {len(jobs)} audio files "
              f"({TTS_WORKERS} at a time)...\n")

        def one(slug: str, cut: str, path: Path):
            text = segments[slug][f"script_{cut}"]
            if args.dry_run and os.getenv("DRY_RUN_SILENT_AUDIO") == "1":
                import subprocess
                path.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                     "-i", "anullsrc=r=24000:cl=mono",
                     "-t", "45" if cut == "short" else "180",
                     "-b:a", "32k", str(path)], check=True)
            else:
                tts.synthesize(text, path)
            return tts.duration_seconds(path), path.stat().st_size

        with ThreadPoolExecutor(max_workers=TTS_WORKERS) as pool:
            futures = {
                pool.submit(captured, one, slug, cut, path): (slug, cut, rel)
                for slug, cut, rel, path in jobs
            }
            for fut in as_completed(futures):
                slug, cut, rel = futures[fut]
                result, log, err = fut.result()
                if err:
                    say(f"[{slug}/{cut}] AUDIO FAILED: "
                        f"{err.__class__.__name__}: {err}\n{log.rstrip()}")
                    continue
                duration, size = result
                mins, secs = divmod(duration, 60)
                say(f"[{slug}/{cut}] {mins}:{secs:02d}, "
                    f"{size / 1024:.0f} KB")
                segments[slug].setdefault("cuts", {})[cut] = {
                    "audio": rel, "duration": duration, "bytes": size,
                }

    # A segment with no playable short cut cannot be stitched, and one with no
    # long cut cannot be played solo. Drop anything that lost both rather than
    # publishing a topic the player will fail on.
    for slug in list(segments):
        cuts = segments[slug].get("cuts") or {}
        if not args.skip_audio and not cuts:
            print(f"  ! {slug}: no audio at all, dropping from today's brief")
            del segments[slug]
    if not segments:
        print("\nERROR: every segment lost its audio; refusing to publish.",
              file=sys.stderr)
        return 1

    # --- transcripts -----------------------------------------------------
    tdir = PUBLIC / "transcripts" / date_str
    tdir.mkdir(parents=True, exist_ok=True)
    for slug, seg in segments.items():
        for cut in ("short", "long"):
            rel = f"transcripts/{date_str}/{slug}-{cut}.txt"
            (PUBLIC / rel).write_text(seg[f"script_{cut}"], encoding="utf-8")
            if (seg.get("cuts") or {}).get(cut):
                seg["cuts"][cut]["transcript"] = rel

    # --- index -----------------------------------------------------------
    published_segments = {}
    for slug, seg in segments.items():
        cuts = seg.get("cuts") or {}
        published_segments[slug] = {
            "slug": slug,
            "label": seg["label"],
            "title": seg["title"],
            "teaser": seg["teaser"],
            "tags": seg["tags"],
            "weight": seg["weight"],
            "cover": seg.get("cover"),
            "sources": seg.get("sources", []),
            "short": cuts.get("short"),
            "long": cuts.get("long"),
        }

    episode = {
        "date": date_str,
        "published": now.isoformat(),
        "segments": published_segments,
    }

    episodes = [e for e in load_episodes() if e.get("date") != date_str]
    episodes.append(episode)

    print("\nPublishing...")
    episodes = prune(episodes)
    (PUBLIC / "episodes.json").write_text(
        json.dumps(
            {"show": SHOW_TITLE, "description": SHOW_DESC,
             "schema": SCHEMA,
             "maxSelection": topics_cfg.MAX_SELECTION,
             "menu": topics_cfg.menu(),
             "updated": now.isoformat(),
             "episodes": episodes},
            indent=2,
        ),
        encoding="utf-8",
    )
    total_cuts = sum(len([c for c in (s["short"], s["long"]) if c])
                     for s in published_segments.values())
    print(f"  + episodes.json ({len(episodes)} days, "
          f"{len(published_segments)} segments today, {total_cuts} cuts)")

    write_feed(episodes)
    copy_web_assets()

    # The browser needs the VAPID public key to subscribe. Publishing it as a
    # small file keeps it out of the HTML, so rotating the key is a repo
    # variable change rather than a code edit.
    vapid_public = os.getenv("VAPID_PUBLIC_KEY", "").strip()
    if vapid_public:
        (PUBLIC / "push.json").write_text(
            json.dumps({"vapidPublicKey": vapid_public}), encoding="utf-8")

    # Hand the notifier the details, via GITHUB_OUTPUT when in Actions. The
    # build cannot know what any individual listener picked, so the
    # notification leads with the day's biggest story across all topics.
    lead = primary_segment(episode)
    out_path = os.getenv("GITHUB_OUTPUT")
    if out_path and lead:
        image = ""
        cov = lead.get("cover") or {}
        if cov.get("type") == "photo":
            image = cov.get("url", "")
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(
                f"built=true\ndate={date_str}\n"
                f"title={lead['title'].replace(chr(10), ' ')}\n"
                f"teaser={(lead.get('teaser') or '').replace(chr(10), ' ')}\n"
                f"image={image}\nsegments={len(published_segments)}\n")

    elapsed = time.monotonic() - started
    print(f"\nDone. {date_str} — {len(published_segments)} segments, "
          f"{total_cuts} audio files, {elapsed / 60:.1f} min, "
          f"published to {PUBLIC}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
