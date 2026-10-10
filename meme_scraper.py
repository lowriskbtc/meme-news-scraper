#!/usr/bin/env python3
"""
Viral Meme Potential News Scraper
---------------------------------
Monitors RSS feeds + selected pages for odd / absurd / high-meme-potential stories.
Scores them, deduplicates, and sends alerts (Discord webhook recommended).

Requirements:
    pip install requests feedparser beautifulsoup4 schedule python-dotenv

Usage:
    1. Copy .env.example → .env and fill in your Discord webhook (and optional email).
    2. python meme_scraper.py
    3. (Optional) Run under cron or as a background service.
"""

import os
import re
import json
import hashlib
import logging
import smtplib
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import List, Dict, Optional, Set
from urllib.parse import urlparse

import requests
import feedparser
from bs4 import BeautifulSoup
from dotenv import load_dotenv

import meme_judge

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

# Discord (recommended – easiest)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# Optional email alerts
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")          # app password for Gmail
EMAIL_TO = os.getenv("EMAIL_TO", "")

# How often to check (minutes)
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))

# Minimum score to trigger an alert
MIN_SCORE = float(os.getenv("MIN_SCORE", "4.0"))

# Admission threshold for the LLM judge. Live scans score bimodally -- real
# material lands at 8-9 and ordinary news at 0-1 -- so anything in 4-7 works;
# 6.0 sits in the empty middle.
JUDGE_MIN_SCORE = float(os.getenv("JUDGE_MIN_SCORE", "6.0"))

# Data directory (seen articles + logs)
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
SEEN_FILE = DATA_DIR / "seen.json"
LOG_FILE = DATA_DIR / "scraper.log"

# User-Agent. The old custom "MemePotentialScraper/1.0" string got a hard 403 from
# UPI (same URL returns 200 with a normal browser UA), so the highest-weight odd-news
# source was silently returning zero items on every scan.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
}

# Per-source cooldown after an HTTP 429, so a rate-limited host (Reddit) is not
# re-hammered every cycle. Maps source name -> epoch seconds to resume at.
_cooldown_until: Dict[str, float] = {}
RATE_LIMIT_COOLDOWN_SECONDS = 15 * 60

# Conditional-GET state per source: ETag / Last-Modified from the previous fetch.
_conditional: Dict[str, Dict[str, str]] = {}

# Why each source returned what it did on the last scan, so that the per-scan yield
# line can tell "0 items because nothing changed" apart from "0 items because dead".
_last_outcome: Dict[str, str] = {}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Meme-potential scoring
# ---------------------------------------------------------------------------
# High-value keywords / patterns (tune these over time)
HIGH_VALUE = [
    r"\b(fart|flatulence|gas leak|stinker|noxious smell)\b",
    r"\b(grim reaper|death\b.*roof|costume.*hospital)\b",
    r"\b(capybara|raccoon|bear.*pool|lizard.*loose|snake.*helmet|monitor lizard)\b",
    r"\b(porta.?potty|portable toilet|toilet tank|stuck.*toilet)\b",
    r"\b(catering truck|food truck one|secret.*plane|plane swap|hid in)\b",
    r"\b(clothing.?optional|nude.*restaurant|naked.*train|nude dining)\b",
    r"\b(spikes.*lawn|get off my lawn)\b",
    r"\b(peter pan.*girl|disney.*obsess|disney adult)\b",
    r"\b(viral|explodes online|takes over|meme.?worthy|meme potential)\b",
    r"\b(legislature|congress|parliament|assembly).*(capybara|rodent|animal|wander|invade)\b",
    r"\b(capybara|rodent).*(legislature|congress|parliament|lobby|vote)\b",
]

MEDIUM_VALUE = [
    r"\b(weird|bizarre|odd|strange|absurd|ridiculous|hilarious)\b",
    r"\b(unusual|unexpected|unlikely|shocking|unbelievable)\b",
    r"\b(arrested.*costume|dressed as|impersonat)\b",
    r"\b(animal|pet|wildlife).*(invade|enter|crash|wander|lobby|roam)\b",
    r"\b(political|congress|legislature|politician).*(animal|rodent|capybara)\b",
    r"\b(record.?breaking|world record).*(stroller|backheel|mile)\b",
    r"\b(theater|cinema|movie).*(flee|evacuat|clear|stench|smell)\b",
]

LOW_VALUE = [
    r"\b(funny|comedy|joke|prank)\b",
    r"\b(internet|social media|tiktok|viral video)\b",
]

def score_text(text: str) -> float:
    """Simple keyword-based meme potential score."""
    if not text:
        return 0.0
    text_l = text.lower()
    score = 0.0

    for pat in HIGH_VALUE:
        if re.search(pat, text_l, re.I):
            score += 3.0
    for pat in MEDIUM_VALUE:
        if re.search(pat, text_l, re.I):
            score += 1.5
    for pat in LOW_VALUE:
        if re.search(pat, text_l, re.I):
            score += 0.5

    # Bonus for short punchy titles (classic meme fuel)
    words = len(text.split())
    if 5 <= words <= 14:
        score += 0.8

    return round(score, 2)


# ---------------------------------------------------------------------------
# Freshness + virality
# ---------------------------------------------------------------------------
# These three were already documented in env.example and set in .env, but nothing
# in the code ever read them — the whole freshness feature was configured and never
# implemented. Wiring them up rather than inventing parallel knobs.
#
# Anything older than this is dropped outright, whatever it scores.
MAX_AGE_HOURS = float(os.getenv("MAX_AGE_HOURS", "24"))
# Flat bonus for stories published within the last FRESHNESS_BOOST_HOURS.
FRESHNESS_BOOST_HOURS = float(os.getenv("FRESHNESS_BOOST_HOURS", "2.0"))
FRESHNESS_BOOST_POINTS = float(os.getenv("FRESHNESS_BOOST_POINTS", "2.0"))

# Reaching /top/?t=day across these subs at all is most of the signal — it is roughly
# 20 posts out of everything submitted that day. So a post starts from a flat base for
# being in the feed, gains a modest amount for position within it, and decays gently
# with age. Age is a multiplier, never a divisor: a Reddit post structurally cannot be
# young (nothing observed under 3.7h, because reaching top/day takes hours of voting),
# so dividing by age penalises posts for the very mechanism that produces the signal.
FEED_BASE_POINTS = 4.0
POSITION_BONUS_MAX = 3.0
AGE_HALF_LIFE_HOURS = 18.0


def entry_age_hours(entry) -> Optional[float]:
    """Hours since publication, or None if the feed gave no usable timestamp."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        published = datetime(*parsed[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) - published).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Cross-source corroboration
# ---------------------------------------------------------------------------
# A story that surfaces independently in two unrelated feeds is spreading — that is
# direct evidence of reach, and it is the one signal that rescues stories the keyword
# scorer cannot see. "Two loose goats visit Oregon transit station, board bus" (UPI,
# scores 0.0) and "Goats gone wild: Loose pygmy goats caught boarding TriMet bus"
# (Reddit, rank 16) are the same event, and neither copy clears its own threshold.
CORROBORATION_BONUS = float(os.getenv("CORROBORATION_BONUS", "3.0"))
CORROBORATION_MIN_OVERLAP = float(os.getenv("CORROBORATION_MIN_OVERLAP", "0.24"))

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for", "with",
    "after", "over", "from", "by", "as", "is", "was", "are", "were", "be", "been",
    "his", "her", "its", "their", "this", "that", "these", "those", "it", "he", "she",
    "they", "who", "has", "have", "had", "not", "no", "new", "says", "said", "up",
    "out", "off", "into", "than", "then", "when", "while", "watch", "look", "video",
}


def _stem(word: str) -> str:
    """Crude suffix stripping — enough to match board/boarding, goat/goats."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def title_tokens(title: str) -> Set[str]:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {_stem(w) for w in words if w not in _STOPWORDS and len(w) > 2}


def _overlap(a: Set[str], b: Set[str]) -> float:
    """Jaccard similarity of two token sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# Matching only within a single scan cannot work here: Reddit carries a story while it
# is breaking (3-11h old) and UPI publishes its own copy on a ~15h lag, by which point
# the Reddit copy has aged out of the feed. The two windows never overlap. So titles are
# remembered for RECENT_WINDOW_HOURS and each scan is matched against that history.
RECENT_FILE = DATA_DIR / "recent.json"
RECENT_WINDOW_HOURS = float(os.getenv("RECENT_WINDOW_HOURS", "48"))


def load_recent() -> List[Dict]:
    if RECENT_FILE.exists():
        try:
            with open(RECENT_FILE, "r", encoding="utf-8") as f:
                entries = json.load(f).get("entries", [])
        except Exception as e:
            logger.warning(f"Could not load recent file: {e}")
            return []
        cutoff = time.time() - RECENT_WINDOW_HOURS * 3600
        return [e for e in entries if e.get("ts", 0) >= cutoff]
    return []


def save_recent(entries: List[Dict]):
    cutoff = time.time() - RECENT_WINDOW_HOURS * 3600
    kept = [e for e in entries if e.get("ts", 0) >= cutoff][-4000:]
    with open(RECENT_FILE, "w", encoding="utf-8") as f:
        json.dump({"entries": kept, "updated": datetime.now(timezone.utc).isoformat()},
                  f, indent=2)


def apply_corroboration(items: List[Dict], history: List[Dict]) -> int:
    """Boost stories that also appeared in a *different* source, now or recently.

    Also the honest fix for dedupe: the old id was a hash of URL path + title, so the
    same syndicated story from two outlets alerted twice. Matching on title tokens
    recognises a repeat as the same event regardless of which outlet carried it.
    """
    token_sets = [title_tokens(i["title"]) for i in items]
    # Current scan and remembered history are matched against identically.
    known = [(set(h.get("tokens", [])), h.get("source", "")) for h in history]
    known += [(token_sets[i], items[i]["source"]) for i in range(len(items))]
    boosted = 0

    for idx, item in enumerate(items):
        distinct = {
            src for tokens, src in known
            if src != item["source"]
            and _overlap(token_sets[idx], tokens) >= CORROBORATION_MIN_OVERLAP
        }
        if distinct:
            item["score"] = round(item["score"] + CORROBORATION_BONUS * len(distinct), 2)
            item["corroborated_by"] = sorted(distinct)
            boosted += 1

    return boosted


def remember_titles(items: List[Dict], history: List[Dict]) -> List[Dict]:
    """Append this scan's titles to the corroboration history."""
    now = time.time()
    seen_ids = {h.get("id") for h in history}
    for item in items:
        if item["id"] not in seen_ids:
            history.append({
                "id": item["id"],
                "source": item["source"],
                "tokens": sorted(title_tokens(item["title"])),
                "ts": now,
            })
    return history


def rank_freshness_score(rank: int, total: int, age_hours: Optional[float]) -> float:
    """Score a community-ranked post: made the cut, where in it, and how stale."""
    if total <= 0:
        return 0.0
    position = POSITION_BONUS_MAX * (1.0 - (rank - 1) / total)
    score = FEED_BASE_POINTS + position
    if age_hours is not None:
        score *= 0.5 ** (age_hours / AGE_HALF_LIFE_HOURS)
    return round(score, 2)


# ---------------------------------------------------------------------------
# Sources (RSS preferred – more reliable & polite)
# ---------------------------------------------------------------------------
RSS_SOURCES = [
    {
        "name": "UPI Odd News",
        # NOT https://www.upi.com/rss/Odd_News/ — that path returns HTTP 200 with
        # UPI's general top-news feed (Senate hearings, earthquakes), so the odd-news
        # source was quietly feeding politics into the scorer. This is the real one.
        "url": "https://rss.upi.com/news/odd_news.rss",
        "kind": "editorial",   # already curated as odd news; regex ranks within it
        "weight": 1.2,
        "min_score": 1.0,
        # UPI publishes on a ~15h lag, so the global 12h cap silences this source
        # entirely. A curated odd-news story is still good material at 15h even
        # though it is not "breaking" — it just needs its own bar.
        "max_age_hours": 30.0,
    },
    {
        # ONE request covering several subs, not one per sub. Reddit rate-limits a
        # burst from a single IP: the first .rss request in a scan returned 200 on
        # 551 of 553 scans, while the 2nd and 3rd were 429 and then sat in cooldown
        # essentially forever (r/news: 1 good scan out of 553).
        #
        # top/?t=day sorts by community score, so feed position is a free virality
        # signal — no OAuth needed. (?t=hour returns zero entries, and the plain
        # .json endpoints are 403 without OAuth.)
        "name": "Reddit odd multireddit",
        "url": "https://www.reddit.com/r/nottheonion+WeirdNews+FloridaMan/top/.rss?t=day",
        "kind": "reddit_ranked",  # rank IS the score; regex adds nothing here
        "weight": 1.0,
        "min_score": 3.5,
    },
    # Dropped: Google News "bizarre OR odd news" keyword search. It produced all four
    # alerts of the first 30h and every one was junk, because "bizarre" is sports-desk
    # filler — the feed returns "bizarre LSU beef", "bizarre weather excuse", "Bizarre
    # play ... Red Sox-Jays delay" and JoJo's Bizarre Adventure. Google News' curated
    # ODDITIES topic feed was checked as a replacement and returns zero entries.
]

# Optional HTML pages (scraped more carefully)
HTML_SOURCES = [
    # Add more if you want; keep rate-limited
]


# ---------------------------------------------------------------------------
# Persistence (seen articles)
# ---------------------------------------------------------------------------
def load_seen() -> Dict[str, None]:
    # Insertion-ordered dict, not a set: save_seen() truncates to the most recent
    # 2000 ids, and a set has no order to truncate by — it would drop arbitrary
    # entries and re-alert old stories once the cap was reached.
    if SEEN_FILE.exists():
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return dict.fromkeys(data.get("ids", []))
        except Exception as e:
            logger.warning(f"Could not load seen file: {e}")
    return {}


def save_seen(seen: Dict[str, None]):
    # Keep only the most recent ~2000 to avoid unbounded growth
    ids = list(seen)[-2000:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"ids": ids, "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)


def make_id(url: str, title: str) -> str:
    raw = f"{urlparse(url).path}|{title}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def fetch_rss(source: Dict) -> List[Dict]:
    results = []
    name = source["name"]

    _last_outcome[name] = "?"

    resume_at = _cooldown_until.get(name, 0.0)
    if time.time() < resume_at:
        _last_outcome[name] = "cooldown"
        logger.debug(f"{name}: in rate-limit cooldown for another "
                     f"{int(resume_at - time.time())}s, skipping")
        return results

    try:
        # Fetch with requests rather than letting feedparser do it, so the HTTP
        # status is visible. feedparser returns an empty entry list for a 403/429
        # exactly as it does for a genuinely empty feed, which is how three of the
        # five sources stayed dead without ever logging anything.
        headers = dict(HEADERS)
        headers.update(_conditional.get(name, {}))
        resp = requests.get(source["url"], headers=headers, timeout=20)

        if resp.status_code == 304:  # not modified since last scan — healthy
            _last_outcome[name] = "304"
            logger.debug(f"{name}: 304 Not Modified")
            return results

        if resp.status_code == 429:
            _last_outcome[name] = "429"
            _cooldown_until[name] = time.time() + RATE_LIMIT_COOLDOWN_SECONDS
            logger.warning(f"{name}: HTTP 429 rate-limited — backing off for "
                           f"{RATE_LIMIT_COOLDOWN_SECONDS // 60} min")
            return results

        if resp.status_code != 200:
            _last_outcome[name] = f"HTTP{resp.status_code}"
            logger.warning(f"{name}: HTTP {resp.status_code} — no items this scan")
            return results

        # Remember validators so the next scan can send a conditional request.
        validators = {}
        if resp.headers.get("ETag"):
            validators["If-None-Match"] = resp.headers["ETag"]
        if resp.headers.get("Last-Modified"):
            validators["If-Modified-Since"] = resp.headers["Last-Modified"]
        _conditional[name] = validators

        feed = feedparser.parse(resp.content)
        _last_outcome[name] = "ok"
        if not feed.entries:
            _last_outcome[name] = "empty200"
            logger.warning(f"{name}: HTTP 200 but zero entries parsed "
                           f"({len(resp.content)} bytes) — feed URL may have moved")

        entries = feed.entries[:25]  # limit per source
        kind = source.get("kind", "editorial")
        stale = 0

        for rank, entry in enumerate(entries, start=1):
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = entry.get("summary", entry.get("description", "")).strip()
            published = entry.get("published", "") or entry.get("updated", "")

            # Clean HTML from summary
            if summary:
                summary = BeautifulSoup(summary, "html.parser").get_text(" ", strip=True)

            if not title or not link:
                continue

            age_hours = entry_age_hours(entry)
            max_age = source.get("max_age_hours", MAX_AGE_HOURS)
            if age_hours is not None and age_hours > max_age:
                stale += 1
                continue

            if kind == "reddit_ranked":
                # Community rank already encodes "do people find this remarkable".
                # Running the keyword regexes over it would only re-introduce the
                # lookup-table failure: it scores reruns of past stories, not new ones.
                base_score = rank_freshness_score(rank, len(entries), age_hours)
            else:
                base_score = score_text(f"{title} {summary}")

            final_score = base_score * source.get("weight", 1.0)
            if age_hours is not None and age_hours <= FRESHNESS_BOOST_HOURS:
                final_score += FRESHNESS_BOOST_POINTS
            final_score = round(final_score, 2)

            results.append({
                "id": make_id(link, title),
                "title": title,
                "url": link,
                "summary": summary[:400] + ("..." if len(summary) > 400 else ""),
                "source": source["name"],
                "published": published,
                "score": final_score,
                "raw_score": base_score,
                "age_hours": round(age_hours, 1) if age_hours is not None else None,
                "rank": rank if kind == "reddit_ranked" else None,
                "min_score": source.get("min_score", MIN_SCORE),
            })

        if stale:
            logger.debug(f"{name}: dropped {stale} entries older than "
                         f"{source.get('max_age_hours', MAX_AGE_HOURS)}h")
    except Exception as e:
        logger.error(f"Failed to fetch {source['name']}: {e}")
    return results


def fetch_all() -> List[Dict]:
    all_items = []
    per_source = []
    for src in RSS_SOURCES:
        items = fetch_rss(src)
        outcome = _last_outcome.get(src["name"], "?")
        # Annotate anything that is not a plain successful fetch, so a source that
        # is merely unchanged (304) cannot be mistaken for one that has gone dead.
        suffix = "" if outcome == "ok" else f"({outcome})"
        per_source.append(f"{src['name']}={len(items)}{suffix}")
        all_items.extend(items)
        time.sleep(1.2)  # polite delay
    logger.info("Source yield: " + ", ".join(per_source))

    # Read-only here so dryrun.py can score a scan without mutating state; run_once()
    # is what writes the history back.
    boosted = apply_corroboration(all_items, load_recent())
    if boosted:
        logger.info(f"Corroboration: {boosted} items matched another source")

    if meme_judge.available():
        judged = meme_judge.judge_items(all_items)
        scored = [i["judge_score"] for i in all_items if "judge_score" in i]
        if scored:
            logger.info(f"Judge: {judged} new judgments, {len(scored)} items scored, "
                        f"{sum(1 for s in scored if s >= JUDGE_MIN_SCORE)} above "
                        f"admission threshold {JUDGE_MIN_SCORE}")
    else:
        logger.warning(f"Judge unavailable ({meme_judge.JUDGE_MODEL}) -- "
                       "falling back to keyword/rank thresholds")

    # Sort by score descending
    all_items.sort(key=lambda x: x["score"], reverse=True)
    return all_items


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------
def send_discord(item: Dict):
    if not DISCORD_WEBHOOK_URL:
        return
    embed = {
        "title": item["title"][:250],
        "url": item["url"],
        "description": item["summary"][:400] or "No summary",
        "color": 0xFF6B35 if item["score"] >= 6 else 0xF7C948,
        "fields": [
            {"name": "Score", "value": str(item["score"]), "inline": True},
            {"name": "Source", "value": item["source"], "inline": True},
            {"name": "Age", "value": (f"{item['age_hours']}h"
                                      if item.get("age_hours") is not None else "n/a"),
             "inline": True},
        ],
        "footer": {"text": ("Meme Potential Scraper — also ran by "
                            + ", ".join(item["corroborated_by"])
                            if item.get("corroborated_by") else "Meme Potential Scraper")},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload = {"embeds": [embed]}
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        r.raise_for_status()
        logger.info(f"Discord alert sent: {item['title'][:60]}...")
    except Exception as e:
        logger.error(f"Discord send failed: {e}")


def send_email(item: Dict):
    if not (EMAIL_USER and EMAIL_PASS and EMAIL_TO):
        return
    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg["Subject"] = f"[Meme Alert {item['score']}] {item['title'][:80]}"

    body = f"""
New high meme-potential story detected!

Title: {item['title']}
Score: {item['score']}
Source: {item['source']}
Published: {item.get('published', 'n/a')}

{item['summary']}

Link: {item['url']}

---
Meme Potential Scraper
"""
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
        logger.info(f"Email alert sent: {item['title'][:60]}...")
    except Exception as e:
        logger.error(f"Email send failed: {e}")


def alert(item: Dict):
    send_discord(item)
    send_email(item)
    # Always log
    rank = f" rank={item['rank']}" if item.get("rank") else ""
    age = f" age={item['age_hours']}h" if item.get("age_hours") is not None else ""
    corr = f" +corroborated:{','.join(item['corroborated_by'])}" if item.get("corroborated_by") else ""
    logger.info(f"ALERT score={item['score']}{rank}{age}{corr} | {item['source']} | {item['title']}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_once(seen: Dict[str, None]) -> Dict[str, None]:
    logger.info("Scanning sources...")
    items = fetch_all()
    new_alerts = 0

    for item in items:
        if item["id"] in seen:
            continue
        seen[item["id"]] = None

        # The judge ADMITS, the numeric score RANKS. Deliberate choice -- do not
        # "simplify" this by adding judge_score into item["score"].
        #
        # The two questions are independent. Rank/keyword scores answer "is the crowd
        # biting, and is it fresh"; the judge answers "is this even the kind of story
        # we want". A story needs both, and on live data each catches what the other
        # misses: "Gen Z Redirects Retirement Savings Into Sports Betting" scored 5.07
        # on rank (would have alerted) but 1.0 on the judge; every UPI item scored 0.0
        # on keywords but 8-9 on the judge. Summing them would break both directions --
        # a 0-10 term added to Reddit items makes their 3.5 threshold meaningless.
        judge_score = item.get("judge_score")
        if judge_score is not None:
            passes = judge_score >= JUDGE_MIN_SCORE
        else:
            # Judge unavailable (model down, or disabled) -- fall back to the previous
            # numeric behaviour rather than alerting on everything or nothing.
            passes = item["score"] >= item.get("min_score", MIN_SCORE)

        if passes:
            alert(item)
            new_alerts += 1
            time.sleep(0.8)  # avoid rate limits on webhooks

    save_seen(seen)
    save_recent(remember_titles(items, load_recent()))
    logger.info(f"Scan complete. New alerts: {new_alerts}. Total seen: {len(seen)}")
    return seen


def main():
    logger.info("=== Meme Potential News Scraper started ===")
    if not DISCORD_WEBHOOK_URL and not (EMAIL_USER and EMAIL_TO):
        logger.warning("No alert method configured! Set DISCORD_WEBHOOK_URL or email vars in .env")

    seen = load_seen()
    logger.info(f"Loaded {len(seen)} previously seen articles")

    # Immediate first run
    seen = run_once(seen)

    # Then periodic
    try:
        import schedule
        schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(lambda: run_once(seen))
        logger.info(f"Scheduled every {CHECK_INTERVAL_MINUTES} minutes. Ctrl+C to stop.")
        while True:
            schedule.run_pending()
            time.sleep(20)
    except ImportError:
        logger.warning("schedule package not installed – running once only. Install with: pip install schedule")
    except KeyboardInterrupt:
        logger.info("Stopped by user.")


if __name__ == "__main__":
    main()
