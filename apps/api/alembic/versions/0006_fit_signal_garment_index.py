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

**Before adding an index to a table with production data in it**, that is
no longer good enough: ``CREATE INDEX`` takes a SHARE lock and blocks
writes for the whole build. Use::

    with op.get_context().autocommit_block():
        op.create_index(..., postgresql_concurrently=True)

``CONCURRENTLY`` cannot run inside a transaction, which is what the
autocommit block is for. Two consequences worth knowing before reaching
for it: the build takes roughly twice as long and can fail part-way,
leaving an INVALID index that must be dropped and retried (it is not
retried automatically), and the migration is then non-transactional, so a
failure half-way through does not roll back the statements before it.
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
