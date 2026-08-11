#!/usr/bin/env python3
"""Build one episode of the daily market brief and publish it into ./public.

Run locally:      python build.py
Dry run (no API): python build.py --dry-run
Backfill a date:  python build.py --date 2026-08-11

The output directory is a complete static site: index.html, episodes.json,
feed.xml, audio/, transcripts/. Anything that can serve static files can host
it -- GitHub Pages is just the free option.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import sources
import tts

ROOT = Path(__file__).parent
WEB = ROOT / "web"
PUBLIC = Path(os.getenv("PUBLIC_DIR", ROOT / "public"))

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
RETAIN = int(os.getenv("RETAIN_EPISODES", "30"))
SHOW_TITLE = os.getenv("SHOW_TITLE", "The Morning Brief")
SHOW_DESC = os.getenv(
    "SHOW_DESCRIPTION",
    "A private daily market brief: what moved overnight, why, and what to watch.",
)
SHOW_AUTHOR = os.getenv("SHOW_AUTHOR", "Samuel Gibby")


def site_url() -> str:
    """Public base URL, auto-derived from the repo when running in Actions."""
    explicit = os.getenv("SITE_URL")
    if explicit:
        return explicit.rstrip("/")
    repo = os.getenv("GITHUB_REPOSITORY")  # e.g. "samuelgibby/market-brief"
    if repo and "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}"
    return ""


# --------------------------------------------------------------------------
# Script generation
# --------------------------------------------------------------------------

def write_script(context: str, date_label: str) -> dict:
    from anthropic import Anthropic

    system_prompt = (ROOT / "brief_prompt.md").read_text(encoding="utf-8")
    client = Anthropic()  # reads ANTHROPIC_API_KEY

    user_msg = (
        f"Today is {date_label}.\n\n"
        "Here is everything gathered this morning. Write today's episode.\n\n"
        f"{context}"
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = "".join(
        block.text for block in resp.content if getattr(block, "type", "") == "text"
    ).strip()

    return parse_episode_json(raw)


def parse_episode_json(raw: str) -> dict:
    """Claude is asked for strict JSON; be forgiving anyway."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    candidate = fenced.group(1) if fenced else raw

    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end != -1:
        candidate = candidate[start : end + 1]

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        print("  ! could not parse JSON, treating the whole reply as the script")
        return {
            "title": "Market Brief",
            "teaser": "Today's market rundown.",
            "topics": [],
            "script": raw,
        }

    script = (data.get("script") or "").strip()
    if not script:
        raise RuntimeError("model returned an episode with no script")

    cited = []
    for n in data.get("sources") or []:
        try:
            cited.append(int(n))
        except (TypeError, ValueError):
            continue

    return {
        "title": (data.get("title") or "Market Brief").strip(),
        "teaser": (data.get("teaser") or "").strip(),
        "topics": [str(t) for t in (data.get("topics") or [])][:5],
        "cited": cited,
        "script": script,
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
        print("  ! model cited no usable sources; falling back to top headlines")
        picked = headlines[:8]

    return [
        {"title": h["title"], "source": h["source"], "link": h["link"]}
        for h in picked[:12]
        if h.get("link")
    ]


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
    """Keep the newest RETAIN episodes and delete files nothing points at."""
    episodes.sort(key=lambda e: e["date"], reverse=True)
    keep, drop = episodes[:RETAIN], episodes[RETAIN:]

    for ep in drop:
        for rel in (ep.get("audio"), ep.get("transcript")):
            if rel:
                (PUBLIC / rel).unlink(missing_ok=True)
        print(f"  - pruned {ep['date']}")

    referenced = {ep.get("audio") for ep in keep} | {ep.get("transcript") for ep in keep}
    for folder in ("audio", "transcripts"):
        d = PUBLIC / folder
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.is_file() and f"{folder}/{f.name}" not in referenced:
                f.unlink()
                print(f"  - removed orphan {folder}/{f.name}")

    return keep


def write_feed(episodes: list[dict]) -> None:
    base = site_url()
    if not base:
        print("  ! SITE_URL unknown, skipping RSS feed")
        return

    items = []
    for ep in episodes:
        pub = datetime.fromisoformat(ep["published"])
        audio_url = f"{base}/{ep['audio']}"
        items.append(f"""    <item>
      <title>{escape(ep['title'])}</title>
      <description>{escape(ep.get('teaser', ''))}</description>
      <pubDate>{format_datetime(pub)}</pubDate>
      <guid isPermaLink="false">{escape(ep['date'])}</guid>
      <enclosure url="{escape(audio_url)}" length="{ep.get('bytes', 0)}" type="audio/mpeg"/>
      <itunes:duration>{ep.get('duration', 0)}</itunes:duration>
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

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="use a canned script and silent audio; no API calls")
    parser.add_argument("--date", help="episode date as YYYY-MM-DD (default: today)")
    parser.add_argument("--skip-audio", action="store_true")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    date_str = args.date or now.strftime("%Y-%m-%d")
    date_label = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %-d, %Y")

    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "audio").mkdir(exist_ok=True)
    (PUBLIC / "transcripts").mkdir(exist_ok=True)

    print(f"\n=== {SHOW_TITLE} — {date_label} ===\n")

    if args.dry_run:
        episode = {
            "title": "Dry Run Episode",
            "teaser": "A local smoke test of the full build pipeline.",
            "topics": ["testing"],
            "sources": [{"title": "Example headline", "source": "Test Feed",
                         "link": "https://example.com/story"}],
            "script": ("Good morning. This is a dry run of the build pipeline. "
                       "No news was fetched and no model was called. "
                       "If you are hearing this, the audio path works."),
        }
    else:
        if not os.getenv("ANTHROPIC_API_KEY"):
            print("ERROR: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
            return 1

        print("Fetching headlines...")
        headlines = sources.fetch_headlines()
        print("\nFetching market data...")
        quotes = sources.fetch_market_snapshot()

        if not headlines and not quotes:
            print("\nERROR: every source failed; refusing to build an empty episode.",
                  file=sys.stderr)
            return 1

        context = sources.format_context(headlines, quotes)
        print(f"\nWriting the script with {MODEL}...")
        episode = write_script(context, date_label)

        words = len(episode["script"].split())
        print(f"  + \"{episode['title']}\" ({words} words, ~{words / 165:.1f} min)")
        if words > 560:
            print(f"  ! script ran long ({words} words) — tighten the length "
                  f"rules in brief_prompt.md if this keeps happening")

        episode["sources"] = resolve_sources(episode.pop("cited", []), headlines)
        print(f"  + {len(episode['sources'])} sources cited")

    # --- audio -----------------------------------------------------------
    audio_rel = f"audio/{date_str}.mp3"
    audio_path = PUBLIC / audio_rel
    duration = 0
    size = 0

    if not args.skip_audio:
        print("\nGenerating audio...")
        if args.dry_run and os.getenv("DRY_RUN_SILENT_AUDIO") == "1":
            import subprocess
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                 "-i", "anullsrc=r=24000:cl=mono", "-t", "12",
                 "-b:a", "32k", str(audio_path)], check=True)
        else:
            tts.synthesize(episode["script"], audio_path)
        duration = tts.duration_seconds(audio_path)
        size = audio_path.stat().st_size

    # --- transcript ------------------------------------------------------
    transcript_rel = f"transcripts/{date_str}.txt"
    (PUBLIC / transcript_rel).write_text(episode["script"], encoding="utf-8")

    # --- index -----------------------------------------------------------
    episodes = [e for e in load_episodes() if e.get("date") != date_str]
    episodes.append({
        "date": date_str,
        "title": episode["title"],
        "teaser": episode["teaser"],
        "topics": episode["topics"],
        "sources": episode.get("sources", []),
        "audio": audio_rel,
        "transcript": transcript_rel,
        "duration": duration,
        "bytes": size,
        "published": now.isoformat(),
    })

    print("\nPublishing...")
    episodes = prune(episodes)
    (PUBLIC / "episodes.json").write_text(
        json.dumps(
            {"show": SHOW_TITLE, "description": SHOW_DESC,
             "updated": now.isoformat(), "episodes": episodes},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  + episodes.json ({len(episodes)} episodes)")

    write_feed(episodes)
    copy_web_assets()

    mins, secs = divmod(duration, 60)
    print(f"\nDone. {date_str} — {mins}:{secs:02d}, "
          f"{size / 1_048_576:.2f} MB, published to {PUBLIC}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
