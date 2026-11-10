# The Morning Brief

A private daily market podcast that builds itself. Every weekday morning,
GitHub pulls the overnight headlines and market data, has Claude write a
seven-minute script, turns it into audio, and publishes it to a web app you
open on your phone.

**Cost: $0 for hosting.** The only spend is Claude API tokens — roughly
2–4 cents a day.

---

## How it works

```
GitHub Actions (weekdays, 6:07am MT)
  │
  ├─ sources.py   RSS headlines + Stooq market data      (free, no keys)
  ├─ build.py     Claude writes the script               (your API key)
  ├─ tts.py       edge-tts turns it into an MP3          (free, no key)
  └─ publishes ./public → the gh-pages branch
                            │
                            └─ GitHub Pages serves it at
                               https://<you>.github.io/market-brief/
```

Nothing runs on your computer. There is no server, no database, no Flask
process to keep alive. The published site is plain static files.

The `gh-pages` branch is force-pushed as a single fresh commit each morning,
so the repo never accumulates years of MP3 history. The site keeps the last
30 episodes; older ones are deleted.

---

## Deploy it (about 10 minutes, one time)

### 1. Create the repo

On GitHub, click **New repository**. Name it `market-brief`. Make it
**Public** — GitHub Pages is only free on public repos. Do *not* add a README
or .gitignore; the folder already has them.

Then, in the project folder on your machine:

```bash
git init -b main
git add .
git commit -m "Daily market brief"
git remote add origin https://github.com/YOUR-USERNAME/market-brief.git
git push -u origin main
```

### 2. Add your Claude API key

In the repo: **Settings → Secrets and variables → Actions → New repository
secret**

- Name: `ANTHROPIC_API_KEY`
- Secret: your key from https://console.anthropic.com

This is the one piece only you can do. The key is encrypted and never appears
in logs.

### 3. Run it once by hand

Go to the **Actions** tab → **Daily Brief** → **Run workflow**.

The first run takes 2–3 minutes. Watch the log. When it finishes, the
`gh-pages` branch will exist.

### 4. Turn on GitHub Pages

**Settings → Pages**

- Source: **Deploy from a branch**
- Branch: **gh-pages** / **(root)** → Save

Wait about a minute, then open:

```
https://YOUR-USERNAME.github.io/market-brief/
```

Your first episode is there.

### 5. Put it on your home screen

On your iPhone, open that URL in **Safari** (it has to be Safari, not Chrome),
tap the share icon, then **Add to Home Screen**.

It installs as a real app: its own icon, no browser chrome, and lock-screen
and AirPods controls.

(Episodes stream rather than being stored offline. A service worker can't sit
in front of streamed audio without hanging the player — see the comment at the
top of `web/sw.js` if you're curious.)

---

## Using it

- **Play / pause, −15s, +30s, speed up to 2×** — all standard.
- **Transcript** — tap to read along or skim instead of listening.
- **Archive** — the last 30 episodes.
- **Lock screen + AirPods** — play, pause, and skip work from the lock screen
  and from a squeeze of your AirPods, like any podcast.
- **Subscribe in a real podcast app** — the tiny link at the bottom of the
  page is an RSS feed. Paste `https://YOUR-USERNAME.github.io/market-brief/feed.xml`
  into Overcast or Pocket Casts if you'd rather listen there.

---

## Tuning it

### The writing

`brief_prompt.md` is the whole personality of the show — voice, structure,
length, what to skip. It is plain English, no code. Edit it, push, and the
next morning's episode changes. This is the file worth iterating on.

Length lives there too, in its own section. It's currently set to 400–480
words, which lands near three minutes. If episodes start creeping long, make
that section blunter rather than adding new rules elsewhere — the build logs
the word count on every run, so you can see the drift.

### Cover art

Each episode gets a cover. Claude nominates which cited headline best
represents the day and whether its imagery is likely to be graphic; if the
story is clean, the build reads that article's Open Graph image and
**hot-links** it — the photo stays on the publisher's CDN exactly as it does
in a Slack or iMessage preview, so nothing copyrighted is copied into this
repo.

Everything else falls back to a designed cover drawn locally in `cover.py`:
the day's S&P move as the hero number, coloured green or red, with a sector
bar motif. It deliberately does **not** repeat the headline, because the page
already shows the title and date right under the artwork.

The fallback runs whenever anything is uncertain — story flagged as sensitive,
no `og:image`, article unreachable, image not actually an image, outlet not on
the allow-list in `ALLOWED_HOSTS`. The allow-list applies to the *article*
host, not the image host: publishers serve art from separate CDNs
(`image.cnbcfm.com`, `mw4.wsj.net`, `s.yimg.com`) and checking those rejected
every real photo.

### Morning notifications

A push arrives each weekday when the brief is published; tapping it opens
straight to that episode via `?episode=YYYY-MM-DD`.

**iOS only delivers push to a web app installed on the home screen.** Open the
site in Safari, Add to Home Screen, then open it from the icon — not from
Safari — before enabling.

One-time setup:

1. Generate a key pair locally (needs `pip install py-vapid`):

   ```
   python genkeys.py
   ```

2. In the repo: add **`VAPID_PUBLIC_KEY`** as a *variable*
   (Settings → Secrets and variables → Actions → Variables) and
   **`VAPID_PRIVATE_KEY`** as a *secret*. Optionally set `VAPID_SUBJECT` to
   `mailto:you@example.com`.
3. Run the workflow once so it publishes `push.json` with the public key.
4. Open the app from your home screen, tap the **bell**, allow notifications.
   It shows a blob of JSON — copy it and save it as the secret
   **`PUSH_SUBSCRIPTION`**.

That's it. `notify.py` runs after each publish and skips silently whenever the
subscription isn't set, so the build never fails over notifications.

If alerts stop, the log will say `subscription expired` — browsers discard
push subscriptions periodically. Tap the bell again and replace the secret.

### The player

If you change anything in `web/`, bump `VERSION` at the top of `web/sw.js`
(`brief-v3` → `brief-v4`). The service worker serves the cached shell first,
so without a bump your phone keeps showing the old UI.

### Everything else

**Settings → Secrets and variables → Actions → Variables** (not Secrets):

| Variable | Default | What it does |
|---|---|---|
| `TTS_VOICE` | `en-US-AndrewMultilingualNeural` | The host's voice |
| `CLAUDE_MODEL` | `claude-sonnet-5` | Swap to `claude-opus-5` for sharper writing at higher cost |
| `SHOW_TITLE` | `The Morning Brief` | Shown in the app and the feed |
| `RETAIN_EPISODES` | `30` | How many episodes to keep |

Other voices worth trying: `en-US-BrianMultilingualNeural` (crisper
newsreader), `en-US-AvaMultilingualNeural`, `en-GB-RyanNeural`.

### The time

**Read this before touching the schedule.**

The brief is built just after the US opening bell, so the scoreboard and
sector segments describe live movement rather than yesterday's close.

GitHub's `schedule` events are queued at low priority on free runners, and on
this repo they were firing **4.5 to 9.5 hours late, every single weekday**. A
12:07 UTC cron produced episodes between 16:50 and 21:36 UTC across twelve
consecutive builds. That is why the brief always looked a day stale in the
morning: today's didn't exist yet, so the newest one was the previous
weekday's. It was never a date bug.

Manually dispatched runs are **not** subject to that queue — they start within
seconds. So real timing comes from an external scheduler calling the dispatch
API, and the cron in the workflow is only a backstop.

**Setting up the trigger (one time, ~5 minutes):**

1. Create a fine-grained personal access token at
   github.com/settings/personal-access-tokens — repository access limited to
   `Daily-Podcast`, and under Permissions set **Actions: Read and write**.
   Give it a long expiry; the podcast stops when the token does.
2. Sign up at cron-job.org (free) and create a job:
   - URL: `https://api.github.com/repos/<you>/Daily-Podcast/actions/workflows/daily-brief.yml/dispatches`
   - Method: **POST**
   - Schedule: weekdays, 9:45am **America/New_York** (it handles DST; GitHub cron can't)
   - Headers: `Authorization: Bearer <your token>`,
     `Accept: application/vnd.github+json`
   - Body: `{"ref":"main"}`
3. Save, then hit "Run now" once. A run should appear in the Actions tab
   within seconds.

Any scheduler works — Cloudflare Workers cron, an always-on machine with
`curl` — the only requirement is that it can POST with a header.

**The backstop and double-builds.** The workflow still has
`cron: "47 14 * * 1-5"`, which lands after the open year-round (09:47 ET in
winter, 10:47 ET in summer). Whenever it eventually fires, `build.py` runs
with `--skip-if-published` and exits immediately if today's episode already
exists. Without that, a backstop run arriving at 8pm would replace your
morning brief with an evening one. To deliberately rebuild, run the workflow
by hand with **force** ticked.

### The sources

`sources.py` has `FEEDS` at the top — RSS URLs, each independent, a dead one
skipped with a warning. Below that are the instrument lists: `US_INDICES`,
`FOREIGN_INDICES`, `SECTORS` (the eleven SPDR sector ETFs), `OTHER` and
`TREASURY_TENORS`.

Prices come from Yahoo's chart API, with Stooq as a fallback, and Treasury
yields from the US Treasury's own daily curve CSV. No API keys.

**Why two providers.** The original build used Stooq alone. Stooq answers
HTTP 200 with a body like `Exceeded the daily hits limit`, so every quote
parsed to `None`, all three market segments were silently skipped, and the
episodes still sounded completely fine — for weeks. The 2026-11-06 brief
eventually said "data feed's down, I can't give you the scoreboard today" out
loud. Every failure is now logged with its provider, HTTP status and the first
80 characters of the response body, the run prints `N of 29 instruments
returned data`, and a build with zero market data **fails** rather than
shipping a half-empty episode that sounds plausible.

If you ever see that count drop, read the `!` lines in the build log — they
name the instrument and what each provider actually said.

## Running it locally

```bash
pip install -r requirements.txt

# smoke test — no API calls, no network, just proves the pipeline works
python build.py --dry-run

# the real thing
export ANTHROPIC_API_KEY=sk-ant-...
python build.py

# then look at it
python -m http.server 8000 -d public
```

Note that `python -m http.server` doesn't support range requests, so seeking
within an episode won't work locally. It works fine on GitHub Pages.

---

## When something breaks

**The workflow failed.** GitHub emails you. Open the Actions tab and read the
red step — the logs name the failing source or engine directly.

**No episode this morning, no failure email.** GitHub disables scheduled
workflows in repos with no activity for 60 days. It emails you first. Push any
commit, or hit *Run workflow* manually, to re-enable.

**The audio sounds robotic.** edge-tts got a 403 and fell back to gTTS. It
usually resolves itself by the next morning — the build retries three times
before falling back. If it keeps happening, add an `OPENAI_API_KEY` secret and
set the `TTS_ENGINE` variable to `openai`; that runs about $2/month and the
voices are excellent.

**The app won't update on my phone.** Pull down to refresh. The service worker
caches the shell aggressively but always checks the network for new episodes.

**An episode won't play / the spinner hangs.** Almost always a stale service
worker. In Safari: Settings → Apps → Safari → Advanced → Website Data, remove
the github.io entry, then reload.

**Some numbers sound wrong.** The prompt forbids inventing figures, and every
number handed to Claude comes from Stooq's daily close — which means the
"latest" close may be yesterday's if a market hasn't settled yet. Check
`sources.py` if a specific symbol looks off.

---

## Files

```
build.py                        orchestrates one episode, start to finish
sources.py                      RSS + market data (edit FEEDS / SYMBOLS here)
tts.py                          text → MP3, with fallbacks
brief_prompt.md                 the show's voice and structure ← edit this one
web/                            the phone app (HTML, service worker, icons)
.github/workflows/daily-brief.yml   the schedule and the deploy
public/                         build output; published to gh-pages, gitignored
```
