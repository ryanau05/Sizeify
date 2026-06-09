"""``owned_garment`` entity — PRD §8.

``measurements`` is a JSONB blob keyed by dimension (chest_cm, body_length_cm,
…). Shape is validated against ``garment_category.measurement_schema``, not
the DB — categories own their schemas. Measurements are always stored in cm
(CLAUDE.md domain conventions).
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.models import Base


class OwnedGarment(Base):
    __tablename__ = "owned_garment"
    __table_args__ = (
        # PRD §8.2: fit-profile construction reads one user's closet within a
        # category. Composite index covers the predicate exactly.
        sa.Index("ix_owned_garment_user_id_category_id", "user_id", "category_id"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid4)
    # User delete cascades the whole closet — see TKT-P1-18.
    user_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    # RESTRICT: categories are a stable, code-referenced set; deleting one out
    # from under owned rows would silently invalidate measurement schemas.
    category_id: Mapped[str] = mapped_column(
        sa.Text(),
        sa.ForeignKey("garment_category.id", ondelete="RESTRICT"),
        nullable=False,
    )
    brand: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    product_name: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    size_label: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    measurements: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fabric_composition: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    # 'none' | 'slight' | 'moderate' | 'high' (PRD §6.3).
    stretch_level: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    # 'love' | 'like' | 'tolerable' | 'dislike' (PRD §5.1).
    overall_rating: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    use_cases: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
