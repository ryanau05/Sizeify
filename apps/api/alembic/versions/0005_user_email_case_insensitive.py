"""unique user email, case-insensitively

Revision ID: 0005_user_email_ci
Revises: 0004_owned_garment_soft_delete
Create Date: 2026-09-10

Closes the gap between how the code looks an account up and what the
database actually enforces.

``UserRepository.get_by_email`` matches on ``lower(email)``, but the only
constraint was ``UNIQUE (email)`` — an exact-match index. Postgres
therefore accepted ``alice@example.com`` and ``Alice@example.com`` as two
distinct rows, which the signup pre-check could not prevent either: two
concurrent requests both read "no such account" and both inserted, and no
``IntegrityError`` was raised for the route's 409 handler to catch.

Once two such rows exist, ``get_by_email`` returns whichever the planner
happens to emit first, so a login for that address can be answered by
either account. The victim sees intermittent 401s on their own correct
password, and the outcome can flip after an ANALYZE or an index rebuild.

A functional unique index on ``lower(email)`` makes the constraint say
what the query means. The upgrade fails loudly if duplicates already
exist; that is deliberate — merging two accounts is a decision, not
something a migration should silently make.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0005_user_email_ci"
down_revision: str | Sequence[str] | None = "0004_owned_garment_soft_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: The duplicate check, as SQL that raises where it runs.
#:
#: ``--sql`` (offline) mode renders migrations to a script for a human to
#: apply, and there is no connection to query — ``op.get_bind().execute(...)``
#: returns ``None``, so the Python check below died with an ``AttributeError``
#: and took the whole chain's offline rendering with it.
#:
#: Skipping the check offline would be worse than the crash: the generated
#: script would create a UNIQUE index that fails at apply time with Postgres's
#: own terse message, against a database the author could not inspect. So the
#: guard is emitted *as part of the script* instead, where it runs at apply
#: time and refuses with the same explanation.
_OFFLINE_DUPLICATE_GUARD = """
DO $$
DECLARE offending text;
BEGIN
    SELECT string_agg(e, ', ') INTO offending
    FROM (
        SELECT lower(email) AS e FROM "user"
        GROUP BY lower(email) HAVING count(*) > 1
    ) AS duplicates;
    IF offending IS NOT NULL THEN
        RAISE EXCEPTION 'cannot add the case-insensitive email index: these '
            'addresses already exist more than once (differing only in case): %. '
            'Merge or remove the duplicate accounts, then re-run this migration.',
            offending;
    END IF;
END $$
"""


def upgrade() -> None:
    """Upgrade schema."""
    if context.is_offline_mode():
        op.execute(_OFFLINE_DUPLICATE_GUARD)
        op.create_index("uq_user_email_lower", "user", [sa.text("lower(email)")], unique=True)
        return

    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                'SELECT lower(email) AS e, count(*) FROM "user" '
                "GROUP BY lower(email) HAVING count(*) > 1"
            )
        )
        .fetchall()
    )
    if duplicates:
        raise RuntimeError(
            "cannot add the case-insensitive email index: these addresses already "
            f"exist more than once (differing only in case): {[row.e for row in duplicates]}. "
            "Merge or remove the duplicate accounts, then re-run this migration."
        )

    op.create_index(
        "uq_user_email_lower",
        "user",
        [sa.text("lower(email)")],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_user_email_lower", table_name="user")
