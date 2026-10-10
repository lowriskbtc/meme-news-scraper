# Where pump.fun lore actually comes from — measured 2026-08-14

Reverse-engineering the lore sources behind coins that bond, to decide whether this
scraper is pointed at the right feeds.

**Short answer: your hypothesis is right — X is ~94% of all external lore. But sourcing
from X is table stakes rather than an edge, and the clock is 30 seconds, not 6 minutes.
The current RSS source set does not appear in bonded lore at all.**

## Method

Data: `https://frontend-api-v3.pump.fun/coins` (works unauthenticated from plain
`requests` with a browser User-Agent — no Cloudflare gate, no key).

| set | filter | n | creation window (UTC) |
|---|---|---|---|
| bonded | `complete=true` | 1000 | 08-13 14:53 → 08-14 14:03 (23.2h) |
| unbonded | `complete=false` | 1000 | 08-14 13:27 → 14:08 (41m) |

`complete` is a real filter, not decorative: zero mint overlap between the two sets,
all 1000 bonded have a `pump_swap_pool`, none of the unbonded do, and median ATH
market cap differs 15× ($37k vs $2.5k).

**The offset caps at ~1000 rows** on both feeds (1100+ returns empty). That is why the
two windows differ in length — 1000 bonded reaches back 23h, 1000 unbonded only 41m.
Every comparison below is checked against that asymmetry.

Classification is by **URL domain**, scraped from `twitter` + `website` + `description`
together. Do not trust the field names — coins routinely put a CNN link in the
`twitter` field and a TikTok link in `website`.

Links reflect launch-time intent: spot-checked 10 bonded coins against their
`metadata_uri` JSON and all 10 matched the API fields exactly, consistent with pump
metadata being write-once.

Reproduce: `lore_analysis/fetch_coins.py` (pulls both sets) then
`lore_analysis/classify.py`. The exact sample behind this document is frozen at
`lore_analysis/coins_2026-08-14.json`.

## 1. The source mix

Percentages are of all 1000 in each set; a coin can carry more than one link.

| source | bonded | unbonded |
|---|---|---|
| **X — specific post** (`x.com/<user>/status/<id>`) | **36.6%** | **60.8%** |
| X — coin's own promo account | 14.6% | 4.3% |
| X — external account (no specific post) | 4.3% | 4.3% |
| TikTok | 3.5% | 3.8% |
| Instagram | 2.3% | 1.1% |
| YouTube | 1.5% | 8.4% |
| Wiki / meme-DB (Fandom, KnowYourMeme) | 1.3% | 0.5% |
| **News article** | **0.9%** | **8.3%** |
| Reddit | 0.6% | 0.7% |
| Twitch | 0.3% | 0.8% |
| — | | |
| any external lore source | 43.4% | 69.8% |
| **no link at all** | **39.6%** | 25.5% |

**Of the coins that have any external lore, 93.8% of it involves X** (92.8% unbonded),
and for 74.4% X is the *only* source. That is the answer to the question as asked.

The non-X remainder, in order: TikTok, Instagram, YouTube, Fandom/KnowYourMeme wikis,
news, Reddit, Twitch.

## 2. X lore is table stakes — it does not separate winners from losers

The raw table looks like X lore *hurts*: 36.6% of bonded coins carry a specific X post
versus 60.8% of unbonded. **That gap is an artifact and should not be believed.** Two
corrections kill it.

**Correction 1 — copycat bursts inflate the unbonded rate.** A single viral tweet mints
dozens of near-identical coins in minutes (§3), and those bursts land disproportionately
in the 41-minute unbonded window: the top 5 lore events alone are 13.2% of that set.
Collapsing every lore event to one coin drops unbonded X-post from 60.8% to **41.2%**
(bonded 36.6% → 29.7%).

**Correction 2 — the two sets are sampled differently.** The bonded set is 23h of
*survivors*; the unbonded set is 41 minutes of a *full population*. Only the overlapping
window compares like with like.

Applying both:

| deduped, same 41-minute window | X-post rate |
|---|---|
| bonded | 43.5% (n=23) |
| unbonded | 41.2% (n=667) |

**Indistinguishable.** With n=23 on the bonded side, this cannot support a claim in
either direction — but it firmly retires the "X lore predicts failure" reading of the
raw numbers.

The defensible conclusion is the weaker one: **sourcing lore from X is table stakes, not
an edge.** Roughly 1460 coins are created per hour and 43 bond — a **~2.2% bond rate**
in the common window. Most of those 1460 already have a viral tweet attached. Finding
the tweet is not the hard part; ~1400 people an hour are already finding it.

Caveat: the unbonded cohort is 41 minutes old and a few will still bond, so 2.2% is a
floor. The steady-state rate implied by the feeds (43 bonded/hr ÷ 1460 created/hr) is
~3%, so the contamination is small.

## 3. What actually separates winners: speed and being first

Decoding tweet snowflake IDs gives the time from tweet posted → first coin minted
(n=492 tweets):

| tweet → first coin | |
|---|---|
| median | **30 seconds** |
| p75 | 4.5 min |
| coined within 10 min | **78%** |
| coined within 1h | 81.5% |

Once a tweet is being coined, the copycats arrive with a **median gap of 10 seconds**,
and the median lore event is fully saturated 3.1 minutes after the first mint.

Being first appears to matter, but the size of the effect is not measurable from this
sample — and the obvious way to measure it is a trap worth recording.

**The trap.** Comparing first-vs-later coins across all 75 lore events with ≥3 coins
gives first 37.3% (28/75) and later 15.4% (63/410). Both numbers are garbage. For any
event older than the 41-minute unbonded window, the sample contains *only that event's
bonded survivors* — its failed copies were never retrievable, because the unbonded feed
caps at ~1000 rows and reaches back only 41 minutes. So old events contribute coins
that bonded almost by construction, inflating both rates.

Restricting to the 50 events whose coins fall **entirely inside the common 41-minute
window** — where both bonded and unbonded copies are observable — gives the unbiased
version:

| | bond rate |
|---|---|
| first coin on a lore event | 6.0% (3/50) |
| every later coin on the same event | 0.6% (2/332) |

The direction is consistent with first-mover advantage and the ratio is large, but it
rests on **3 and 2 coins respectively** — far too little to quote a multiplier from.
Both figures are also floors, since coins in that window may still bond. Treat "being
first helps" as directional only; the honest measurement needs a longer unbonded
history than this endpoint will serve.

The race is visible in the raw data. One @elonmusk tweet about a hovering Tesla
roadster produced 42 coins from 25 distinct creators under 14 different names —
`READ TWEET`, `CAN YALL SEE`, `READ READ READ`, `HOVERING`, `Hovering tesla roadster`.
A @Strawberriiisa tweet produced 46 coins all named `THE ACTUAL NAME`.

## 4. Potential lore sources — who to actually watch

Ranked by **distinct creator wallets** referencing the handle, not raw coin count.
That filters out deployers spamming their own promo account across their own launches:
a handle cited by 25 unrelated creators is a lore source, a handle cited 20 times by
2 wallets is an ad.

| handle | distinct creators | coins |
|---|---|---|
| @elonmusk | 25 | 52 |
| @seyong | 23 | 29 |
| @Devorskiiii | 18 | 24 |
| @a1lon9 (pump.fun cofounder) | 14 | 17 |
| @ddddddd8a | 13 | 17 |
| @K1rajj | 11 | 12 |
| @xilingfuyi | 11 | 17 |
| @lilbruhfish | 10 | 17 |
| @Luigiferforever | 9 | 22 |
| @imsheepsol | 7 | 13 |
| @cb_doge | 7 | 7 |
| @kylesamani | 7 | 8 |
| @whitehouse | 6 | 6 |
| @Minecraft | 6 | 6 |

Two distinct populations here: genuine outside-world accounts whose posts get mined
(@elonmusk, @whitehouse, @Minecraft, @cb_doge) and crypto-native KOLs whose posts are
themselves the event (@seyong, @Devorskiiii, @a1lon9, @kylesamani).

Highest-ATH examples per non-X platform, as a source shortlist:

- **TikTok** — `Larping Final Boss` ($816k, `@simbabimbababy`), `risking all my aura`
  ($768k), `Tung Tung Tung Sahur` ($745k). Note two of four link a TikTok *search
  query*, not a video — the trend, not one post.
- **Instagram** — `Niles The Croc` ($1.08M, a single `/p/` post), `Catecoin` ($795k, a
  kabosumama story).
- **YouTube** — `Jimothy The Raccoon` ($2.68M — the largest lore-backed coin in the
  sample), `Shane Gillis Coin` ($945k, deep-linked to `?t=5408`, a specific bit).
- **Fandom / KnowYourMeme** — `The Toad Pepe` ($927k, `elreinoinfantil.fandom.com`),
  `そらジロー` ($893k, `japanese-tv-mascot.fandom.com`). Evergreen character lore, not news.
- **News** — `Ebola Virus` ($964k, a 2021 CNN article), `Justice for Carolina Molina`
  ($734k, WaPo, the ICE-agent dashcam story), `영끌 Generation` ($255k, Reuters).
- **Reddit** — `Dollar Tree Gooner` ($99k, r/news). Thin.

## 5. What this means for this scraper

The measurements here are unkind to the current design, and it is better to say so
plainly than to bolt an X feed onto the side.

1. **The configured sources are never cited as lore.** Across all 1000 bonded coins
   there are **no** references to `upi.com`, `nottheonion`, `WeirdNews`, or `FloridaMan`
   — not one. Six coins (0.6%) link `reddit.com` at all, none to the three configured
   subreddits. Every news outlet combined accounts for 0.9%.

   Read this precisely: it is a test of *cited links*, not of story origin. A coin
   minted off a Florida-man story would link the tweet that carried it, not the
   subreddit — so this does not prove those feeds can never surface usable material.
   What it does bound is the ceiling: news of every kind is 0.9% of bonded lore. The
   decisive argument against the current design is the latency one below, not this
   string search.

2. **The latency budget is off by two orders of magnitude.** `CHECK_INTERVAL_MINUTES`
   is 6 and `MAX_AGE_HOURS` is 12. The median tweet is coined in **30 seconds** and
   the median lore event is saturated in **3.1 minutes**. A 12-hour freshness window
   is not a filter in this game; every story admitted by it was coined ~700 times
   before the scan ran. This is also why UPI's ~15h publication lag is fatal rather
   than merely inconvenient — corroboration via UPI can only confirm a story long
   after the race is over.

3. **The 48h corroboration window measures the wrong thing.** Cross-source spread over
   48h is a good virality signal for news. Here the entire decision window is minutes,
   and corroboration by a second source is evidence you are already late.

4. **If the goal is to be first, the target is a small set of X accounts, not a
   firehose.** Section 4's list is short and dominated by a handful of handles. Polling
   ~20 accounts' timelines is a much smaller problem than classifying 75 RSS items a
   scan, and it is where 94% of the lore is. That needs X API access or an
   authenticated scrape — which `NOTES.md` already rejected for the RSS daemon on
   cookie-rot grounds. That rejection was correct for a background RSS process and is
   worth revisiting on its own terms for this use case.

5. **Reconsider whether the target is worth chasing at all.** A ~2.2% base bond rate, a
   median reaction time of 30 seconds, and opponents who are visibly automated — 99
   creator wallets produced 323 of the 1000 bonded coins, and two wallets produced 21
   each. Even granting a first-mover edge, winning requires beating automated deployers
   to a tweet by seconds. That is a latency-engineering problem, not a news-scoring
   problem, and it is a different project from this one.

## 5b. The source tweets themselves — you cannot filter on virality

Added after building `lore_analysis/enrich_tweets.py`, which resolves the tweet URLs in
pump.fun metadata to real content for free via `cdn.syndication.twimg.com/tweet-result`
(no key, no account). No rate limiting at 0.8s spacing across 522 fetches.

**423 of 522 distinct tweets resolved (81.0%). The other 99 (19.0%) are tombstoned** —
deleted, suspended, or age-restricted. Those still return HTTP 200 with valid JSON
(`{"__typename":"TweetTombstone"}`), which is why an earlier draft of this section
reported 100%: the tool was counting tombstones as hits. Nearly a fifth of pump.fun lore
links are dead on arrival, which is worth knowing on its own.

For the tweets that do resolve we get like and reply counts. The result is the most
operationally useful thing in this document.

Measuring on the *unbonded* set — where the coin is at most 41 minutes old, so
likes-now ≈ likes-at-coin-time — restricted to tweets coined within 2h of posting, and
**deduped to one row per distinct tweet** (essential: one tweet spawned 46 coins here,
and burst-weighted rows would count the same tweet dozens of times):

| at the moment it was first coined | n=149 distinct tweets |
|---|---|
| median tweet age | **0.3 min (18 seconds)** |
| **median likes** | **1** |
| p75 likes | 10 |
| p90 likes | 184 |
| under 10 likes | **74%** |
| under 1000 likes | 97% |

**The median coined tweet is 18 seconds old and has one like.** Whatever virality these
tweets later acquire happens *after* the coin exists, not before. And because likes are
read at fetch time — minutes into the tweet's life, not at the instant of coining — 1
like is a **ceiling**, not an estimate.

This kills the obvious strategy. "Watch X for viral posts and launch on them" cannot
work, because by the time a tweet has measurable engagement it has already been coined,
usually many times over (§3). There is no engagement threshold that separates
coin-worthy tweets from noise at the moment the decision has to be made.

The natural reading is that the fast actors are not filtering on virality at all — they
are coining everything from a watchlist and letting the market sort it out. That is an
inference, not a measurement: a keyword rule or a broad firehose filter would produce
the same signature. What *is* measured is that engagement carries no usable signal at
decision time.

It also reframes §4: the value of that account list may be less that those accounts post
viral things, and more that they are the accounts being *watched* by the people who coin
fast.

Caveat on units: `favorite_count` is read at fetch time, so for the bonded set (up to
23h old) likes have had far longer to accumulate — which is why bonded shows median 32
vs unbonded 6 across all tweets. That gap is measurement lag, not a real difference, and
the table above deliberately uses only the near-simultaneous subset.

Language mix of the 423 resolved tweets: **~90% English** (380), the rest Japanese (10),
Turkish, Spanish, Korean, Portuguese, plus 9 media-only posts with no text. Non-English
is far more common among the *highest*-engagement coined tweets than in the population —
large foreign-language viral clips get imported as lore — but the bulk of the feed is
English.

## 6. What this does not show

- **Bonding is not profit.** Everything above treats bonding as the outcome. A coin can
  bond and still lose its buyers money, so any edge measured here is an edge in
  *bonding*, not in realised PnL.
- **No causal claim.** Whatever first-mover edge exists may partly reflect that the
  fastest actors are also the best-capitalised and most automated, not that earliness
  itself causes bonding.
- **The first-mover effect is directional only** (3 and 2 coins, §3). Do not size a
  strategy off it.
- **Sub-1% buckets are fragile.** News at 0.9% is 9 coins; Reddit at 0.6% is 6. Do not
  over-read those, though the order-of-magnitude conclusion in §5.1 is safe.
- **One 23-hour window**, dominated by a few live events (a hovering-Tesla-roadster
  story alone drove the 8.3% unbonded news figure via `teslarati.com`). Rerun before
  treating the per-platform mix as stable.
- **`No link` (39.6% of bonded) is not analysed.** Some are lore-free bot launches;
  some carry lore in the coin *name* with no URL. Ticker-text analysis would be needed
  to split them, and it would move the "external lore" share upward by an unknown amount.
