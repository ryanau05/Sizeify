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
* ``privacy_consent_accepted_at`` gets ``SYNTHETIC_CONSENT_AT``, a deliberately
  impossible timestamp (the Unix epoch, before this product existed). Using
  ``created_at`` was the first instinct and is wrong: it manufactures a consent
  record indistinguishable from a real one, and ``GET /me/export`` then presents
  it to the user as "the consent record we are relying on". A fabricated consent
  must be greppable. The pre-auth rows are dev fixtures, not real consent.
"""

import os
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import context, op

#: Set to ``1`` to allow a downgrade that destroys data.
DESTRUCTIVE_OPT_IN_ENV = "ALEMBIC_ALLOW_DESTRUCTIVE_DOWNGRADE"


def _require_destructive_optin(revision_name: str) -> None:
    """Refuse an irreversible downgrade unless explicitly allowed."""
    if os.environ.get(DESTRUCTIVE_OPT_IN_ENV) != "1":
        raise RuntimeError(
            f"downgrading {revision_name} destroys data that cannot be recovered. "
            f"Re-run with {DESTRUCTIVE_OPT_IN_ENV}=1 if that is genuinely intended."
        )


# revision identifiers, used by Alembic.
revision: str = "0003_user_credentials"
down_revision: str | Sequence[str] | None = "0002_refresh_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Deliberately not a parseable argon2 hash: ``verify`` raises
# InvalidHashError internally and returns False, so nothing can
# authenticate against it.
LOCKED_PASSWORD_HASH = "!locked-no-password-set"

# Obviously not a real consent timestamp: this product did not exist in 1970.
# Anything at this instant was manufactured by this migration, never given by
# a user.
#
# A ``datetime``, not the ISO string it reads as. SQLAlchemy infers a bind's
# type from its Python value, so a ``str`` here is sent as ``$1::VARCHAR`` and
# Postgres refuses to assign VARCHAR to ``timestamptz`` without a cast — the
# whole migration aborts on a fresh database. The column type is pinned on the
# bindparam below for the same reason.
SYNTHETIC_CONSENT_AT = datetime(1970, 1, 1, tzinfo=UTC)


#: Fail fast rather than queue behind a long-running transaction.
#:
#: This migration's ``SET NOT NULL`` steps take ACCESS EXCLUSIVE on ``user``,
#: and it reaches them holding row locks from two whole-table UPDATEs — the
#: classic deadlock shape against a live application transaction that already
#: holds ACCESS SHARE and then tries to update one of those rows. Without a
#: timeout the ALTER simply waits, and every reader queues behind it, so a
#: migration that cannot proceed takes the table down while it cannot
#: proceed.
#:
#: Three seconds is short enough that a blocked deploy fails while someone is
#: still watching it, and long enough to ride out ordinary contention. On the
#: empty tables v1 has, it is never reached.
LOCK_TIMEOUT = "3s"


def upgrade() -> None:
    """Upgrade schema."""
    if not context.is_offline_mode():
        op.execute(sa.text(f"SET lock_timeout = '{LOCK_TIMEOUT}'"))

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
        sa.text(
            'UPDATE "user" SET privacy_consent_accepted_at = :synthetic '
            "WHERE privacy_consent_accepted_at IS NULL"
        ).bindparams(
            sa.bindparam("synthetic", SYNTHETIC_CONSENT_AT, type_=sa.DateTime(timezone=True))
        )
    )

    op.alter_column("user", "password_hash", nullable=False)
    op.alter_column("user", "privacy_consent_accepted_at", nullable=False)


def downgrade() -> None:
    """Downgrade schema. Destructive and one-way — see the guard below.

    Dropping these columns destroys every credential and every consent record
    in the database. Re-upgrading then backfills ``LOCKED_PASSWORD_HASH`` for
    all of them, which no password can ever verify against, so every account
    is locked out — and there is no password-reset flow to recover through
    (``grep -rn "reset" src/api/routes`` returns nothing). Consent records are
    simply gone.

    Alembic will happily run this, so the guard is the only thing between a
    mistyped ``downgrade`` and an unrecoverable database.
    """
    _require_destructive_optin("0003_user_credentials")
    op.drop_column("user", "privacy_consent_accepted_at")
    op.drop_column("user", "password_hash")
