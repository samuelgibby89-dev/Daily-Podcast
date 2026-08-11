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

The schedule lives at the top of `.github/workflows/daily-brief.yml`:

```yaml
- cron: "7 12 * * 1-5"
```

That's 12:07 UTC — 6:07am Denver in summer. **Cron has no daylight saving**,
so it becomes 5:07am when the clocks change in November. Switch it to
`"7 13 * * 1-5"` then if the hour bothers you. GitHub also runs scheduled jobs
on a best-effort basis; a 5–20 minute delay at peak times is normal.

### The sources

`sources.py` has a `FEEDS` list at the top. Add or remove RSS URLs freely —
each feed is independent and a dead one is skipped with a warning. `SYMBOLS`
just below controls which indexes, yields, and commodities get quoted.

---

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
