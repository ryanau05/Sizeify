"""Delete refresh-token rows whose tokens have already expired.

``refresh_token`` gains a row on every login and every rotation, and
nothing removed one: revocation only stamps ``revoked_at``. Left alone the
table grows with login volume forever, and every ``get_by_hash`` walks a
unique index that only gets bigger.

Run it periodically::

    uv run python -m api.maintenance.prune_refresh_tokens

Daily is ample — the rows it removes have been useless since their tokens
expired, and nothing depends on the exact moment they go. ``--dry-run``
reports the count without deleting, for a first run against real data.

What it does *not* delete is as deliberate as what it does: see
``RefreshTokenRepository.delete_expired``. Only rows past the refresh TTL
go, so a revoked-but-unexpired row — the kind a replay would present — is
kept, and the replay alarm keeps working.
"""

from __future__ import annotations

import argparse
import asyncio

from api.config import get_settings
from api.logging import configure_logging
from api.repositories.base import get_sessionmaker, transaction
from api.repositories.refresh_tokens import RefreshTokenRepository


async def prune(*, dry_run: bool = False) -> int:
    """Delete expired rows. Returns how many went (or would have)."""
    settings = get_settings()
    sessionmaker = get_sessionmaker()

    async with sessionmaker() as session:
        repository = RefreshTokenRepository(session)
        async with transaction(session):
            deleted = await repository.delete_expired(ttl_seconds=settings.refresh_token_ttl)
            if dry_run:
                # The count is only knowable by doing the delete, so do it and
                # roll back — the whole thing is inside one transaction.
                await session.rollback()
        return deleted


async def _amain(dry_run: bool) -> None:
    configure_logging()
    deleted = await prune(dry_run=dry_run)
    verb = "would delete" if dry_run else "deleted"
    print(f"refresh-token prune: {verb} {deleted} expired row(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report the count without deleting anything",
    )
    args = parser.parse_args()
    asyncio.run(_amain(args.dry_run))


if __name__ == "__main__":
    main()
