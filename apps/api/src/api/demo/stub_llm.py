"""DEMO stub for the Phase 3 LLM extraction service.

Real extraction (PRD §6.1) sends free-text feedback + category schema to the
Anthropic API and returns structured fit signals. For the demo we pattern-match
a few canned phrases so onboarding shows the "type how it fits -> structured
chips" moment without live API calls, cost, or latency variance.

Output shape matches the real ExtractionResult so the swap is a drop-in. Every
signal carries source='nlp_extracted' and the raw excerpt (CLAUDE.md requires
both on every fit_signal row).

Approach: split the feedback into clauses, then read one dimension + one
verdict out of each clause. Clause splitting is what lets a single sentence
carry two opposing verdicts ("perfect chest but the collar is tight"), which a
flat keyword table cannot express. THROWAWAY — not a model, not the Phase 3
design.
"""

from __future__ import annotations

import re
from typing import Any

# Clause boundaries. Contrastive conjunctions first — "but"/"though" are what
# separate a compliment from a complaint in the phrases a presenter types.
_CLAUSE_SPLIT = re.compile(
    r"\s*(?:,|;|\.|\bbut\b|\bthough\b|\balthough\b|\bhowever\b|\band\b)\s*",
    re.IGNORECASE,
)

# Dimension synonyms -> the demo fixture's shorthand dimension name. The
# router maps these onto canonical names via ``_normalize.canonical_dim``,
# exactly as the seed does.
_DIMENSIONS: list[tuple[tuple[str, ...], str]] = [
    (("collar", "neck"), "neck"),
    (("chest", "torso", "body"), "chest"),
    (("shoulder", "shoulders"), "shoulder"),
    (("sleeve", "sleeves", "arm", "arms", "cuff"), "sleeve"),
    (("length", "hem", "untucked", "tucked"), "body_length"),
]

# Verdict keywords -> base verdict. Order matters: the first hit in a clause
# wins, so "perfect" must outrank the looser adjectives.
_VERDICTS: list[tuple[tuple[str, ...], str]] = [
    (("perfect", "great", "love", "spot on", "just right", "ideal", "nails"), "preferred"),
    (("tight", "snug", "constricting", "digs", "pulls"), "slightly_tight"),
    (("loose", "roomy", "baggy", "billowy", "boxy", "swimming"), "slightly_loose"),
    (("short", "cropped"), "slightly_short"),
    (("long",), "slightly_loose"),
]

# Intensifiers promote a "slightly_*" verdict to its extreme form.
_INTENSIFIERS = ("too ", "way ", "really ", "very ", "far ", "much ", "unwearably ")

# Hedges keep a verdict at "slightly_*" even next to an intensifier-ish word.
_HEDGES = ("a little", "a bit", "slightly", "a touch", "somewhat", "marginally")

# The length axis is the only place the _SHORT analogues are meaningful
# (PRD §6.1 / Verdict docstring) — on a circumference they'd be nonsense.
_LENGTH_DIMENSIONS = frozenset({"sleeve", "body_length"})

_EXTREME = {
    "slightly_tight": "too_tight",
    "slightly_loose": "too_loose",
    "slightly_short": "too_short",
}


def _find_dimension(clause: str) -> str | None:
    for keywords, dimension in _DIMENSIONS:
        if any(k in clause for k in keywords):
            return dimension
    return None


def _find_verdict(clause: str, dimension: str) -> str | None:
    for keywords, verdict in _VERDICTS:
        hit = next((k for k in keywords if k in clause), None)
        if hit is None:
            continue
        # "short" on a circumference dimension isn't a fit verdict we model.
        if verdict in ("slightly_short",) and dimension not in _LENGTH_DIMENSIONS:
            continue
        if verdict == "preferred":
            return verdict
        hedged = any(h in clause for h in _HEDGES)
        intensified = any(i + hit in clause for i in _INTENSIFIERS)
        if intensified and not hedged:
            return _EXTREME.get(verdict, verdict)
        return verdict
    return None


def extract(feedback: str, category_schema: dict[str, Any]) -> dict[str, Any]:
    """free text -> {signals: [...], unparsed_notes: str}.

    Deterministic by construction: same input, same signals, every run. Any
    clause we can't read lands in ``unparsed_notes`` rather than being dropped
    silently — mirrors the real service's fallback so the demo doesn't imply
    a confidence the extractor doesn't have.
    """
    signals: list[dict[str, Any]] = []
    unparsed: list[str] = []
    seen: set[str] = set()

    for raw_clause in _CLAUSE_SPLIT.split(feedback):
        clause = raw_clause.strip()
        if not clause:
            continue
        text = clause.lower()
        dimension = _find_dimension(text)
        verdict = _find_verdict(text, dimension) if dimension else None
        if dimension is None or verdict is None:
            unparsed.append(clause)
            continue
        # One signal per dimension: the first clause to mention it wins, so a
        # trailing restatement can't quietly overwrite the user's first read.
        if dimension in seen:
            continue
        seen.add(dimension)
        signals.append(
            {
                "dimension": dimension,
                "verdict": verdict,
                "source": "nlp_extracted",
                # The excerpt is the clause, not the whole blob — CLAUDE.md
                # wants the raw text that produced *this* signal for prompt
                # -quality debugging.
                "raw_feedback_text": clause,
                "prompt_version": "demo-v0",
            }
        )

    return {"signals": signals, "unparsed_notes": " / ".join(unparsed)}
