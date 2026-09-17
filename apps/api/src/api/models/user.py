"""``user`` entity — PRD §8.

The table name matches PRD §8 verbatim (singular ``user``). ``user`` is a
reserved word in PostgreSQL; SQLAlchemy auto-quotes it in DDL and FK
references, so reads and writes work without manual quoting at call sites.

Two columns here are not in the PRD §8.1 sketch but are mandated by PRD
§11 and added in migration 0003 (TKT-P1-07):

* ``password_hash`` — argon2id PHC string (§11 "secure password storage").
  §8.1 elides it the way schema sketches usually elide credentials; there
  is no login without it.
* ``privacy_consent_accepted_at`` — the GDPR/CCPA consent timestamp
  captured at signup (§11 "consent flows from day one").
"""

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from api.models import Base


class User(Base):
    __tablename__ = "user"
    __table_args__ = (
        # Migration 0005. The plain ``unique=True`` on ``email`` below is
        # exact-match, but every lookup goes through ``lower(email)``
        # (``UserRepository.get_by_email``), so without this a pair of
        # case-variant rows could coexist and login became a coin flip
        # between them.
        sa.Index("uq_user_email_lower", sa.func.lower(sa.column("email")), unique=True),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(sa.Text(), nullable=False, unique=True)
    # Argon2id PHC string from ``api.auth.password.hash`` — algorithm,
    # parameters, salt and digest in one opaque field. Never leaves the
    # server (excluded from the §11 export, see schemas/me.py).
    password_hash: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    # GDPR/CCPA consent timestamp, captured at signup and never null: a
    # user row without recorded consent is not a state we want to be able
    # to represent.
    privacy_consent_accepted_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
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
