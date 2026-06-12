"""DEMO stub for the Phase 3 LLM extraction service.

Real extraction (PRD §6.1) sends free-text feedback + category schema to the
Anthropic API and returns structured fit signals. For the demo we pattern-match
a few canned phrases so onboarding shows the "type how it fits -> structured
chips" moment without live API calls, cost, or latency variance.

Output shape matches the real ExtractionResult so the swap is a drop-in. Every
signal carries source='nlp_extracted' and the raw excerpt (CLAUDE.md requires
both on every fit_signal row).
"""

from __future__ import annotations

from typing import Any

# Minimal keyword -> (dimension, verdict) table. DEMO ONLY — not a real model.
_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("collar", "neck", "tight"), "neck", "slightly_tight"),
    (("chest", "tight"), "chest", "slightly_tight"),
    (("chest", "roomy", "loose"), "chest", "slightly_loose"),
    (("sleeve", "short"), "sleeve", "slightly_short"),
    (("sleeve", "long"), "sleeve", "slightly_loose"),
    (("perfect", "great", "love"), "chest", "preferred"),
]


def extract(feedback: str, category_schema: dict[str, Any]) -> dict[str, Any]:
    """free text -> {signals: [...], unparsed_notes: str}.

    DEMO TODO Day 2: expand canned rules to cover the 3-4 demo phrases the
    presenter will type live. Keep deterministic. Anything unmatched lands in
    unparsed_notes (mirrors the real two-failure fallback).
    """
    text = feedback.lower()
    signals = []
    for keywords, dimension, verdict in _RULES:
        if all(k in text for k in keywords):
            signals.append(
                {
                    "dimension": dimension,
                    "verdict": verdict,
                    "source": "nlp_extracted",
                    "raw_feedback_text": feedback,
                    "prompt_version": "demo-v0",
                }
            )
    unparsed = "" if signals else feedback
    return {"signals": signals, "unparsed_notes": unparsed}
