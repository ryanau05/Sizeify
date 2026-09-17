"""Effectful orchestration: loading rows, calling the pure domain, side effects.

The layer between ``repositories`` (which own the session) and ``domain``
(which owns the model and touches no I/O). Route handlers stay thin by
delegating here — a handler that assembled a domain input itself would have
to be copied into every other caller of the same flow.

Phase 1 ships ``fit_profile``. The share-sheet hot path (``recommend``), URL
resolution, and push dispatch land in Phase 6 (PRD §9.2, FILE_STRUCTURE.md).
"""
