"""drop the redundant exact-match email constraint

Revision ID: 0007_drop_email_uq
Revises: 0006_fit_signal_ix
Create Date: 2026-09-29

``0001`` created ``UNIQUE (email)`` and ``0005`` added a functional unique
index on ``lower(email)``. The second subsumes the first: two rows sharing
an ``email`` necessarily share its ``lower()``, so the case-insensitive
index rejects everything the exact-match one would have, and more.

Keeping both cost two unique checks on every signup INSERT and left two
constraint names that the route's ``IntegrityError`` handler had to know
about — a set it is easy to get wrong, because the one that actually
fires depends on which index Postgres evaluates first.

Dropping the exact-match constraint leaves ``uq_user_email_lower`` as the
single authority on address uniqueness, which is also the rule the code
enforces: ``UserRepository.get_by_email`` matches on ``lower(email)``.

The downgrade re-adds it, which can fail where the upgrade succeeded — a
pair of addresses differing only in case is legal under neither, but a
database that acquired them some other way would refuse. That is correct:
re-adding a constraint the data violates should not silently pass.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_drop_email_uq"
down_revision: str | Sequence[str] | None = "0006_fit_signal_ix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "user_email_key"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(CONSTRAINT_NAME, "user", type_="unique")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_unique_constraint(CONSTRAINT_NAME, "user", ["email"])
