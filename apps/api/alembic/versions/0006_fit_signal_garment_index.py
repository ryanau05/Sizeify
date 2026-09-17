"""index fit_signal.owned_garment_id

Revision ID: 0006_fit_signal_ix
Revises: 0005_user_email_ci
Create Date: 2026-09-17

Postgres does not index a foreign key for you — only the *referenced*
side gets one automatically — and ``fit_signal`` was the one table in the
schema with no index at all beyond its primary key.

Two things paid for that. ``FitSignalRepository.list_for_user`` joins
``fit_signal`` to ``owned_garment`` on this column, and it sits on the
fit-profile build that PRD §9.2 budgets at 100 ms; with no index the
planner sequentially scans every user's signals on every build. And the
``ON DELETE CASCADE`` from ``owned_garment`` — which ``DELETE /me`` fans
out across the whole closet — re-scans the same table once per deleted
parent row.

The index is composite on ``(owned_garment_id, created_at)`` rather than
the bare foreign key because ``list_for_user`` also does
``ORDER BY created_at, id``; the wider index serves the sort from the
index rather than making the planner heapsort the result.

Both tables are trivially small in v1 (single user, ≤500 garments), so
the index is built non-concurrently inside the migration transaction.
The first index added after there is real production data will need
``postgresql_concurrently=True`` inside ``op.get_context().autocommit_block()``,
which cannot run in a transactional migration.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_fit_signal_ix"
down_revision: str | Sequence[str] | None = "0005_user_email_ci"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_fit_signal_owned_garment_id"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        INDEX_NAME,
        "fit_signal",
        ["owned_garment_id", "created_at"],
    )


def downgrade() -> None:
    """Downgrade schema.

    Dropping an index destroys no data, so this needs no destructive opt-in
    the way 0003-0005 do — it only makes the joins slow again.
    """
    op.drop_index(INDEX_NAME, table_name="fit_signal")
