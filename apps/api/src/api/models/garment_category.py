"""``garment_category`` entity — PRD §8.

``id`` is a TEXT slug (e.g. ``'mens_button_down_shirt'``) rather than a UUID
because categories are a small, code-referenced set (PRD §5.1, v1 = one
category) and a stable slug makes joins / seeds / dimension-weight wiring
readable. Seeded in TKT-P1-02.
"""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.models import Base


class GarmentCategory(Base):
    __tablename__ = "garment_category"

    id: Mapped[str] = mapped_column(sa.Text(), primary_key=True)
    display_name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    measurement_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dimension_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
