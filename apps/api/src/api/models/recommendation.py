"""``recommendation`` entity — PRD §8 with the ``prompt_version`` invariant.

``prompt_version`` is non-nullable from day one (CLAUDE.md) even though no
LLM prompt is in the loop yet — Phase 1 writes the ``'manual-v0'`` sentinel
(TKT-P1-16). Requiring the column up front avoids a backfill migration once
real LLM versions land in Phase 3.

``reference_garment_ids`` is a UUID array (no FK enforcement) so historic
recommendations can keep citing soft-deleted garments and survive user-driven
closet cleanup without losing their audit trail.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.models import Base


class Recommendation(Base):
    __tablename__ = "recommendation"
    __table_args__ = (
        # PRD §8.2: outcome-tracking views read a user's recent recommendations
        # in reverse-chronological order. DESC index serves the sort without a
        # filesort.
        sa.Index(
            "ix_recommendation_user_id_created_at",
            "user_id",
            sa.text("created_at DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    # RESTRICT: brand_product is shared catalog state; deleting one out from
    # under a recommendation row would orphan the row's "why" trail.
    brand_product_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("brand_product.id", ondelete="RESTRICT"),
        nullable=False,
    )
    recommended_size: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    # 0.000–1.000.
    confidence: Mapped[Decimal] = mapped_column(sa.Numeric(4, 3), nullable=False)
    fit_notes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reference_garment_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(sa.Uuid()), nullable=False, server_default=sa.text("'{}'::uuid[]")
    )
    use_case_assumed: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    # 'pending' | 'correct' | 'incorrect' | 'unconfirmed' (PRD §5.5).
    outcome: Mapped[str] = mapped_column(
        sa.Text(), nullable=False, server_default=sa.text("'pending'")
    )
    outcome_confirmed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    prompt_version: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
