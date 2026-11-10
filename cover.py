"""Cover art for the day's episode.

Two paths, in order of preference:

1. **The day's news photo.** Claude nominates which cited headline best
   represents the episode, and we read that article's Open Graph image. The
   URL is *hot-linked*, never copied into this repo: the image stays on the
   publisher's own CDN, exactly as it does in a Slack or iMessage link
   preview, so we're not redistributing someone else's photograph.

2. **A designed cover.** Generated locally from the day's own data — a colour
   keyed to whether the tape is green or red, the headline set in type, the
   date, the leading sector. Used whenever there's no clean photo, and
   whenever Claude flags the cover story as likely to carry graphic imagery.

The fallback is not a consolation prize. It is the guarantee that the app
never shows something upsetting next to a market brief, and it runs whenever
anything about path 1 is uncertain.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Outlets whose images we are willing to show. All business-news desks, which
# is itself a meaningful filter: their lead art skews to tickers, trading
# floors, executives and buildings rather than conflict photography.
ALLOWED_HOSTS = (
    "cnbc.com", "marketwatch.com", "wsj.com", "dowjones.com",
    "investing.com", "seekingalpha.com", "yahoo.com", "federalreserve.gov",
    "reuters.com", "bloomberg.com", "ft.com", "barrons.com",
)

OG_PATTERNS = (
    r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
    r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
)


def _host_allowed(url: str) -> bool:
    m = re.match(r"https?://([^/]+)", url or "")
    if not m:
        return False
    host = m.group(1).lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def find_news_image(article_url: str) -> str | None:
    """The article's Open Graph image URL, if it has a usable one."""
    if not _host_allowed(article_url):
        print(f"  ! cover: {article_url[:60]} is not an allowed outlet")
        return None
    try:
        resp = requests.get(article_url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"  ! cover: could not read the article ({str(exc)[:70]})")
        return None

    head = resp.text[:200_000]
    for pattern in OG_PATTERNS:
        m = re.search(pattern, head, re.I)
        if not m:
            continue
        img = html.unescape(m.group(1)).strip()
        if img.startswith("//"):
            img = "https:" + img
        if not img.startswith("https://"):
            continue  # an http image would break the page's https
        # Deliberately no allow-list on the image host. Publishers serve art
        # from separate CDNs -- image.cnbcfm.com, mw4.wsj.net, s.yimg.com --
        # so checking it here rejected every real photo and the feature would
        # have quietly never worked. The editorial trust decision is the
        # *article* host, checked above; this is that page's own declared
        # lead image.
        try:
            h = requests.head(img, headers={"User-Agent": UA},
                              timeout=12, allow_redirects=True)
            ctype = (h.headers.get("content-type") or "").lower()
            if h.status_code != 200 or not ctype.startswith("image/"):
                print(f"  ! cover: image not reachable ({h.status_code} {ctype[:24]})")
                continue
        except Exception as exc:  # noqa: BLE001
            print(f"  ! cover: image HEAD failed ({str(exc)[:60]})")
            continue
        return img
    print("  ! cover: no og:image on the page")
    return None


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def generate_cover(path: Path, title: str, date_str: str,
                   snapshot: dict | None = None) -> Path:
    """Draw the fallback cover from the day's own data."""
    from PIL import Image, ImageDraw, ImageFont

    S = 1024
    snapshot = snapshot or {}
    spx = next((q for q in snapshot.get("us_indices", [])
                if q["symbol"] == "^GSPC"), None)
    pct = spx["pct_change"] if spx else 0.0
    sectors = snapshot.get("sectors") or []

    # Colour follows the tape: green up, red down, violet when flat/unknown.
    if spx and pct > 0.05:
        accent, glow = (52, 199, 123), (16, 70, 48)
    elif spx and pct < -0.05:
        accent, glow = (240, 94, 94), (76, 24, 24)
    else:
        accent, glow = (124, 108, 246), (42, 36, 96)

    img = Image.new("RGB", (S, S), (8, 9, 11))
    d = ImageDraw.Draw(img)
    for i in range(S // 2, 0, -1):          # soft radial glow, top-centre
        t = i / (S / 2)
        d.ellipse([S / 2 - i * 1.5, -i, S / 2 + i * 1.5, i],
                  fill=tuple(round(8 + (glow[c] - 8) * (1 - t) * 0.5) for c in range(3)))

    def font(size, bold=True):
        for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                  if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
            try:
                return ImageFont.truetype(p, size)
            except Exception:  # noqa: BLE001, PERF203
                continue
        return ImageFont.load_default()

    # Deliberately NOT the headline: the page already shows the title and date
    # directly beneath the artwork, and an early version repeated both inside
    # the image, which read as a mistake. The cover carries the number instead.
    pad = 84
    d.text((pad, pad), "THE MORNING BRIEF", font=font(28), fill=accent)
    nice_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%a %-d %b").upper()
    d.text((pad, pad + 42), nice_date, font=font(24, False), fill=(138, 143, 152))

    # Hero: the day's move, big.
    hero = f"{pct:+.2f}%" if spx else "—"
    f_hero = font(196)
    w = d.textlength(hero, font=f_hero)
    while w > S - 2 * pad and f_hero.size > 90:
        f_hero = font(f_hero.size - 8)
        w = d.textlength(hero, font=f_hero)
    d.text((pad, S / 2 - 118), hero, font=f_hero, fill=accent)
    d.text((pad + 6, S / 2 + 86), "S&P 500" if spx else "MARKETS",
           font=font(30), fill=(244, 245, 247))

    # A small bar motif along the bottom, one bar per sector, scaled by move.
    if sectors:
        bars = sectors[:8]
        span = S - 2 * pad
        bw = span / (len(bars) * 1.7)
        gap = (span - len(bars) * bw) / max(1, len(bars) - 1)
        base = S - pad - 104
        peak = max(abs(s["pct_change"]) for s in bars) or 1
        for i, s in enumerate(bars):
            h = max(4, abs(s["pct_change"]) / peak * 70)
            x = pad + i * (bw + gap)
            up = s["pct_change"] >= 0
            col = (52, 199, 123) if up else (240, 94, 94)
            y0, y1 = (base - h, base) if up else (base, base + h)
            d.rounded_rectangle([x, y0, x + bw, y1], radius=min(bw / 3, h / 2.2), fill=col)

        lead = bars[0]
        d.text((pad, S - pad - 26),
               f"{lead['label'].upper()}  {lead['pct_change']:+.2f}%",
               font=font(24, False), fill=(138, 143, 152))

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    return path


def build_cover(episode: dict, headlines: list[dict], date_str: str,
                public_dir: Path, snapshot: dict | None = None) -> dict:
    """Decide the episode's cover and return what the player needs."""
    idx = episode.get("cover_story", -1)
    story = headlines[idx] if 0 <= idx < len(headlines) else None

    if episode.get("cover_is_sensitive"):
        print("  · cover: story flagged as sensitive, using a designed cover")
    elif story:
        img = find_news_image(story.get("link", ""))
        if img:
            print(f"  + cover: photo from {story['source']}")
            return {"type": "photo", "url": img,
                    "credit": story["source"], "link": story.get("link", ""),
                    "story": story["title"]}

    rel = f"covers/{date_str}.png"
    generate_cover(public_dir / rel, episode.get("title", "Market Brief"),
                   date_str, snapshot)
    print("  + cover: generated from the day's data")
    return {"type": "generated", "url": rel, "credit": "", "link": "", "story": ""}
