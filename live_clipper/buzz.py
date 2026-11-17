"""Buzzword triggers, the same rule as Tripwire: a phrase counts only when the audio rises near it.

The phrase list is Tripwire's ``buzzwords.yml``. Transcription uses the OpenShorts Parakeet
backend, the same one Tripwire uses. Pure helpers below; ``transcribe_words`` needs the models.
"""
import bisect
import os
import re
import statistics
from pathlib import Path

import yaml

from . import config

WINDOW = 60      # seconds of history in the rolling median
MIN_BASE = 10    # no rise is judged before this much history exists


def norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("-", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", s)).strip()


def load_rules(creator: str, path=None):
    """(config, tiers, rules) where rules are (spoken form, phrase, tier)."""
    cfg = yaml.safe_load(open(path or config.BUZZWORDS_FILE, encoding="utf-8"))
    tiers = {int(k): v for k, v in cfg["tiers"].items()}
    extra = (cfg.get("creators") or {}).get(creator.lower(), [])
    rules = []
    for tier, t in list(tiers.items()) + [(2, {"phrases": extra})]:
        for ph in t["phrases"]:
            for form in {ph, *cfg.get("variants", {}).get(ph, [])}:
                rules.append((norm(form), norm(ph), tier))
    return cfg, tiers, rules


def find_hits(words, rules) -> list:
    """Each phrase occurrence as {phrase, tier, t}, t = start of its first word."""
    text, offs = "", []
    for w in words:
        offs.append(len(text) + (1 if text else 0))
        text += (" " if text else "") + (norm(w["w"]) or "_")
    hits = []
    for form, phrase, tier in rules:
        for m in re.finditer(r"(?<!\S)" + re.escape(form) + r"(?!\S)", text):
            i = bisect.bisect_right(offs, m.start()) - 1
            hits.append({"phrase": phrase, "tier": tier, "t": words[i]["start"]})
    return hits


def rise_per_second(levels) -> list:
    """dB above the median of the previous minute, per second (0 until there is history)."""
    db = [d for _, d in sorted(levels)]
    rise = []
    for i, d in enumerate(db):
        base = db[max(0, i - WINDOW):i]
        rise.append(d - statistics.median(base) if len(base) >= MIN_BASE else 0.0)
    return rise


def events(hits, rise, cfg, tiers) -> list:
    """(chunk_seconds, weight) for each hit whose audio rises enough within the match window."""
    out, win = [], cfg["match_window_s"]
    for h in hits:
        lo, hi = max(0, int(h["t"] - win)), min(len(rise), int(h["t"] + win) + 1)
        if lo >= hi:
            continue
        sec = max(range(lo, hi), key=lambda s: rise[s])
        if rise[sec] >= tiers[h["tier"]]["min_rise_db"]:
            out.append((float(sec), rise[sec] + tiers[h["tier"]]["weight"]))
    return out


def transcribe_words(path) -> list:
    """Blocking. Word-level transcript [{w, start, end}] of a media file, via the OpenShorts Parakeet backend."""
    import sys
    sys.path.insert(0, str(config.REPO_ROOT))
    import transcribe_backends as T
    wav = T._extract_wav(str(path))
    try:
        t = T._parakeet_transcript(list(T._get_parakeet_model().recognize(wav)))
    finally:
        os.remove(wav)
    return [{"w": w["word"].strip(), "start": round(w["start"], 2), "end": round(w["end"], 2)}
            for s in t["segments"] for w in s["words"]]
