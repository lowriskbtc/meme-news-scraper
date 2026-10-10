# Engineering notes — 2026-08-13

## Libraries evaluated and rejected

### PixelRAG (`StarTrail-org/PixelRAG`, Apache-2.0)

Renders documents to screenshot tiles, embeds them with a fine-tuned Qwen3-VL-2B
vision model, and serves top-k image tiles from a FAISS/Qdrant index.

**Rejected: there is no retrieval problem here.** RAG retrieves from a corpus given a
query. This scraper has a *stream* and a *classifier* — no corpus, no queries. Standing
up Playwright + a vision embedding model + a vector index in order to judge a 400-char
RSS summary isn't overkill, it's the wrong shape of tool.

The one place vision would genuinely help is scoring an article's *thumbnail* for
absurdity — but that is a single plain VLM call, not PixelRAG.

### Agent-Reach (`Panniantong/agent-reach`, MIT)

CLI giving agents read/search access to ~13 platforms without paid API keys.

**Rejected: its zero-config source set is the one we already have.** Per the project's
own support table in its README (not our testing — recheck it, tables change):

| Source | Zero config? |
|---|---|
| Web, RSS, YouTube, V2EX | yes |
| GitHub | yes (public repos) |
| **Reddit** | **no — login credentials** |
| **Twitter/X** | **no — cookie required** |
| Facebook, Instagram, Xiaohongshu | no — browser session |

RSS is exactly what `meme_scraper.py` already does with feedparser. Everything that
would actually be *new* (Reddit, X) needs credentials.

That is disqualifying for this specific program, which is an **unattended daemon**.
Cookie state expires silently, failures are invisible in a background process, and the
README warns of account-suspension risk when scripting these platforms. It would work
for a week and then rot.

Also note: the documented install path is "hand your agent a raw.githubusercontent URL
and let it execute the doc." If installing, read that doc first and run it yourself, and
pin a tag — there are at least two near-identical repos (`Panniantong/agent-reach` and
`EdisonChenAI/agent-reach`) and star counts reported between 31k and 71k.

**If the goal is reliable Reddit, the real answer is Reddit's own OAuth** — a free
"script" app gives 100 queries/minute, which is far more than this needs.

## Fixed 2026-08-13

1. **UPI feed URL was wrong.** `https://www.upi.com/rss/Odd_News/` returns HTTP 200
   serving UPI's *general top-news* feed (Senate hearings, earthquakes, DOJ suits), so
   the highest-weight "odd news" source was feeding straight politics into a meme
   scorer. The real feed is `https://rss.upi.com/news/odd_news.rss`.
2. **User-Agent got a hard 403 from UPI.** The custom `MemePotentialScraper/1.0` string
   was blocked; the same URL returns 200 with a normal browser UA.
3. **HTTP failures were invisible.** `feedparser.parse(url)` returns an empty entry list
   for a 403/429 exactly as it does for a genuinely empty feed. Three of five sources
   were dead for 30+ hours without a single log line. Now fetched via `requests` so the
   status code is visible, with a per-scan `Source yield:` line.
4. **Reddit 429 backoff + conditional GET.** 15-minute per-source cooldown after a 429,
   and ETag/Last-Modified so unchanged feeds cost a 304.
5. **`seen` truncation dropped arbitrary entries.** `save_seen` sliced `list(seen)[-2000:]`
   on a *set*, which has no order — so at the 2000 cap it would evict random ids and
   re-alert old stories. Now an insertion-ordered dict.

## 2026-08-14 — freshness + virality rework

The ask: alerts must be **fresh** and **high viral potential**. Neither was measured.

### Where virality now comes from

Reddit's `/top/.rss?t=day` is sorted by community score, so **feed position is a free
virality signal with no OAuth** — the `.json` endpoints that carry raw score/comment
counts are 403 without it. `?t=hour` returns zero entries, so hourly granularity isn't
available.

All three Reddit sources collapsed into **one multireddit request**
(`r/nottheonion+WeirdNews+FloridaMan`). This was forced by the log: across 553 scans the
*first* Reddit request returned 200 on 551, while the 2nd and 3rd were 429 and then sat
in cooldown permanently — r/news got 1 usable scan out of 553. Reddit rate-limits a
burst from one IP, so N subreddits must cost one request, not N.

### Scoring, and a wrong turn worth recording

First attempt scored *climb rate* — position divided by age — on the theory that rank 10
at 3.7h beats rank 1 at 15h. It ranked a survey trend-piece ("Gen Z Redirects Retirement
Savings Into Sports Betting") at 7.4 while the single highest-ranked odd-news post of the
day across all three subs ("boat operator used a foghorn to wake a sleeping polar bear")
scored 2.65 and **failed** a 3.5 threshold. When the top-ranked item doesn't fire, the
curve is wrong.

The error: a Reddit post *structurally cannot be young*. Nothing observed was under 3.7h,
because reaching top-of-day requires hours of voting. Dividing by age penalises a post
for the exact mechanism that generates the signal.

Current model — membership is most of the signal, position is a nudge, age is a
multiplier and never a divisor:

    score = (FEED_BASE_POINTS + POSITION_BONUS_MAX * (1 - (rank-1)/total))
            * 0.5 ** (age_hours / AGE_HALF_LIFE_HOURS)

Against the same live sample it fires 7 of 10: rank 1 at 15.1h now clears (3.91), a 21h
post correctly does not (2.70), and a rank-19 marketing item does not (2.88).

### Freshness

`MAX_AGE_HOURS`, `FRESHNESS_BOOST_HOURS` and `FRESHNESS_BOOST_POINTS` were **already in
`.env` and documented in `env.example`, but no code ever read them** — the freshness
feature was configured and never built. Now wired up.

Sweep over one live scan (44 dated items):

| cap | survivors |
|---|---|
| 2h | **0** |
| 4h | **1** (the configured value) |
| 6h | 3 |
| 12h | 9 — all Reddit |
| 18h | 21 |
| 24h | 23 |

`MAX_AGE_HOURS=4.0` is why nothing good was coming through. **12h recommended** — that is
a `.env` change and yours to make; `env.example` has been updated. `FRESHNESS_BOOST_HOURS=2.0`
is inert at any setting below ~4h and is flagged as such.

Per-source thresholds now exist (`min_score` in `RSS_SOURCES`), because the Reddit rank
scale and the UPI keyword scale are not the same scale and one global gate cannot serve both.

### Cross-source corroboration (built)

A story surfacing independently in two unrelated feeds is spreading — direct evidence of
reach, and the only signal that rescues stories the keyword scorer cannot see. Titles are
tokenised (stopwords dropped, crude suffix stemming so *board/boarding* and *goat/goats*
match) and compared by Jaccard overlap; a match against a **different** source adds
`CORROBORATION_BONUS` (default 3.0) at threshold `CORROBORATION_MIN_OVERLAP` (0.24).

Validated on seven real pairs from live feeds — three true matches, four decoys:

| pair | overlap | |
|---|---|---|
| goats: UPI "Two loose goats visit Oregon transit station" / Reddit "Loose pygmy goats caught boarding TriMet bus" | 0.286 | match |
| snake in helmet (UPI / Reddit) | 0.500 | match |
| Ecuador cocaine with Messi's name (UPI / Reddit) | 0.286 | match |
| two unrelated Florida-man arrests | 0.182 | correctly not matched |
| two unrelated "man arrested" stories | 0.062 | correctly not matched |
| two unrelated animal stories | 0.000 | correctly not matched |
| Texas swearing / Count Binface | 0.000 | correctly not matched |

Lowest true positive 0.286, highest false positive 0.182 — the 0.24 threshold sits
between them, but that is a narrow margin on a seven-pair sample. Tune via env if it
starts pairing unrelated stories.

**Same-scan matching cannot work here, and that drove the design.** Reddit carries a
story while it breaks (3-11h old); UPI publishes its copy on a ~15h lag, by which point
the Reddit copy has aged out of top-of-day. The windows never overlap. So titles are
persisted to `data/recent.json` for `RECENT_WINDOW_HOURS` (48) and each scan is matched
against that history. **This means corroboration needs ~a day of warm-up** before it
starts firing — the history was empty at first run.

This also fixes dedupe: the old id was a hash of URL path + title, which is why the same
Tulsa wrestling story alerted twice from two outlets.

Rejected: per-subreddit rank normalisation — with ~19 merged entries the small subs
contribute 2-3 each, so "rank 1 of 2" would inflate every one of them to the top.

### Gotcha: a scoring change never surfaces the existing backlog

`run_once` marks an item seen **before** testing it against the threshold, so a story the
old scorer rated 0.0 is still recorded as seen and can never alert later. After the
rework, the first Reddit-inclusive scan found 5 items above threshold and alerted on
**zero** — all five had already been consumed (and silently discarded) by the old model.

So: **after any scoring change, delete `data/seen.json` and restart the process.**
Deleting the file alone does nothing while the scraper is running — `main()` reads the
ledger into memory once at startup and hands that dict to the scheduler, so the next scan
writes all the old ids straight back. Verified the hard way: the file was removed at
02:31 and the daemon would have recreated it at 02:33 with all 378 entries intact.

Re-alerting on the current feed contents is safe now — the Google News noise source is
gone, and UPI items score 0.0 so they stay quiet. Confirmed: restarting on an empty
ledger fired exactly the 5 expected Reddit stories and nothing else.

### Still open

UPI items score **0.0** across the board on the keyword scorer, so at present they only
alert when corroborated. The lookup-table problem below is unfixed for that path.

## 2026-08-14 — LLM judge (built, benchmarked, NOT yet wired in)

`meme_judge.py` scores a headline via local Ollama. `judge_eval.py` benchmarks it on 20
real headlines from this session, split into the two documented failure modes: 10 the
keyword scorer rated 0.0, and 10 pieces of "bizarre" sports/marketing filler that caused
all four junk alerts of the first 30 hours.

### Ollama was broken and had never run

`OLLAMA_MODELS` pointed at `D:\Ollama\models`; there is no D: drive, so the server
crash-looped with `exit status 1` and no model was ever downloaded. Repointed to
`G:\Ollama\models` (the 1.74TB Storage Space) to keep weights off C:. **If G: ever
re-letters this breaks again the same way** — the symptom is `ollama list` printing
server logs instead of models.

### Results

| | 3B (llama3.2) | 8B (llama3.1) |
|---|---|---|
| correct @ threshold 6 | 18/20 | 18/19 |
| separation (mean HIGH - mean LOW) | **+6.60** | +7.61 |
| median latency | **3.69s** | 5.41s |
| resident RAM | ~1.0GB | ~5GB |

**Running with 3B despite 8B scoring better.** This box sits at ~0.2GB available RAM with
39.5GB commit — it is deep in the pagefile, and several live Python processes share it.
The 92s cold load observed on 8B was paging, not disk (G: is a Storage Space on SCSI, not
USB). The extra ~1 point of separation is not worth that pressure. `JUDGE_MODEL=llama3.1:8b`
switches it if the machine is ever quieter.

### Two failure modes found, and how each was fixed

**1. The model reproduced the regex bug.** First prompt described the target abstractly;
the 3B model then scored 8.0 on four sports/marketing headlines whose only qualification
was containing the word *"bizarre"* — the exact failure of the lists it was replacing.
Fixed by instructing it to judge the **event, not the vocabulary**, with examples showing
sports/marketing/product headlines score low *even when the headline says "bizarre"*.
14/20 → 17/20. The prompt's examples are deliberately NOT drawn from `judge_eval.py`, so
the benchmark stays honest.

**2. A disaster leaked through.** "Colombia declares economic emergency after deadly
earthquake" scored **8.0** despite the prompt explicitly saying to score human suffering
0-2. Prompts are not a safety mechanism, so `HARD_SUPPRESS` enforces this in code where
the model cannot override it. Suppressed items return 0.0 without an inference call.

**The suppressor then over-blocked, which is the failure mode a deny-list always has.**
Bare disaster words ate real material — "Town declares war on aggressive turkey",
"Photographer **shooting** wildlife documentary", "**Drought** of common sense". Those
terms now require disambiguating context (`war crimes`/`civil war`, `mass shooting`/`shot
dead`, `car crash`/`crashed into`). Now 12/12 good headlines pass and 8/8 harmful ones
block — but this list will keep colliding with idiom, and the bias is deliberately set to
lose a joke rather than alert on a death.

### Operational

`keep_alive` is set to 20m because Ollama's 5-minute default is **shorter than the
6-minute scan interval**, so every scan would otherwise pay a cold load. `JUDGE_TIMEOUT`
is 120s for the same reason. If the model is unreachable, `judge_one` returns `None` and
the caller falls back to keyword scoring — the daemon must never block on inference.

### Integration: the judge ADMITS, the numeric score RANKS

Deliberate, and load-bearing -- do not "simplify" it by adding `judge_score` into
`item["score"]`. The two are independent questions and each catches what the other
misses, demonstrated on a single live scan:

| item | rank/keyword | judge | outcome |
|---|---|---|---|
| Gen Z Redirects Retirement Savings Into Sports Betting | 5.06 (rank 4) | **1.0** | vetoed -- would have alerted |
| Watch: Two loose goats visit Oregon transit station | **0.0** | 9.0 | rescued -- was invisible |
| Watch: Blindfolded man assembles Mr. Potato Head | **0.0** | 9.0 | rescued |
| D.C. bomb squad identifies suspicious device as adult toy | **0.0** | 8.0 | rescued |

Summing would break both directions: a 0-10 term added to Reddit items makes their 3.5
threshold meaningless, and UPI items would stay buried at 0.0 + judge.

Scores are bimodal on live data -- real material at 8-9, ordinary news at 0-1, nothing in
between -- so `JUDGE_MIN_SCORE` anywhere in 4-7 behaves identically. 6.0 sits in the gap.

If the model is unreachable, `judge_score` is absent and admission falls back to the old
per-source numeric thresholds. The daemon never blocks on inference.

**Volume goes up.** Admission went from ~5/13 to ~9/10 items per scan, because the UPI
feed is genuinely on-target and was previously invisible. Expect roughly 30-45 alerts/day
rather than a handful. Raise `JUDGE_MIN_SCORE` to 8 to tighten, though given the bimodal
distribution that mostly trims 8s and keeps 9s.

### Operational quirk: occasional ~40s stall

Benchmark runs show a median of 3.7s with occasional maxima of 32-43s. That is the model
being evicted and reloaded under memory pressure -- `keep_alive: 20m` does not reliably
hold on this box at ~0.2GB available. It stays well inside `JUDGE_TIMEOUT`, so it is a
stall and not a failure, but do not diagnose it later as a hang.

### Known remaining miss

"Texas man jailed after swearing at a public meeting" scores 2.0 ("ordinary news") on
both models. Arguably the benchmark label is wrong rather than the model — it *is* a
crime report.

## Open — the original scorer problem (UPI path only)

**The scorer is a lookup table of stories that already happened.** With the UPI feed
corrected, 25 headlines of exactly the target content score like this:

```
0.0  Watch: Two loose goats visit Oregon transit station, board bus
0.0  Watch: Blindfolded man assembles Mr. Potato Head in 7.92 seconds
0.0  Watch: D.C. bomb squad identifies suspicious device as adult toy
0.0  Look: Escaped tortoise found after 2 weeks, over 12 miles from home
0.0  Watch: Mysterious cat cutouts placed in vacant lots as owl deterrent
3.6  Look: High school football player finds cottonmouth snake inside helmet
3.6  Look: Large lizard caught after nearly a month on the loose in Tennessee
```

22 of 25 score **0.0**. The only two that fire are the ones matching `snake.*helmet`
and `lizard.*loose` — regexes transcribed from headlines that had already run. The
scorer cannot recognize a novel absurd story; it only catches reruns.

The production record agrees: in ~30 hours it fired **4 alerts**, all on the literal
word "bizarre" (a clickbait marker, not a meme marker), and two of those were the same
Tulsa wrestling story that dedup missed because the URL differed
(`tulsaflyer.org` vs `Tulsa Flyer`).

Options, cheapest first:
- Score the *shape* of the story, not its nouns: absurd-juxtaposition patterns
  (`<mundane actor> + <wrong venue>`), "Watch:"/"Look:" prefixes, animal + civic
  infrastructure, superlatives with absurd units.
- Embedding similarity against ~50 exemplar meme headlines — no API key needed with a
  local sentence-transformer, and it generalizes to stories never seen before.
- LLM judge on the ~75 items/scan (README already suggests this). Highest quality,
  costs an API key and per-scan spend.

Dedup should also key on normalized title, not URL path, to catch syndicated reruns.

## Other operational issues (not fixed — your call)

- `CHECK_INTERVAL_MINUTES=1` in `.env` is what earns the Reddit 429s. 15–30 min is
  plenty for RSS and would likely bring all three Reddit feeds back.
- Email alerts fail on **every** alert: Gmail `535 BadCredentials`. Needs an app
  password, or drop the email path and rely on Discord.
- `venv/` contains only pip — the deps live in system Python, which is what the running
  process uses. Either populate the venv or delete it so it stops being misleading.
