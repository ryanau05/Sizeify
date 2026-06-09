"""``fit_signal`` entity — PRD §8 with §6.1 verdict + ``source`` invariants.

``verdict`` and ``source`` are PostgreSQL ENUM types (not TEXT as in the bare
PRD snippet) so bad rows fail at insert time — the matching engine reads them
without defensive validation. ``source`` and ``raw_feedback_text`` are
non-nullable (CLAUDE.md); user-added signals synthesize a feedback string
upstream (see TKT-P1-10) rather than nulling the column.

Verdict values include the ``slightly_short`` / ``too_short`` analogues per
PRD §6.1 for length-axis dimensions (sleeve length, body length).
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from api.models import Base

VERDICT_VALUES: tuple[str, ...] = (
    "too_tight",
    "slightly_tight",
    "preferred",
    "slightly_loose",
    "too_loose",
    "slightly_short",
    "too_short",
)

SOURCE_VALUES: tuple[str, ...] = (
    "nlp_extracted",
    "user_edited",
    "user_added",
)

# ``create_type=False``: the type is created/dropped by the Alembic migration,
# not by ``metadata.create_all()``. Tests run migrations, so this is correct.
_verdict_enum = PgEnum(*VERDICT_VALUES, name="fit_signal_verdict", create_type=False)
_source_enum = PgEnum(*SOURCE_VALUES, name="fit_signal_source", create_type=False)


class FitSignal(Base):
    __tablename__ = "fit_signal"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid4)
    owned_garment_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("owned_garment.id", ondelete="CASCADE"),
        nullable=False,
    )
    dimension: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    verdict: Mapped[str] = mapped_column(_verdict_enum, nullable=False)
    magnitude_cm: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 2), nullable=True)
    use_case: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    source: Mapped[str] = mapped_column(_source_enum, nullable=False)
    raw_feedback_text: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
