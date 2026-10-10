#!/usr/bin/env python3
"""Score one live scan and print the ranking. Sends no alerts, writes no state.

Use this to tune MAX_AGE_HOURS / min_score / VELOCITY_GAIN without firing the
Discord webhook or poisoning data/seen.json:

    python dryrun.py
"""
import meme_scraper as ms

items = ms.fetch_all()
items.sort(key=lambda x: x["score"], reverse=True)

print(f"\n{len(items)} items within MAX_AGE_HOURS={ms.MAX_AGE_HOURS}\n")
print(f"{'score':>6} {'judge':>6} {'thr':>5} {'rank':>5} {'age':>7}  source / title")
print("-" * 100)

fired = 0
for i in items:
    judge = i.get("judge_score")
    if judge is not None:
        threshold = ms.JUDGE_MIN_SCORE
        hit = judge >= threshold
    else:
        threshold = i.get("min_score", ms.MIN_SCORE)
        hit = i["score"] >= threshold
    fired += hit
    age = "-" if i["age_hours"] is None else f"{i['age_hours']}h"
    rank = i["rank"] or "-"
    flag = "*" if hit else " "
    corr = " [corroborated]" if i.get("corroborated_by") else ""
    jd = "-" if judge is None else f"{judge:g}"
    print(f"{i['score']:>6} {jd:>6} {threshold:>5} {rank:>5} {age:>7}  "
          f"{flag} {i['source'][:22]:<22} {i['title'][:52]}{corr}")

print(f"\nwould alert on {fired} of {len(items)}")
