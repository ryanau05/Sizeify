"""Tests for the demo's stubbed LLM extraction (DEMO-08).

THROWAWAY alongside ``api.demo`` — but the demo's onboarding beat depends on
these phrases producing exactly these signals on stage, so they're pinned.
"""

from api.demo.stub_llm import extract


def _pairs(feedback: str) -> list[tuple[str, str]]:
    result = extract(feedback, {})
    return [(s["dimension"], s["verdict"]) for s in result["signals"]]


def test_contrastive_clause_yields_two_opposing_signals() -> None:
    """The DEMO-08 acceptance phrase: one sentence, two opposite verdicts."""
    assert _pairs("perfect chest but collar is tight") == [
        ("chest", "preferred"),
        ("neck", "slightly_tight"),
    ]


def test_hedged_and_intensified_variants_of_the_same_phrase() -> None:
    assert _pairs("perfect in the chest but the collar is a little tight") == [
        ("chest", "preferred"),
        ("neck", "slightly_tight"),
    ]
    # "way too" promotes to the extreme verdict; the hedge does not.
    assert _pairs("the collar is way too tight") == [("neck", "too_tight")]
    assert _pairs("the collar is a bit tight") == [("neck", "slightly_tight")]


def test_short_verdicts_only_apply_to_length_dimensions() -> None:
    """PRD §6.1: the _SHORT analogues are meaningless on a circumference."""
    assert _pairs("sleeves are too short") == [("sleeve", "too_short")]
    assert ("neck", "slightly_short") not in _pairs("the collar is short")


def test_first_mention_of_a_dimension_wins() -> None:
    signals = _pairs("chest is tight, and honestly the chest is loose")
    assert signals == [("chest", "slightly_tight")]


def test_unreadable_feedback_lands_in_unparsed_notes() -> None:
    result = extract("love it", {})
    assert result["signals"] == []
    assert result["unparsed_notes"] == "love it"


def test_every_signal_carries_source_and_raw_excerpt() -> None:
    """CLAUDE.md: source + raw feedback text are mandatory on every signal."""
    result = extract("perfect chest but collar is tight", {})
    for signal in result["signals"]:
        assert signal["source"] == "nlp_extracted"
        assert signal["raw_feedback_text"]
        assert signal["prompt_version"] == "demo-v0"
    # The excerpt is the clause that produced the signal, not the whole blob.
    assert result["signals"][1]["raw_feedback_text"] == "collar is tight"


def test_empty_feedback_produces_nothing() -> None:
    assert extract("", {}) == {"signals": [], "unparsed_notes": ""}
