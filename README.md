# Viral Meme Potential News Scraper

Monitors odd / bizarre / high-meme-potential news from RSS feeds, scores stories, and sends alerts the moment something fresh and absurd appears.

## Features

- **Virality is measured, not guessed.** Reddit's `top/?t=day` across
  `r/nottheonion+WeirdNews+FloridaMan` is sorted by community score, so feed position is
  a real signal — and it needs no API key. One multireddit request covers all the subs.
- **Freshness gate + decay.** Stories older than `MAX_AGE_HOURS` are dropped, and score
  decays with age on an 18h half-life.
- **Cross-source corroboration.** A story appearing in two unrelated feeds within 48h is
  spreading; it gets a large bonus. This also catches syndicated reruns that URL-based
  dedup missed.
- Per-source score thresholds and age caps (a curated odd-news wire and a ranked social
  feed are not on the same scale, and don't have the same publication lag).
- Discord webhook alerts (pretty embeds) + optional email
- `dryrun.py` scores a live scan without sending alerts or writing state

## Scoring model

| source | how it's scored |
|---|---|
| Reddit multireddit | `(4.0 + up to 3.0 for position) × 0.5^(age/18h)` — being in top-of-day at all is most of the signal |
| UPI Odd News | keyword regexes (see `HIGH_VALUE` / `MEDIUM_VALUE` / `LOW_VALUE`) |
| any | `+3.0` per distinct other source that ran the same story within 48h |

Age is always a multiplier, never a divisor: a Reddit post cannot be young, because
reaching top-of-day takes hours of voting.

See [NOTES.md](NOTES.md) for the measurements behind these choices, including two
approaches that were tried and rejected.

## Quick Start

```bash
# 1. Install
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env → paste your Discord webhook URL (easiest method)

# 3. Run
python meme_scraper.py
```

The first run will scan and alert on anything currently scoring ≥ `MIN_SCORE`. After that it only notifies on new stories.

## Discord Setup (recommended)

1. Open your Discord server
2. Channel Settings → Integrations → Webhooks → New Webhook
3. Copy the URL into `.env` as `DISCORD_WEBHOOK_URL`

## Tuning

Always tune with `python dryrun.py` first — it scores a live scan and prints the full
ranking with each item's threshold, rank, and age, without firing the webhook or
touching `data/seen.json`.

> **After changing how scoring works, delete `data/seen.json` _and restart the
> scraper_.** Items are marked seen before the threshold is checked, so anything the
> *old* scorer discarded stays discarded — a better scorer otherwise only applies to
> stories that break from that point on.
>
> The restart is not optional: `main()` loads the ledger into memory once at startup and
> never re-reads it, so deleting the file under a running process does nothing — the next
> scan just writes the in-memory ids straight back.

**Freshness** (`.env`): `MAX_AGE_HOURS` is the main dial. Measured on a live 44-item
scan, a 4h cap passed 1 item and 2h passed zero — nothing reaches these feeds that fast.
12h is the working default. Per-source overrides live in `RSS_SOURCES` (UPI runs at 30h
because it publishes on a ~15h lag).

**Corroboration** (`.env`): lower `CORROBORATION_MIN_OVERLAP` to pair more aggressively.
Validated true matches sat at 0.286–0.50 and the nearest false pair at 0.182, so there
is not much room — if unrelated stories start pairing, raise it.

**Keyword lists** (`meme_scraper.py`): `HIGH_VALUE` (+3), `MEDIUM_VALUE` (+1.5),
`LOW_VALUE` (+0.5). These only affect the UPI path — Reddit items are scored by rank.
Be aware these patterns were transcribed from headlines that had already run, so they
catch reruns rather than new stories; 22 of 25 live odd-news headlines score 0.0.

## Running in the Background

**Simple (Linux/macOS):**
```bash
nohup python meme_scraper.py > scraper.out 2>&1 &
```

**Cron (every 30 min, once-only mode):**
```bash
# Comment out the schedule loop and just call run_once if preferred
*/30 * * * * cd /path/to/meme_news_scraper && /path/to/venv/bin/python meme_scraper.py
```

**systemd** or Docker also work fine.

## Data

- `data/seen.json` – ids of already-processed articles (insertion-ordered; the newest
  2000 are kept)
- `data/recent.json` – title tokens from the last 48h, used for cross-source
  corroboration. Corroboration needs roughly a day of warm-up before it can fire.
- `data/scraper.log` – full log. Each scan writes a `Source yield:` line; a bare `0`
  means the feed is dead, while `0(304)`, `0(429)` and `0(cooldown)` are all healthy
  states.

Delete `seen.json` if you want the scraper to re-alert on recent stories.

## Extending

- Add more RSS feeds to the `RSS_SOURCES` list
- Add HTML scraping targets in `HTML_SOURCES` (and implement a simple scraper function)
- Hook in an LLM (OpenAI / Grok / Claude) for smarter “is this memeable?” judgment if you want higher quality filtering

## Legal / Polite Use

- Respects basic rate limiting
- Uses a clear User-Agent
- Prefer RSS over aggressive HTML scraping
- For personal monitoring only

Enjoy the alerts. When the next “theater-clearing fart” or “capybara lobbyist” drops, you’ll know first.

## Tests

```bash
pip install -r requirements-test.txt
pytest tests
```

18 tests cover the LLM judge in `meme_judge.py`: the hard suppression list (real harm such as deaths, disasters and crashes is always blocked, while idioms like "declares war on aggressive turkey" pass through), score parsing and clamping, refusal on malformed model output, and the judgment cache. The model server is mocked and the network is blocked. 4/4 deliberate mutations are caught.
