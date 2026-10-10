#!/usr/bin/env python3
"""Does the local model actually have the taste we need? And how fast is it?

Every headline below is real, pulled from live feeds during this session. The set is
built around the two documented failure modes:

  * SHOULD score high -- headlines the keyword scorer rated 0.0 ("Mr. Potato Head",
    "two loose goats"). These are the misses that made the scraper feel useless.
  * SHOULD score low  -- the sports-desk and marketing filler that the word "bizarre"
    dragged in, which produced all four junk alerts of the first 30 hours.

A model that cannot separate these two groups is not worth wiring in.
"""
import statistics
import sys
import time

import meme_judge

SHOULD_BE_HIGH = [
    "Watch: Two loose goats visit Oregon transit station, board bus",
    "Watch: Blindfolded man assembles Mr. Potato Head in 7.92 seconds",
    "Watch: D.C. bomb squad identifies suspicious device as adult toy",
    "Nigel Farage faces off against Count Binface in special election",
    "A boat operator used a foghorn to wake a sleeping polar bear. They were fine",
    "Texas man jailed after swearing at a public meeting",
    "Look: High school football player finds cottonmouth snake inside helmet",
    "Abandoned llama not interested in being rescued on Colorado trail",
    "Look: Escaped tortoise found after 2 weeks, over 12 miles from home",
    "Watch: Mysterious cat cutouts placed in vacant lots as apparent owl deterrent",
]

SHOULD_BE_LOW = [
    "Senate Democrats want answers on reported suicide attempts on warship",
    "Judge dismisses DOJ suit against Harvard for alleged anti-Semitism",
    "Colombia declares economic emergency after deadly earthquake",
    "Jayden Daniels' bizarre LSU beef is petty and sad at the same time",
    "Bizarre play, rules check leads to lengthy Red Sox-Jays delay",
    "Wanda Cinemas Games Launches JoJo's Bizarre Adventure: Golden Spin",
    "J.J. McCarthy issues bizarre weather excuse after losing Vikings game",
    "Domino's Turns Its Logo Into A Pizza Made For One",
    "Justice Department yet to appeal ruling allowing silencers, some guns",
    "Ouachita Parish man arrested for selling drugs to kids",
]

if not meme_judge.available():
    print(f"model {meme_judge.JUDGE_MODEL} not available at {meme_judge.OLLAMA_URL}")
    sys.exit(1)

print(f"model: {meme_judge.JUDGE_MODEL}\n")
results = {}
timings = []

for label, group in (("HIGH", SHOULD_BE_HIGH), ("LOW", SHOULD_BE_LOW)):
    print(f"--- should score {label} ---")
    scores = []
    for headline in group:
        t0 = time.time()
        out = meme_judge.judge_one(headline)
        elapsed = time.time() - t0
        timings.append(elapsed)
        if out is None:
            print(f"   FAILED  {headline[:58]}")
            continue
        score, why = out
        scores.append(score)
        print(f"   {score:>4.1f}  {elapsed:>5.2f}s  {headline[:52]:<52} {why[:28]}")
    results[label] = scores
    print()

hi, lo = results.get("HIGH", []), results.get("LOW", [])
if hi and lo:
    correct = sum(1 for h in hi if h >= 6) + sum(1 for x in lo if x < 6)
    print(f"mean HIGH {statistics.mean(hi):.2f}   mean LOW {statistics.mean(lo):.2f}   "
          f"separation {statistics.mean(hi) - statistics.mean(lo):+.2f}")
    print(f"correct at threshold 6: {correct}/{len(hi) + len(lo)}")
    print(f"latency: median {statistics.median(timings):.2f}s  max {max(timings):.2f}s  "
          f"total {sum(timings):.1f}s for {len(timings)} headlines")
