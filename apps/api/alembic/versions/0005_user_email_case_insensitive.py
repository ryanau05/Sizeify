"""unique user email, case-insensitively

Revision ID: 0005_user_email_ci
Revises: 0004_owned_garment_soft_delete
Create Date: 2026-09-10

Closes the gap between how the code looks an account up and what the
database actually enforces.

``UserRepository.get_by_email`` matches on ``lower(email)``, but the only
constraint was ``UNIQUE (email)`` — an exact-match index. Postgres
therefore accepted ``alice@example.com`` and ``Alice@example.com`` as two
distinct rows, which the signup pre-check could not prevent either: two
concurrent requests both read "no such account" and both inserted, and no
``IntegrityError`` was raised for the route's 409 handler to catch.

Once two such rows exist, ``get_by_email`` returns whichever the planner
happens to emit first, so a login for that address can be answered by
either account. The victim sees intermittent 401s on their own correct
password, and the outcome can flip after an ANALYZE or an index rebuild.

A functional unique index on ``lower(email)`` makes the constraint say
what the query means. The upgrade fails loudly if duplicates already
exist; that is deliberate — merging two accounts is a decision, not
something a migration should silently make.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_user_email_ci"
down_revision: str | Sequence[str] | None = "0004_owned_garment_soft_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                'SELECT lower(email) AS e, count(*) FROM "user" '
                "GROUP BY lower(email) HAVING count(*) > 1"
            )
        )
        .fetchall()
    )
    if duplicates:
        raise RuntimeError(
            "cannot add the case-insensitive email index: these addresses already "
            f"exist more than once (differing only in case): {[row.e for row in duplicates]}. "
            "Merge or remove the duplicate accounts, then re-run this migration."
        )

    op.create_index(
        "uq_user_email_lower",
        "user",
        [sa.text("lower(email)")],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_user_email_lower", table_name="user")
