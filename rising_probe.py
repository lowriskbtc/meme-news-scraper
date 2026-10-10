#!/usr/bin/env python3
"""Measure how fast r/*/rising turns over, and log first-seen times for later scoring.

Two questions this answers:
  1. Turnover -- if the same ids sit in /rising for 10 minutes, fast polling buys
     nothing and the lead time comes from /rising existing, not from frequency.
  2. Precision -- does appearing in /rising actually predict reaching top/day? Every
     id is logged with a first-seen timestamp so it can be cross-checked a day later.

Usage:  python rising_probe.py [samples] [interval_seconds]
Appends to data/rising_log.jsonl. Sends nothing, touches no scraper state.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

URL = "https://www.reddit.com/r/nottheonion+WeirdNews+FloridaMan/rising/.rss"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"),
}
LOG = Path(__file__).parent / "data" / "rising_log.jsonl"

samples = int(sys.argv[1]) if len(sys.argv) > 1 else 4
interval = int(sys.argv[2]) if len(sys.argv) > 2 else 180

first_seen: dict[str, float] = {}
previous: set[str] = set()

for n in range(samples):
    resp = requests.get(URL, headers=HEADERS, timeout=25)
    now = time.time()
    if resp.status_code != 200:
        print(f"sample {n+1}: HTTP {resp.status_code} -- skipped")
        time.sleep(interval)
        continue

    entries = feedparser.parse(resp.content).entries
    ids = {e.get("id", e.get("link", "")) for e in entries}

    added = ids - previous
    dropped = previous - ids if previous else set()
    churn = "n/a" if not previous else f"+{len(added)} -{len(dropped)}"
    print(f"sample {n+1}: {len(entries)} entries  churn={churn}")

    with open(LOG, "a", encoding="utf-8") as f:
        for e in entries:
            key = e.get("id", e.get("link", ""))
            if key not in first_seen:
                first_seen[key] = now
            pp = e.get("published_parsed") or e.get("updated_parsed")
            age_min = None
            if pp:
                published = datetime(*pp[:6], tzinfo=timezone.utc)
                age_min = round((datetime.now(timezone.utc) - published).total_seconds() / 60, 1)
            f.write(json.dumps({
                "id": key,
                "title": e.get("title", ""),
                "link": e.get("link", ""),
                "age_min_at_sight": age_min,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "sample": n + 1,
            }) + "\n")
        for e in entries[:6]:
            mark = "NEW" if e.get("id", e.get("link", "")) in added else "   "
            print(f"    {mark} {e.get('title','')[:64]}")

    previous = ids
    if n < samples - 1:
        time.sleep(interval)

print(f"\ndistinct posts seen across {samples} samples: {len(first_seen)}")
print(f"logged to {LOG}")
