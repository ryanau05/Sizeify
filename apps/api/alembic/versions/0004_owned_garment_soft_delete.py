"""owned_garment soft delete

Revision ID: 0004_owned_garment_soft_delete
Revises: 0003_user_credentials
Create Date: 2026-09-09

Adds ``owned_garment.deleted_at`` for TKT-P1-09's ``DELETE
/closet/garments/{id}``.

Why soft delete
---------------
``recommendation.reference_garment_ids`` points at the closet rows that
justified a recommendation ("1cm looser than your favorite Uniqlo
Oxford", PRD §5.4). Hard-deleting a garment would leave every historic
recommendation citing a row that no longer exists, so the reasoning the
user was shown becomes unreconstructible — and PRD §12.2 wants to
correlate recommendation correctness against the closet that produced
it, months later.

Nullable timestamp rather than a boolean: "when" is strictly more
information than "whether", and the §11 export benefits from it.

Index
-----
Every closet read is "this user's *live* garments", so the partial index
carries the ``deleted_at IS NULL`` predicate rather than indexing tombstones
nobody queries. It supersedes nothing — the plain ``(user_id, category_id)``
index from 0001 still serves the matching engine's per-category read.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_owned_garment_soft_delete"
down_revision: str | Sequence[str] | None = "0003_user_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "owned_garment",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_owned_garment_user_id_active",
        "owned_garment",
        ["user_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_owned_garment_user_id_active", table_name="owned_garment")
    op.drop_column("owned_garment", "deleted_at")
