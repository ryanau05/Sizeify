"""LLM prompt metadata shared across the API.

Phase 1 only needs the version constants in ``versions`` — the extraction
client, prompt templates, and schema land in Phase 3 (PRD §6.1).

Note on location: ``docs/FILE_STRUCTURE.md`` sketches the LLM extraction
service as a top-level ``llm`` package beside ``src/``, since it is one of
the four independently deployable components (CLAUDE.md architecture).
This module is deliberately *not* that service — it holds identifiers the
API itself writes to ``recommendation.prompt_version``, so it lives with the
API and is importable today without a packaging change. When the extraction
service lands, it should import these constants rather than redeclare them:
a prompt version that means two different things in two packages is exactly
the drift the column exists to detect.
"""
