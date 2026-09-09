"""refresh_token table — TKT-P1-06

Revision ID: 0002_refresh_tokens
Revises: 0001_core_schema
Create Date: 2026-05-26

One row per issued refresh JWT. ``token_hash`` stores SHA-256 of the JWS
string so a DB compromise doesn't yield usable tokens. Rotation marks
``revoked_at``; presenting a row that's already revoked is the replay
signal handled in ``api.auth.jwt.rotate``.

``user_id`` cascade-deletes with the user row so TKT-P1-18's
``DELETE /me`` is a one-statement cleanup.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_refresh_tokens"
down_revision: str | Sequence[str] | None = "0001_core_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "refresh_token",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_refresh_token_user_id",
        "refresh_token",
        ["user_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_refresh_token_user_id", table_name="refresh_token")
    op.drop_table("refresh_token")
