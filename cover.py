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

# Outlets whose images we are willing to show.
#
# This list has to track topics.py. When the show was markets-only, every
# entry was a business desk, and that was itself a useful filter: their lead
# art skews to tickers, trading floors, executives and buildings. Adding five
# more topics without adding their publishers here would have meant every new
# topic silently falling back to a designed cover -- the photo feature
# "working" in code and never once firing in production.
#
# World news publishers appear here because they also cover business and
# science. The protection against conflict photography is not this list, it
# is topics.py's allow_photo_cover=False on the world topic plus the model's
# own sensitivity flag.
ALLOWED_HOSTS = (
    # business / markets
    "cnbc.com", "marketwatch.com", "wsj.com", "dowjones.com",
    "investing.com", "seekingalpha.com", "yahoo.com", "federalreserve.gov",
    "reuters.com", "bloomberg.com", "ft.com", "barrons.com",
    "fortune.com", "businessinsider.com", "forbes.com",
    # technology
    "arstechnica.com", "theverge.com", "techcrunch.com",
    "technologyreview.com", "venturebeat.com", "wired.com",
    # sport
    "espn.com", "cbssports.com", "skysports.com",
    # general news desks
    "bbc.co.uk", "bbc.com", "theguardian.com", "npr.org",
    "aljazeera.com", "france24.com", "dw.com",
    # science and health
    "sciencedaily.com", "nature.com", "statnews.com",
    "scientificamerican.com", "phys.org", "nih.gov",
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


def _font(size: int, bold: bool = True):
    from PIL import ImageFont

    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
              if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001, PERF203
            continue
    return ImageFont.load_default()


def generate_cover(path: Path, topic, date_str: str,
                   snapshot: dict | None = None) -> Path:
    """Draw the designed cover for one topic's segment.

    Markets gets the data-driven treatment it always had -- the day's S&P move
    as a hero number, coloured by direction, over a sector bar motif. Every
    other topic gets the same layout with its own accent colour and its label
    where the number would be, so a brief with no usable photo still looks
    deliberate rather than broken.

    Deliberately NOT the headline: the page shows the title and date directly
    beneath the artwork, and an early version repeated both inside the image,
    which read as a mistake.
    """
    from PIL import Image, ImageDraw

    S = 1024
    snapshot = snapshot or {}
    is_markets = bool(getattr(topic, "needs_market_data", False))
    label = getattr(topic, "label", "The Morning Brief")

    spx = next((q for q in snapshot.get("us_indices", [])
                if q["symbol"] == "^GSPC"), None) if is_markets else None
    sectors = (snapshot.get("sectors") or []) if is_markets else []

    if spx and spx["pct_change"] > 0.05:
        accent, glow = (52, 199, 123), (16, 70, 48)
    elif spx and spx["pct_change"] < -0.05:
        accent, glow = (240, 94, 94), (76, 24, 24)
    else:
        accent = tuple(getattr(topic, "accent", (124, 108, 246)))
        glow = tuple(round(c * 0.34) for c in accent)

    img = Image.new("RGB", (S, S), (8, 9, 11))
    d = ImageDraw.Draw(img)
    for i in range(S // 2, 0, -1):          # soft radial glow, top-centre
        t = i / (S / 2)
        d.ellipse([S / 2 - i * 1.5, -i, S / 2 + i * 1.5, i],
                  fill=tuple(round(8 + (glow[c] - 8) * (1 - t) * 0.5) for c in range(3)))

    pad = 84
    d.text((pad, pad), "THE MORNING BRIEF", font=_font(28), fill=accent)
    nice_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%a %-d %b").upper()
    d.text((pad, pad + 42), nice_date, font=_font(24, False), fill=(138, 143, 152))

    if spx:
        # Markets keeps the hero number it always had, with its label beneath.
        f_hero = _font(196)
        hero = f"{spx['pct_change']:+.2f}%"
        while d.textlength(hero, font=f_hero) > S - 2 * pad and f_hero.size > 54:
            f_hero = _font(f_hero.size - 6)
        d.text((pad, S / 2 - 118), hero, font=f_hero, fill=accent)
        d.text((pad + 6, S / 2 + 86), "S&P 500", font=_font(30),
               fill=(244, 245, 247))
    else:
        # The topic's name is the hero, and it is said exactly once. An
        # earlier draft set it large in the middle AND small along the bottom,
        # which is the same repetition this file was already written to avoid.
        f_hero = _font(132)
        words = label.split()
        while (max(d.textlength(w, font=f_hero) for w in words) > S - 2 * pad
               and f_hero.size > 54):
            f_hero = _font(f_hero.size - 6)
        lines = _wrap(d, label, f_hero, S - 2 * pad)
        line_h = f_hero.size * 1.08
        top = S / 2 - (len(lines) * line_h) / 2 - 24
        for i, line in enumerate(lines):
            d.text((pad, top + i * line_h), line, font=f_hero, fill=accent)

    if sectors:
        # One bar per sector along the bottom, scaled by the size of the move.
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
               font=_font(24, False), fill=(138, 143, 152))
    else:
        # A quiet ascending motif so non-markets covers aren't a bare field.
        base = S - pad - 104
        steps = 8
        span = S - 2 * pad
        bw = span / (steps * 1.7)
        gap = (span - steps * bw) / (steps - 1)
        for i in range(steps):
            h = 10 + (i / (steps - 1)) ** 1.6 * 62
            x = pad + i * (bw + gap)
            shade = tuple(round(c * (0.32 + 0.68 * i / (steps - 1))) for c in accent)
            d.rounded_rectangle([x, base - h, x + bw, base],
                                radius=min(bw / 3, h / 2.2), fill=shade)

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    return path


def build_cover(segment: dict, headlines: list[dict], date_str: str,
                public_dir: Path, topic, snapshot: dict | None = None) -> dict:
    """Decide one segment's cover and return what the player needs."""
    idx = segment.get("cover_story", -1)
    story = headlines[idx] if 0 <= idx < len(headlines) else None
    slug = getattr(topic, "slug", "brief")

    if not getattr(topic, "allow_photo_cover", True):
        print(f"  · cover[{slug}]: photo covers off for this topic, designing one")
    elif segment.get("cover_is_sensitive"):
        print(f"  · cover[{slug}]: story flagged as sensitive, designing one")
    elif story:
        img = find_news_image(story.get("link", ""))
        if img:
            print(f"  + cover[{slug}]: photo from {story['source']}")
            return {"type": "photo", "url": img,
                    "credit": story["source"], "link": story.get("link", ""),
                    "story": story["title"]}

    rel = f"covers/{date_str}-{slug}.png"
    generate_cover(public_dir / rel, topic, date_str, snapshot)
    print(f"  + cover[{slug}]: designed")
    return {"type": "generated", "url": rel, "credit": "", "link": "", "story": ""}
