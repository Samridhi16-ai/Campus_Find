"""Matching engine: suggests likely lost<->found pairs.

Score (0-100) = weighted mix of text similarity, location similarity,
category match and date proximity. Pure standard library (difflib).
"""
import re
from datetime import date
from difflib import SequenceMatcher

STOPWORDS = {"a", "an", "the", "my", "of", "in", "on", "at", "with", "and",
             "is", "it", "to", "near", "i", "was", "has", "have", "lost", "found"}


def _tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _date_score(d1: str, d2: str) -> float:
    try:
        gap = abs((date.fromisoformat(d1) - date.fromisoformat(d2)).days)
    except (TypeError, ValueError):
        return 0.5
    if gap <= 1:
        return 1.0
    if gap <= 3:
        return 0.8
    if gap <= 7:
        return 0.5
    if gap <= 14:
        return 0.2
    return 0.0


def score_pair(a: dict, b: dict) -> float:
    """Score two listings (dicts with name, description, location, category, date)."""
    name = max(_ratio(a["name"], b["name"]),
               _jaccard(_tokens(a["name"]), _tokens(b["name"])))
    desc = _jaccard(_tokens(a["description"]), _tokens(b["description"]))
    loc = max(_ratio(a["location"], b["location"]),
              _jaccard(_tokens(a["location"]), _tokens(b["location"])))
    cat = 1.0 if a["category"] == b["category"] else 0.0
    when = _date_score(a["date"], b["date"])

    total = 0.35 * name + 0.25 * desc + 0.15 * loc + 0.15 * cat + 0.10 * when
    return round(total * 100, 1)


def find_matches(target: dict, candidates: list, min_score: float = 35.0, limit: int = 5):
    """Rank opposite-type, still-open candidates against `target`."""
    results = []
    for c in candidates:
        if c["id"] == target["id"] or c["type"] == target["type"]:
            continue
        if c["status"] != "active":
            continue
        s = score_pair(target, c)
        if s >= min_score:
            results.append({"listing": c, "score": s})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]
