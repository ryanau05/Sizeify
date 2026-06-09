"""Seed scripts.

Each module here exposes (a) a pure ``seed_*`` coroutine that takes an
``AsyncSession`` and upserts rows, and (b) a ``main()`` that wires up its
own session + transaction and calls ``asyncio.run``. The split lets tests
drive the seed against the per-test transactional fixture without needing
to manage a top-level event loop.

Seeds are idempotent — re-running upserts on the row's primary key, so
running ``uv run python -m api.seeds.<name>`` after code changes brings
the DB into sync rather than failing on uniqueness.
"""
