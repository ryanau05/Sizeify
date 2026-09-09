"""``user`` entity — PRD §8.

The table name matches PRD §8 verbatim (singular ``user``). ``user`` is a
reserved word in PostgreSQL; SQLAlchemy auto-quotes it in DDL and FK
references, so reads and writes work without manual quoting at call sites.
"""

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from api.models import Base


class User(Base):
    __tablename__ = "user"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(sa.Text(), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    # 'cm' | 'in'. Display unit only — stored measurements are always cm
    # (CLAUDE.md domain conventions).
    preferred_units: Mapped[str] = mapped_column(
        sa.Text(), nullable=False, server_default=sa.text("'cm'")
    )
    # 'slim' | 'regular' | 'relaxed'. Captured in onboarding (PRD §10.1 step 3),
    # nullable so signup can land before onboarding completes.
    stated_fit_preference: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    device_push_token: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
