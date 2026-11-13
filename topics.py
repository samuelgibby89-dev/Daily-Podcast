"""The topic menu.

This file is the whole configuration surface for what the show covers. Adding
a seventh topic means adding one entry below -- no changes to build.py, the
player, or the workflow. Removing one is deleting an entry.

Each topic produces two pieces of audio every weekday:

    short   ~45 seconds, used when the listener picked several topics and
            they get stitched into one continuous brief
    long    ~3 minutes, used when the listener picked this topic ALONE

Both come from a single model call per topic, so six topics cost six calls
rather than twelve.

A note on feeds: every topic deliberately lists more feeds than it needs.
Publishers retire RSS endpoints without warning -- Reuters killed its public
feeds outright, and Yahoo's news index started 404ing mid-2026 -- and the
failure mode that actually hurts is not a dead feed, it's a dead feed nobody
notices. Over-provisioning means one death is absorbed silently; MIN_HEADLINES
below means a topic that has lost too many is reported and skipped for the day
rather than quietly generating a segment out of nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A topic needs at least this many usable headlines to be worth generating.
# Below it, the segment is skipped for the day and the player hides the topic
# rather than offering a brief assembled from two stale links.
MIN_HEADLINES = 5


@dataclass(frozen=True)
class Topic:
    slug: str                  # stable id: filenames, JSON keys, saved prefs
    label: str                 # shown on the picker and the chapter list
    blurb: str                 # one line under the label on the picker
    icon: str                  # glyph id the player draws; see web/index.html
    accent: tuple[int, int, int]   # colour of this topic's designed cover
    feeds: list[tuple[str, str]]
    long_order: str            # editorial running order for the 3-minute cut
    short_order: str           # what the 45-second cut must hit
    needs_market_data: bool = False
    extra_prompt: str = ""     # topic-specific rules appended to the voice
    # Whether this topic may hot-link a publisher's article photo as cover art.
    # False means always use the designed cover. World news is set False on
    # purpose: its lead art is the most likely of any topic to be a casualty,
    # a body or a conflict scene, and no model flag is worth betting a
    # listener's morning on. Flipping this back is one word.
    allow_photo_cover: bool = True
    aliases: list[str] = field(default_factory=list)


MARKETS = Topic(
    slug="markets",
    label="Markets",
    blurb="What moved overnight, why, and what to watch",
    icon="chart",
    needs_market_data=True,
    accent=(124, 108, 246),
    feeds=[
        ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
        ("CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
        # finance.yahoo.com/news/rssindex started 404ing; the per-symbol
        # headline feed still works and carries broad market news.
        ("Yahoo Finance",
         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US"),
        ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
        ("Investing.com", "https://www.investing.com/rss/news_25.rss"),
        ("Seeking Alpha", "https://seekingalpha.com/market_currents.xml"),
    ],
    long_order="""
**1. What happened. (30-45 seconds, ~85-125 words)**
The major events. Fed decisions and Fed speakers, geopolitics, wars and
ceasefires, elections, regulation, big corporate news. Lead with the single
most important one -- no greeting before it, say good morning after the first
beat. Two or three events, with what each actually changes. If it was a quiet
news day, say so and move on rather than inflating something minor.

**2. The index scoreboard. (20-30 seconds, ~55-85 words)**
Where the major US indexes are: S&P 500, Nasdaq, Dow, Russell 2000. Read the
SESSION line at the top of the data first. When the market is open these are
live moves happening right now, so say it that way -- "the S&P is up four
tenths an hour into the session", not "closed up". When it's closed, talk
about the last close. Then foreign markets -- only mention one if it moved
meaningfully, and always name any index flagged as a big move.

**3. Sectors. (30-40 seconds, ~85-110 words)**
The top three or four movers, high and low -- and, more importantly, *why*.
Tie each move to something in the headlines: energy bid on crude, utilities
catching a defensive flow, tech leading on an earnings beat. This is where you
add the most value, because the numbers don't explain themselves. Early in a
session moves are noisy and often reverse; where that's the honest read, say
so. If a move has no obvious explanation in the news, say that plainly
instead of inventing one.

**4. Fixed income. (10-20 seconds, ~30-55 words)**
Treasuries. Where the 2-year, 10-year and 30-year landed, the direction in
basis points, and what the curve is doing. Keep it tight and say what it
implies about growth or Fed expectations.

**5. The rest -- your discretion. (~30 seconds, ~80 words)**
What's on the calendar today: earnings, data releases, Fed speakers. Anything
worth watching. You can also point the listener at one story genuinely worth
reading in full, and say in a sentence why.

**6. An inspiring quote to close. (~10 seconds, ~25 words)**
End on a real quote with a good message -- about patience, perspective,
judgment, resilience, long horizons, doing hard things well. It does not have
to be about markets, and it is better when it isn't. Say the quote, say who
said it, and stop. No commentary on it.
""",
    short_order="""
Lead with the single biggest market story and what it changes. Then the index
scoreboard in one or two sentences -- read the SESSION line first and phrase
live moves as live. Then the one sector move that carries the most meaning,
with why. Close on the ten-year if it moved enough to matter. No quote, no
calendar, no second story.
""",
    extra_prompt="""
- **Only use numbers that appear in the supplied data.** Never estimate, round
  from memory, or infer a figure that was not given to you.
- Write numbers the way they are spoken: "up one point two percent", "the ten
  year at four point three", "eight basis points".
- Sector data comes from sector ETFs. Say "technology" or "the tech sector",
  never "XLK".
- Spell out tickers: "Nvidia", not "NVDA".
""",
)

AI_TECH = Topic(
    slug="ai-tech",
    label="AI & Tech",
    blurb="Models, chips, platforms, and who's shipping",
    icon="chip",
    accent=(99, 179, 237),
    feeds=[
        ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/technology-lab"),
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ("TechCrunch", "https://techcrunch.com/feed/"),
        ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
        ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/"),
        ("Wired", "https://www.wired.com/feed/rss"),
        ("Hacker News", "https://hnrss.org/frontpage"),
    ],
    long_order="""
**1. The lead story. (40-55 seconds, ~110-150 words)**
The single most consequential thing that happened in technology. A model
release, a chip export rule, an acquisition, an outage, a lawsuit. Say what
happened and then spend most of the time on what it actually changes -- for
developers, for competitors, for whoever uses the thing.

**2. Two or three more that matter. (60-75 seconds, ~170-210 words)**
Other real developments, each with a sentence on why it's not just noise.
Funding rounds only if the number or the investor tells you something.
Product launches only if the product is different, not just newer.

**3. The thing being overhyped. (~25 seconds, ~70 words)**
One story getting more attention than it deserves, and the honest reason it's
thinner than the headline suggests. You are allowed a point of view here. If
nothing qualifies today, use the space on a story worth reading in full
instead -- say in a sentence why.

**4. What to watch. (~20 seconds, ~55 words)**
Something landing soon: an earnings call, a shipping date, a ruling, a
conference. Close there, cleanly. No quote.
""",
    short_order="""
The single biggest technology story and what it changes -- that is most of the
time. Then one more development worth knowing, in a sentence or two. Stop. No
overhype segment, no watch list.
""",
    extra_prompt="""
- The listener is technically literate. Do not explain what a GPU, an API, a
  model or a token is.
- Be concrete about capability claims. "Scores higher on a benchmark" is not
  "is smarter". Where a claim comes from a company's own announcement, say so.
- Say company and product names as people say them aloud.
""",
)

SPORTS = Topic(
    slug="sports",
    label="Sports",
    blurb="Last night's results and today's stakes",
    icon="ball",
    accent=(72, 199, 142),
    feeds=[
        ("ESPN", "https://www.espn.com/espn/rss/news"),
        ("ESPN NFL", "https://www.espn.com/espn/rss/nfl/news"),
        ("ESPN NBA", "https://www.espn.com/espn/rss/nba/news"),
        ("BBC Sport", "https://feeds.bbci.co.uk/sport/rss.xml"),
        ("CBS Sports", "https://www.cbssports.com/rss/headlines/"),
        ("Yahoo Sports", "https://sports.yahoo.com/rss/"),
        ("Sky Sports", "https://www.skysports.com/rss/12040"),
    ],
    long_order="""
**1. Last night's headline result. (35-50 seconds, ~100-140 words)**
The game or performance everyone will be talking about. What happened, the
score, and the bit that actually decided it. Write it for someone who did not
watch and does not want it spoiled slowly -- give the result first, then the
texture.

**2. The rest of the night. (50-65 seconds, ~140-185 words)**
Other results and performances that matter, grouped so it flows -- not a
scores readout. Standings implications where they're real. Injuries, trades,
suspensions and contract news belong here too.

**3. The story behind the story. (~25 seconds, ~70 words)**
One thing developing over a longer arc: a streak, a slump, a race for a seed,
a manager under pressure, a record in reach.

**4. Tonight and today. (~20 seconds, ~55 words)**
What's on, and which one is worth your evening. Close there. No quote.
""",
    short_order="""
Last night's biggest result, with the detail that decided it. Then the one or
two other things that genuinely matter from the night. One sentence on what's
on tonight. Stop.
""",
    extra_prompt="""
- Give results plainly and early. Never tease a score.
- Say scores the way they are spoken: "a hundred and twelve to a hundred and
  eight", not "112-108".
- Cover whatever the day actually produced -- do not force a league in because
  it is popular, and do not assume which team the listener follows.
- No betting lines, odds or wagering advice.
""",
)

BUSINESS = Topic(
    slug="business",
    label="Business",
    blurb="Earnings, deals, and how companies are really doing",
    icon="briefcase",
    accent=(246, 173, 85),
    feeds=[
        ("CNBC Business", "https://www.cnbc.com/id/10001147/device/rss/rss.html"),
        ("WSJ Business", "https://feeds.content.dowjones.io/public/rss/WSJcomUSBusiness"),
        ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
        ("Fortune", "https://fortune.com/feed/"),
        ("Business Insider", "https://markets.businessinsider.com/rss/news"),
        ("Forbes Business", "https://www.forbes.com/business/feed/"),
    ],
    long_order="""
**1. The lead story. (40-55 seconds, ~110-150 words)**
The most consequential corporate or economic story. Earnings that revealed
something, a deal, a bankruptcy, a regulator moving, a labour fight, a big
strategic reversal. What happened, and what it tells you about the business
underneath.

**2. Two or three more. (60-75 seconds, ~170-210 words)**
Other developments worth knowing, each tied to why it matters. Prefer stories
where something was actually revealed over stories where something was merely
announced.

**3. A pattern worth naming. (~25 seconds, ~70 words)**
Something showing up across several stories -- a sector under pressure, a
hiring shift, consumers trading down, capital getting more expensive. Say what
would make you think you're wrong about it.

**4. On the calendar. (~20 seconds, ~55 words)**
Earnings, data or decisions landing soon. Close there. No quote.
""",
    short_order="""
The biggest business story and what it reveals. Then one more that matters, in
a sentence or two. Stop. No pattern segment, no calendar.
""",
    extra_prompt="""
- The listener is financially literate. Do not explain margins, guidance or
  what a quarter is.
- Distinguish what a company announced from what the numbers show.
- Only use figures that appear in the supplied headlines. Never reconstruct a
  revenue or margin number from memory.
""",
)

WORLD = Topic(
    slug="world",
    label="World",
    blurb="Geopolitics and what's happening globally",
    icon="globe",
    accent=(237, 137, 160),
    feeds=[
        ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
        ("NPR World", "https://feeds.npr.org/1004/rss.xml"),
        ("Guardian World", "https://www.theguardian.com/world/rss"),
        ("France 24", "https://www.france24.com/en/rss"),
        ("Deutsche Welle", "https://rss.dw.com/rdf/rss-en-world"),
    ],
    allow_photo_cover=False,
    long_order="""
**1. The lead story. (40-55 seconds, ~110-150 words)**
The most consequential thing happening in the world. A conflict, an election,
a negotiation, a leader falling, a border, a treaty, a disaster. What happened
and what it changes on the ground.

**2. Two or three more. (60-75 seconds, ~170-210 words)**
Other developments that matter, each with the stake made clear. Favour
substance over diplomatic choreography -- a meeting that produced nothing is
usually not a story.

**3. The slower-moving thing. (~25 seconds, ~70 words)**
One situation building over weeks or months that the daily cycle keeps
missing, and what would mark a real turn in it.

**4. What to watch. (~20 seconds, ~55 words)**
A vote, a deadline, a summit, a ruling coming up. Close there. No quote.
""",
    short_order="""
The single most consequential world story and what it changes. Then one more
that matters, briefly. Stop.
""",
    extra_prompt="""
- Be accurate and even-handed on contested situations. Attribute contested
  claims to whoever is making them rather than stating them flatly.
- Do not editorialise on which side of a conflict is right. Explain the stakes
  and what each party is actually seeking.
- Handle violence and casualties factually and without dwelling on detail.
  Give numbers where they are reported, and attribute them.
- Name places and people in full on first mention.
""",
)

SCIENCE_HEALTH = Topic(
    slug="science-health",
    label="Science & Health",
    blurb="Research that holds up, and what it means for you",
    icon="flask",
    accent=(129, 199, 212),
    feeds=[
        ("Science Daily", "https://www.sciencedaily.com/rss/top/science.xml"),
        ("Nature", "https://www.nature.com/nature.rss"),
        ("NPR Health", "https://feeds.npr.org/1128/rss.xml"),
        ("STAT News", "https://www.statnews.com/feed/"),
        ("Scientific American",
         "https://www.scientificamerican.com/platform/syndication/rss/"),
        ("BBC Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
        ("Phys.org", "https://phys.org/rss-feed/"),
    ],
    long_order="""
**1. The lead finding. (40-55 seconds, ~110-150 words)**
The most significant piece of research or health news. What was found, how
strong the evidence actually is, and what would have to be true for it to
matter. Say the study design and size when the headline leans on the result.

**2. Two or three more. (60-75 seconds, ~170-210 words)**
Other findings or developments worth knowing -- approvals, outbreaks, trial
results, a genuinely surprising result. Each with a sentence on how much
weight it can bear.

**3. The one being oversold. (~25 seconds, ~70 words)**
A finding the coverage has run ahead of -- a mouse study written up as a cure,
an association reported as a cause, a tiny sample. Name the gap plainly. If
nothing qualifies, use the space on a study genuinely worth reading.

**4. What to watch. (~20 seconds, ~55 words)**
A decision, a readout or a season turning. Close there. No quote.
""",
    short_order="""
The most significant finding, with an honest line on how strong the evidence
is. Then one more worth knowing, briefly. Stop.
""",
    extra_prompt="""
- Be rigorous about evidence. Say "associated with" when that is what was
  found. Flag preprints, animal studies and small samples as what they are.
- Never give medical advice, dosing, or anything a listener could act on
  clinically. Report findings; do not recommend.
- Where a finding concerns a treatment people are already taking, say plainly
  that it is a research result and not a reason to change anything.
- No alarmism. Give absolute risk alongside relative risk when both are
  reported.
""",
)


TOPICS: list[Topic] = [MARKETS, AI_TECH, SPORTS, BUSINESS, WORLD, SCIENCE_HEALTH]

BY_SLUG: dict[str, Topic] = {t.slug: t for t in TOPICS}

# Most topics a listener may stack into one brief. Four 45-second segments is
# three minutes, which is the cap the show is built around.
MAX_SELECTION = 4


def menu() -> list[dict]:
    """The picker's data, published into episodes.json for the client."""
    return [
        {"slug": t.slug, "label": t.label, "blurb": t.blurb, "icon": t.icon}
        for t in TOPICS
    ]


def all_feeds() -> list[tuple[str, str, str]]:
    """Every (topic_slug, feed_name, url) across the whole menu."""
    return [(t.slug, name, url) for t in TOPICS for name, url in t.feeds]
