"""``brand_product`` entity — PRD §8.

Cache of scraped product pages and their size charts. ``product_url`` is the
canonical resolved URL (PRD §7.2) and is the cache key on the share-sheet
hot path — uniqueness gives us an implicit index for free.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.models import Base


class BrandProduct(Base):
    __tablename__ = "brand_product"
    __table_args__ = (
        # PRD §8.2: brand + category browse queries (e.g. admin views).
        sa.Index("ix_brand_product_brand_category_id", "brand", "category_id"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid4)
    brand: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    product_name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    product_url: Mapped[str] = mapped_column(sa.Text(), nullable=False, unique=True)
    category_id: Mapped[str] = mapped_column(
        sa.Text(),
        sa.ForeignKey("garment_category.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fabric_composition: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    # 'none' | 'slight' | 'moderate' | 'high' (PRD §6.3).
    stretch_level: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    # ``{ "S": {chest_cm: 50, ...}, "M": {...} }`` (PRD §8). cm-only.
    size_chart: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    scraped_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    scraper_version: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
