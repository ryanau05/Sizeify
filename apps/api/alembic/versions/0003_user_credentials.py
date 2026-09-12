"""user credentials + privacy consent

Revision ID: 0003_user_credentials
Revises: 0002_refresh_tokens
Create Date: 2026-09-09

Adds the two ``user`` columns TKT-P1-07's auth endpoints need:

* ``password_hash`` — argon2id PHC string (PRD §11 "secure password
  storage (bcrypt/argon2)").
* ``privacy_consent_accepted_at`` — GDPR/CCPA consent timestamp captured
  at signup (PRD §11 "consent flows from day one").

Neither appears in the PRD §8.1 entity sketch; §8.1 lists "key fields"
and elides credentials the way schema sketches usually do. §11 makes both
mandatory, so they land as NOT NULL — a user row with no credential or no
recorded consent is not a state worth being able to represent.

Backfill
--------
Both columns are added nullable, backfilled, then tightened to NOT NULL,
so the migration is safe against a non-empty table (dev DBs seeded before
TKT-P1-07 landed):

* ``password_hash`` gets ``LOCKED_PASSWORD_HASH``, a sentinel that is not
  a valid argon2 PHC string. ``api.auth.password.verify`` returns False
  for it (InvalidHashError), so a pre-existing row is left unable to log
  in rather than silently given a guessable credential. Recovery is a
  password reset, which is the correct outcome for a row that never had a
  password.
* ``privacy_consent_accepted_at`` gets ``created_at`` — the only defensible
  approximation, and the pre-auth rows are dev fixtures, not real consent
  records.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_user_credentials"
down_revision: str | Sequence[str] | None = "0002_refresh_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Deliberately not a parseable argon2 hash: ``verify`` raises
# InvalidHashError internally and returns False, so nothing can
# authenticate against it.
LOCKED_PASSWORD_HASH = "!locked-no-password-set"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("user", sa.Column("password_hash", sa.Text(), nullable=True))
    op.add_column(
        "user",
        sa.Column("privacy_consent_accepted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        sa.text('UPDATE "user" SET password_hash = :locked WHERE password_hash IS NULL').bindparams(
            locked=LOCKED_PASSWORD_HASH
        )
    )
    op.execute(
        'UPDATE "user" SET privacy_consent_accepted_at = created_at '
        "WHERE privacy_consent_accepted_at IS NULL"
    )

    op.alter_column("user", "password_hash", nullable=False)
    op.alter_column("user", "privacy_consent_accepted_at", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("user", "privacy_consent_accepted_at")
    op.drop_column("user", "password_hash")
