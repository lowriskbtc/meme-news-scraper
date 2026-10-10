#!/usr/bin/env python3
"""LLM judgment of meme / virality potential for a headline.

Why this exists: the keyword lists in meme_scraper.py were transcribed from headlines
that had already run, so they only recognise reruns -- 22 of 25 live odd-news headlines
score 0.0, including "Blindfolded man assembles Mr. Potato Head in 7.92 seconds". And
pre-viral sources (Reddit /rising) carry no votes to read, so rank-based scoring cannot
apply to them either. Something has to judge the text itself.

Design notes:
  * Local Ollama is the default backend -- no API key, no rate limit, no per-call cost,
    and the volume here (<200 new items/day) is nowhere near needing anything bigger.
  * Every call is wrapped so a down/slow model degrades to None and the caller falls
    back to keyword scoring. An unattended daemon must never block on this.
  * Judgments are cached by item id, so a restart or a re-scan costs nothing.
"""
import json
import re
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "llama3.2:3b")
# A cold load on this box took 92s -- the machine runs at ~0.2GB available RAM and
# 39.5GB commit, so loading weights means paging, not disk. The timeout has to clear
# a cold start or the first call of every scan fails.
JUDGE_TIMEOUT = float(os.getenv("JUDGE_TIMEOUT", "120"))

# Ollama unloads an idle model after 5 minutes by default, which is SHORTER than the
# 6-minute scan interval -- so every single scan would pay that cold load. Holding the
# model resident between scans trades ~2GB of RAM for avoiding that churn. Set to "0"
# to unload immediately after each call if memory is tighter than latency matters.
JUDGE_KEEP_ALIVE = os.getenv("JUDGE_KEEP_ALIVE", "20m")
JUDGE_ENABLED = os.getenv("JUDGE_ENABLED", "1") not in ("0", "false", "False", "")

CACHE_FILE = Path(__file__).parent / "data" / "judgments.json"

# The first version of this prompt described the target in the abstract and the 3B model
# scored 8.0 on four sports/marketing headlines that merely contained the word "bizarre"
# -- reproducing the exact failure of the regex lists it was meant to replace. The fix is
# to attack that directly: judge the EVENT, never the vocabulary. Examples below are
# deliberately NOT drawn from judge_eval.py, so the benchmark stays an honest test.
SYSTEM_PROMPT = (
    "You rate how likely people are to share, screenshot, or joke about a news story.\n"
    "\n"
    "Judge the EVENT DESCRIBED, never the words used. Writers call ordinary sports "
    "plays, business moves and celebrity feuds 'bizarre', 'weird' or 'wild' as a figure "
    "of speech. That word tells you nothing. Ask instead: did something genuinely absurd "
    "physically happen in the real world?\n"
    "\n"
    "Score 8-10: a real event that sounds invented. An animal doing a human activity, a "
    "person in a ridiculous predicament, officials solemnly handling something silly.\n"
    "  'Emu escapes farm, spotted riding escalator at shopping mall' -> 9\n"
    "  'Town council spends two hours debating the mayor's goose' -> 8\n"
    "\n"
    "Score 0-3: ordinary news. Sports games and commentary, company or product "
    "announcements, marketing stunts, elections and legislation, court rulings, crime "
    "reports, business results, entertainment releases and video games. These score low "
    "EVEN IF the headline calls them bizarre, weird, wild, strange or shocking.\n"
    "  'Quarterback gives bizarre excuse after ugly loss' -> 1 (sports commentary)\n"
    "  'Chain turns its logo into a single-serving pizza' -> 1 (marketing)\n"
    "  'Studio launches Bizarre Adventure game sequel' -> 0 (product release)\n"
    "\n"
    "Score 0-2 for real human suffering -- death, injury, illness, disaster, abuse -- "
    "no matter how strange the circumstances.\n"
    "\n"
    'Reply with only JSON: {"score": <0-10 integer>, "why": "<6 words max>"}'
)


# Deterministic suppression. The model was told to score real human suffering 0-2 and
# still rated "Colombia declares economic emergency after deadly earthquake" an 8.0. A
# probabilistic judge should not be the only thing between a fatal disaster and a meme
# feed, so these are enforced in code where they cannot be talked out of it.
# Bare disaster words collide with common idiom -- an early version killed "Town declares
# war on aggressive turkey", "Photographer shooting wildlife documentary" and "Drought of
# common sense", all of which are exactly the material we want. Terms that carry a strong
# figurative sense are therefore required to appear with disambiguating context.
HARD_SUPPRESS = re.compile(
    r"\b("
    r"kill(ed|ing)?|dead|death(s)?|dies|died|fatal(ity|ities)?|murder(ed)?|homicide"
    r"|stabb(ed|ing)|massacre|manslaughter"
    r"|earthquake|tsunami|hurricane|wildfire|landslide|famine|flooding"
    r"|derail(ed|ment)?|collision|wreckage"
    r"|suicide|overdose|abuse|rape|trafficking|kidnap(ped|ping)?"
    r"|airstrike|bombing|shelling|hostage|genocide|refugee(s)?"
    r"|outbreak|epidemic|pandemic|hospitalized|critical condition"
    r"|missing child|amber alert|funeral|obituary"
    r")\b"
    # Context-qualified: these need a companion word to count as real-world harm.
    r"|\b(mass|school|fatal|drive.by) shooting\b|\bshooting victim\b|\bshot dead\b"
    r"|\bwar (crimes?|zone|dead)\b|\bcivil war\b|\bat war\b|\bwarplane\b"
    r"|\b(car|plane|train|bus|helicopter) crash\b|\bcrashed into\b"
    r"|\bassault(ed)? (a|the|his|her|two|three|\d)\b|\bsexual assault\b",
    re.I,
)


def suppressed(text: str) -> Optional[str]:
    """Return the matched term if this story must never be treated as meme material."""
    hit = HARD_SUPPRESS.search(text or "")
    return hit.group(0) if hit else None


def _load_cache() -> Dict[str, Dict]:
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("judgments", {})
        except Exception as e:
            logger.warning(f"judge cache unreadable: {e}")
    return {}


def _save_cache(cache: Dict[str, Dict]):
    # Keep the most recent 5000; dicts preserve insertion order.
    trimmed = dict(list(cache.items())[-5000:])
    CACHE_FILE.parent.mkdir(exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"judgments": trimmed}, f, indent=2)


def available() -> bool:
    """Is a usable Ollama server up, with the configured model present?"""
    if not JUDGE_ENABLED:
        return False
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if r.status_code != 200:
            return False
        names = {m.get("name", "") for m in r.json().get("models", [])}
        # tags come back like "llama3.2:3b"; accept a bare-name match too
        return any(n == JUDGE_MODEL or n.split(":")[0] == JUDGE_MODEL.split(":")[0]
                   for n in names)
    except Exception:
        return False


def judge_one(title: str, summary: str = "") -> Optional[Tuple[float, str]]:
    """Return (score 0-10, reason) or None if the model could not be reached."""
    if not JUDGE_ENABLED:
        return None
    text = title if not summary else f"{title}\n\n{summary[:300]}"

    blocked = suppressed(text)
    if blocked:
        # Cheaper than an inference call, and not overridable by the model.
        return 0.0, f"suppressed: {blocked}"

    payload = {
        "model": JUDGE_MODEL,
        "prompt": f"Headline: {text}",
        "system": SYSTEM_PROMPT,
        "stream": False,
        "format": "json",          # constrains output, keeps it parseable and short
        "keep_alive": JUDGE_KEEP_ALIVE,
        "options": {"temperature": 0.0, "num_predict": 40},
    }
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=JUDGE_TIMEOUT)
        r.raise_for_status()
        raw = r.json().get("response", "").strip()
        parsed = json.loads(raw)
        score = float(parsed.get("score", 0))
        why = str(parsed.get("why", ""))[:60]
        return max(0.0, min(10.0, score)), why
    except Exception as e:
        logger.warning(f"judge failed ({type(e).__name__}): {str(e)[:80]}")
        return None


def judge_items(items) -> int:
    """Attach 'judge_score'/'judge_why' to items in place. Returns how many were judged.

    Cached by item id, so only genuinely new stories cost inference time.
    """
    if not JUDGE_ENABLED:
        return 0
    cache = _load_cache()
    judged = 0
    dirty = False

    for item in items:
        key = item.get("id", "")
        hit = cache.get(key)
        if hit is None:
            result = judge_one(item.get("title", ""), item.get("summary", ""))
            if result is None:
                continue  # model down -- leave the item on keyword scoring
            hit = {"score": result[0], "why": result[1]}
            cache[key] = hit
            dirty = True
            judged += 1
        item["judge_score"] = hit["score"]
        item["judge_why"] = hit["why"]

    if dirty:
        _save_cache(cache)
    return judged


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(f"model={JUDGE_MODEL} available={available()}")
    for headline in sys.argv[1:]:
        t0 = time.time()
        print(f"  {judge_one(headline)}  ({time.time()-t0:.2f}s)  {headline[:60]}")
