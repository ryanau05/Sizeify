"""``RefreshToken`` entity — TKT-P1-06.

One row per issued refresh JWT. ``token_hash`` is SHA-256 of the JWS
string (hex-encoded), not the raw token — a DB compromise must not yield
usable tokens (PRD §11). The JWT itself is self-validating; the row
exists to track rotation state so reuse can be detected.

Lifecycle:

* Issued: row inserted with ``revoked_at = NULL``.
* Rotated: row's ``revoked_at`` set to the rotation timestamp; a new row
  is inserted for the replacement token.
* Reused: presenting a token whose row has ``revoked_at NOT NULL`` is
  the replay signal — TKT-P1-06's ``rotate`` revokes the user's entire
  chain and raises.
* Cascade: ``ON DELETE CASCADE`` on ``user_id`` means TKT-P1-18's
  ``DELETE /me`` wipes refresh tokens atomically with the user row.
"""

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from api.models import Base


class RefreshToken(Base):
    __tablename__ = "refresh_token"
    __table_args__ = (
        # The "revoke all for user" query — TKT-P1-06's reuse-detection
        # and TKT-P1-18's account delete both hit this path.
        sa.Index("ix_refresh_token_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid4)
    token_hash: Mapped[str] = mapped_column(sa.Text(), nullable=False, unique=True)
    user_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    issued_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
