"""Versioned identifiers for the prompts that produced a stored artifact.

Every ``recommendation`` row records the prompt version behind its fit
signals (CLAUDE.md domain conventions), and every ``fit_signal`` extracted by
the LLM will reference one too. The column is what makes extraction drift
detectable: when recommendation correctness moves, the first question is
whether the prompt changed underneath it, and that is only answerable if the
version travelled with the row.

Versioning rule (CLAUDE.md workflow rules): prompts are tagged templates in
version control. Changing a prompt means adding a new version here and a new
template file — never editing an existing one in place. Old rows keep
pointing at the old version, and comparing outcome rates across versions is
the point.

Phase 1 has no LLM in the loop: recommendations are assembled by the
deterministic engine in ``api.domain.recommendation``. They still need a
value, and it needs to be honest about its provenance — hence a sentinel that
names the mechanism rather than a fake ``v1`` that would later be
indistinguishable from a real prompt version.
"""

from typing import Final

#: Provenance for artifacts produced without an LLM — the Phase 1
#: deterministic matching engine (TKT-P1-16). Real prompt versions
#: (``fit_extraction/v1`` and successors) arrive in Phase 3 and must not
#: reuse this value.
MANUAL_PROMPT_VERSION: Final = "manual-v0"
