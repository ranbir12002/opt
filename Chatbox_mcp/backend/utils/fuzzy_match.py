# backend/utils/fuzzy_match.py
"""
Centralized fuzzy matching utilities for Simpro entity resolution.

Provides name-matching logic used across all agents — staff, contractors,
jobs, cost centres, sections, etc.

Techniques used (in scoring order):
  1. Exact match                  — 100
  2. Whole-word match             — 90
  3. Substring containment        — 80-89  (with word-coverage bonus)
  4. Token set ratio              — 78     (rapidfuzz: handles word reorder + partial + typo)
  5. All query words exact        — 70     (every word appears verbatim in name)
  6. All query words near-match   — 70     (Jaro-Winkler ≥0.85 per word)
  7. Trigram Jaccard              — 65-69  (char-level, handles cross-word-boundary typos)
  8. Partial word match           — 40-60  (fraction of words matched)
  9. Per-word Jaro-Winkler        — 75/48+ (name-optimized edit distance fallback)

Libraries:
  - rapidfuzz (C++ backed): JaroWinkler, token_set_ratio, fuzz.ratio
    Replaces difflib.SequenceMatcher everywhere — same interface, better
    algorithms specifically designed for name matching.

Usage:
    from utils.fuzzy_match import fuzzy_match_name, fuzzy_match_entities, deduplicate_matches
"""

from __future__ import annotations
import re
import logging
from typing import Any, Dict, List, Optional

from rapidfuzz import fuzz as _fuzz
from rapidfuzz.distance import JaroWinkler as _JaroWinkler

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trigram helpers (no external library needed — pure Python)
# ---------------------------------------------------------------------------

def _trigrams(s: str) -> set:
    """
    Generate character trigrams from a string.
    Uses word-boundary padding (_) so start/end characters get fair coverage.
    e.g. "john" → {_jo, joh, ohn, hn_}
    """
    padded = f"_{s}_"
    return {padded[i:i+3] for i in range(len(padded) - 2)}


def _trigram_jaccard(a: str, b: str) -> float:
    """
    Jaccard similarity on character trigrams: |A ∩ B| / |A ∪ B|.

    Production-proven technique (PostgreSQL pg_trgm).
    Handles cross-word-boundary typos and partial names that edit-distance
    misses because it operates over the full string rather than word-by-word.

    Returns a float in [0, 1].
    """
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta and not tb:
        return 1.0
    intersection = len(ta & tb)
    union = len(ta | tb)
    return intersection / union if union else 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fuzzy_match_name(query: str, candidates: List[str], threshold: int = 70) -> Optional[str]:
    """
    Fuzzy match a query string against a list of candidate strings.

    Tries substring matching first (fast, precise), then falls back
    to rapidfuzz ratio (Levenshtein-based, C++ backed).

    Args:
        query: Search string
        candidates: List of candidate strings
        threshold: Minimum similarity score (0-100)

    Returns:
        Best matching candidate string, or None if nothing meets threshold
    """
    query_lower = query.lower()
    best_match = None
    best_score = 0

    for candidate in candidates:
        cand_lower = candidate.lower()

        # Substring matching (exact containment)
        if query_lower in cand_lower:
            return candidate

        # rapidfuzz ratio — Levenshtein normalized, C++ speed
        score = _fuzz.ratio(query_lower, cand_lower)
        if score > best_score and score >= threshold:
            best_score = score
            best_match = candidate

    return best_match


def fuzzy_match_entities(
    query: str,
    candidates: List[Dict[str, Any]],
    name_field: str = "Name",
    id_field: str = "ID",
    extra_name_fields: Optional[List[str]] = None,
    source: str = "",
) -> List[Dict[str, Any]]:
    """
    Fuzzy match a query against a list of entity dicts.

    Works for any Simpro entity: employees, contractors, jobs, cost centres, etc.
    Multi-signal scoring: substring → token-set → all-words → trigram → Jaro-Winkler.

    Args:
        query: Search string (e.g., "Stephen")
        candidates: List of dicts, each with at least name_field and id_field
        name_field: Key for the primary name (default "Name")
        id_field: Key for the entity ID (default "ID")
        extra_name_fields: Additional name fields to check (e.g., ["ContactName"])
        source: Label for match provenance (e.g., "list_employees")

    Returns:
        List of {id, name, score, source} sorted by score descending
    """
    query_lower = query.lower().strip()
    extra_name_fields = extra_name_fields or []
    matches = []

    for candidate in candidates:
        cid = candidate.get(id_field)
        name = (candidate.get(name_field) or "").strip()

        if not cid:
            continue

        query_words = [w for w in re.split(r'[\s\-]+', query_lower) if w]

        # Score against primary name field
        score = 0
        best_name = name
        if name:
            name_lower = name.lower()
            name_words = [w for w in re.split(r'[\s\-]+', name_lower) if w]
            score = _score_name(query_lower, query_words, name_lower, name_words)

        # Always check extra name fields — take the best score across all fields.
        # A contractor's ContactName "Nicholas Gubby" should beat a garbage
        # typo match against the company Name "AMN".
        # No penalty: contractors' real names live in ContactName — penalizing
        # secondary fields systematically disadvantages contractors vs employees.
        for field in extra_name_fields:
            extra = (candidate.get(field) or "").strip()
            if not extra:
                continue
            extra_lower = extra.lower()
            extra_words = [w for w in re.split(r'[\s\-]+', extra_lower) if w]
            extra_score = _score_name(query_lower, query_words, extra_lower, extra_words)
            if extra_score > score:
                score = extra_score
                best_name = extra

        if score > 0:
            matches.append({"id": cid, "name": best_name, "score": score, "source": source})

    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches


# ---------------------------------------------------------------------------
# Core scoring logic
# ---------------------------------------------------------------------------

def _score_name(
    query: str,
    query_words: List[str],
    name: str,
    name_words: List[str],
    penalty: int = 0,
) -> int:
    """
    Score how well a query matches a name. Returns an integer in [0, 100].

    Scoring tiers (higher = better match, strictly ordered):
        100 — exact match
         90 — query is a whole word in the name (or vice versa)
      80-89 — query is a substring of name (+ word-coverage bonus)
      75-84 — token set ratio ≥ 82 (word reorder, partial+typo multi-word)
         72 — all query words verbatim in name (every word exact)
         71 — all query words near-matched via Jaro-Winkler ≥ 0.85 per word
      65-69 — trigram Jaccard ≥ 0.25 (cross-word-boundary / run-together typos)
      40-60 — partial word match (fraction of significant words found, exact)
         62 — per-word Jaro-Winkler near-match ≥ 0.85 (best single word match)
      48-55 — per-word Jaro-Winkler loose match 0.75-0.85

    Why these techniques:
      - Jaro-Winkler (replaces SequenceMatcher): purpose-built for human names.
        Prefix-weighted (people rarely mistype the start), handles transpositions
        as single edits. Validated on US Census name corpora.
      - Token set ratio (rapidfuzz): handles "Smith John" = "John Smith",
        and "Apex Roofin" ≈ "Apex Roofing Services Pty Ltd". The current
        substring + word checks are exact; token_set_ratio adds edit-distance
        tolerance on top of partial/subset matching.
      - Trigram Jaccard (PostgreSQL pg_trgm approach): operates on the full
        string as a character sequence rather than splitting on words. Catches
        typos that cross word boundaries and run-together strings like
        "johnmcg" vs "john mcgregor" which word-level checks miss entirely.

    Args:
        penalty: Points deducted (reserved for future secondary-field weighting)
    """
    # ── Tier 1: Exact match ──────────────────────────────────────────────────
    if query == name:
        return 100 - penalty

    # ── Tier 2: Whole-word match ─────────────────────────────────────────────
    # Query is a complete word within the name, or the name is a complete
    # word within the query (handles "stephen" in "stephen andrew").
    if query in name_words or name in query_words:
        return 90 - penalty

    # ── Tier 3: Substring containment ────────────────────────────────────────
    # Full query string appears verbatim inside the name.
    # Coverage bonus (0-9) rewards names where more of their words are
    # covered by the query — so "Metal Roof" scores higher against
    # "SS - Metal Roof" (short, fully covered) than "SS - Metal Roof Flashing".
    if query in name:
        significant_words = [w for w in name_words if len(w) >= 2]
        if significant_words:
            covered = sum(1 for nw in significant_words if nw in query)
            coverage_bonus = int((covered / len(significant_words)) * 9)
            return 80 + coverage_bonus - penalty
        return 80 - penalty

    # ── Tier 4: Token set ratio ───────────────────────────────────────────────
    # rapidfuzz token_set_ratio: computes Levenshtein ratio on the intersection
    # of word tokens, making it invariant to word order and tolerant of extra
    # words in either string. Returns 0-100.
    #
    # Handles:
    #   • "Smith John" vs "John Smith" (word reorder)       → 100
    #   • "Apex Roofin" vs "Apex Roofing Services Pty Ltd"  → ~95
    #   • "mts roof plumning" vs "MTS Roof Plumbing"        → ~92
    #
    # Threshold 82 (=0.82) chosen to sit below substring (80) in terms of
    # confidence but above all-words (70) — it's more flexible than substring
    # but weaker as evidence than an exact substring match.
    tsr = _fuzz.token_set_ratio(query, name)
    if tsr >= 82:
        # Scale 82-100 → 75-84 so strong multi-word matches (TSR ~94) score
        # clearly above single-word JW fallback (score 75).
        # e.g. TSR=94 → 80, TSR=82 → 75, TSR=100 → 84
        scaled = 75 + int((tsr - 82) / 18 * 9)
        return scaled - penalty

    # ── Tier 5: All query words present (exact substring check) ──────────────
    # Every query word appears verbatim somewhere in the name string.
    # Score 72 — above all fuzzy tiers, below TSR (which adds word-order tolerance).
    if all(w in name for w in query_words):
        return 72 - penalty

    # ── Tier 6: All query words near-matched (Jaro-Winkler per word) ─────────
    # Every query word matches some name word via JW ≥ 0.85 (or exact substring).
    # Score 71 — just below tier 5 (exact words) but above all single-word tiers.
    #
    # CRITICAL: this must outscore tier 9 (single-word JW = 62) so that
    # "mts roof plumning" → "MTS Roof Plumbing" (all 3 words near-matched)
    # scores higher than "HydroFlo Plumbing Solutions" (only 1 word near-matched).
    #
    # Jaro-Winkler replaces SequenceMatcher: handles transpositions as single
    # edits and its prefix bonus prevents short words like "roof" spuriously
    # matching unrelated words. Short words (≤3 chars) require exact substring
    # — fuzzy matching them is too broad.
    def _word_matched_jw(qw: str) -> bool:
        if qw in name:
            return True
        if len(qw) <= 3:
            return False
        for nw in name_words:
            if len(nw) < 3:
                continue
            if _JaroWinkler.normalized_similarity(qw, nw) >= 0.85:
                return True
        return False

    if all(_word_matched_jw(w) for w in query_words):
        return 71 - penalty

    # ── Tier 7: Trigram Jaccard ───────────────────────────────────────────────
    # Character trigram Jaccard similarity over the full strings (not per-word).
    # This is the technique behind PostgreSQL's pg_trgm extension, proven at
    # scale in production databases.
    #
    # Why this tier exists:
    #   Word-level checks split on spaces — they fail when:
    #   • The user runs words together: "johnmcg" vs "john mcgregor"
    #   • A typo crosses a word boundary: "mcgreggor" vs "mcgregor"
    #   • The name has internal punctuation that splits differently
    #
    # Trigrams operate over the full lowercased string (spaces normalised to _)
    # so they are completely immune to word-boundary issues.
    #
    # Threshold 0.25: trigram Jaccard scores are inherently lower than ratio
    # scores because the sets grow quadratically with string length. 0.25 is
    # equivalent to "at least a quarter of all trigrams are shared" which is
    # a strong signal for names of typical length (5-20 chars).
    # Score range 65-69: below all-words (70) but above partial (60).
    tj = _trigram_jaccard(query.replace(" ", "_"), name.replace(" ", "_"))
    if tj >= 0.25:
        # Scale 0.25-1.0 → 65-69
        scaled = 65 + int((tj - 0.25) / 0.75 * 4)
        return scaled - penalty

    # ── Tier 8: Partial word match ────────────────────────────────────────────
    # Some but not all significant query words (≥3 chars) appear verbatim
    # in the name. Score scales with fraction matched.
    # "plumbing" in "mts roof plumbing" (1 of 3 words) → score 47
    significant_words = [w for w in query_words if len(w) >= 3]
    if significant_words:
        matched = sum(1 for w in significant_words if w in name)
        if matched > 0:
            fraction = matched / len(significant_words)
            # Scale: 1/1 → 60, 2/3 → 53, 1/3 → 46, 1/2 → 50
            return int(40 + 20 * fraction) - penalty

    # ── Tier 9: Per-word Jaro-Winkler fallback ───────────────────────────────
    # Last resort before returning 0. Looks for any single word pair across
    # query and name that scores high on Jaro-Winkler.
    #
    # Two sub-tiers:
    #   JW ≥ 0.85 → near-match (score 75): "alister"→"allister" (0.93)
    #   JW ≥ 0.75 → loose typo (score ~48-55): proportional to similarity
    #
    # Jaro-Winkler replaces SequenceMatcher here because:
    #   - JW on names like "stefen"→"stephen" gives 0.86 vs SM 0.857 — similar
    #     but JW's prefix bonus means "steph..."→"stephen" scores higher than
    #     SM would, which is the right behaviour (the user started typing correctly)
    #   - JW on "nick"→"rick" gives 0.75 vs SM 0.75 — same, both reject at 0.85
    #     threshold for short words (correct: these are different names)
    #
    # Short words (≤4 chars) require JW ≥ 0.88 to prevent single-character
    # swaps from scoring as near-matches ("nick"→"rick" gives JW 0.75 → blocked).
    # Single best word match across all query/name word pairs.
    # Score 62 for near-match (JW ≥ 0.85) — below all all-words tiers (71/72)
    # so a 3-word query matching all 3 words always beats a 1-word match.
    # Short words (≤4 chars) require JW ≥ 0.88 to block single-char swaps
    # like "nick"→"rick" (JW=0.75) from scoring as near-matches.
    best_jw_score = 0
    for qw in query_words:
        if len(qw) < 3:
            continue
        for nw in name_words:
            if len(nw) < 3:
                continue
            jw = _JaroWinkler.normalized_similarity(qw, nw)
            min_jw = 0.88 if len(qw) <= 4 else 0.75
            if jw >= min_jw:
                if jw >= 0.85:
                    # Near-match: 62 — below all-words tiers, above partial
                    word_score = 62 - penalty
                else:
                    # Loose typo: proportional, ~48-55
                    word_score = int(jw * 65) - penalty
                best_jw_score = max(best_jw_score, word_score)

    return best_jw_score


# ---------------------------------------------------------------------------
# Post-filter (unchanged — used for API wildcard result filtering)
# ---------------------------------------------------------------------------

def post_filter_matches(
    query: str,
    candidates: List[Dict[str, Any]],
    name_field: str = "Name",
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    """
    Post-filter API wildcard results to remove noise before clarification.

    Keeps only candidates whose name contains ALL significant query words
    as whole words. Handles address components (numbers checked as substrings,
    words checked as whole-word boundaries).

    This solves the problem where a wildcard search for "%jackson%" returns
    "Jacksonia Drive" alongside "18 Jackson Street" — "jacksonia" contains
    "jackson" as a substring but not as a whole word.

    Args:
        query: Original user search (e.g., "18 jackson street")
        candidates: List of dicts from API results
        name_field: Key containing the name to check against
        max_results: Maximum candidates to return

    Returns:
        Filtered list (subset of candidates), or original list (capped)
        if filtering removes everything.
    """
    query_words = [w.lower() for w in re.split(r'[\s\-]+', query.strip()) if w]
    if not query_words:
        return candidates[:max_results]

    numeric_tokens = [w for w in query_words if w.isdigit()]
    text_words = [w for w in query_words if not w.isdigit() and len(w) >= 3]

    if not text_words and not numeric_tokens:
        return candidates[:max_results]

    filtered = []
    for candidate in candidates:
        name = (candidate.get(name_field) or "").lower()
        if not name:
            continue

        name_words = [w.lower() for w in re.split(r'[\s\-,]+', name) if w]

        # Text words: must appear as a whole word in the candidate name
        text_ok = all(
            any(qw == nw for nw in name_words)
            for qw in text_words
        )

        # Numeric tokens: substring check (addresses like "18" in "18 Jackson St")
        num_ok = all(tok in name for tok in numeric_tokens)

        if text_ok and num_ok:
            filtered.append(candidate)

    if not filtered:
        logger.info(f"Post-filter removed all {len(candidates)} candidates for '{query}', falling back to original")
        return candidates[:max_results]

    logger.info(f"Post-filter: {len(candidates)} → {len(filtered)} candidates for '{query}'")
    return filtered[:max_results]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate_matches(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Deduplicate match results by ID, keeping the highest score for each.

    Args:
        matches: List of {id, name, score, source} dicts

    Returns:
        Deduplicated list sorted by score descending
    """
    seen: Dict[Any, Dict[str, Any]] = {}
    for m in matches:
        mid = m["id"]
        if mid not in seen or m["score"] > seen[mid]["score"]:
            seen[mid] = m
    return sorted(seen.values(), key=lambda m: m["score"], reverse=True)
