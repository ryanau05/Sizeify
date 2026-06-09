"""core schema: user, garment_category, owned_garment, fit_signal, brand_product, recommendation

Revision ID: 0001_core_schema
Revises: bd99cc399c81
Create Date: 2026-05-25

Single initial migration for PRD §8 entities plus the §8.2 indexes. Two
PostgreSQL ENUM types (``fit_signal_verdict``, ``fit_signal_source``) are
created and dropped explicitly so the downgrade path is clean — Alembic's
table-drop does not cascade to user-defined enum types.

Foreign-key policy: user-owned chains (user → owned_garment → fit_signal,
user → recommendation) use ``ON DELETE CASCADE`` so TKT-P1-18's
``DELETE /me`` is one statement. Catalog references (garment_category,
brand_product) use ``ON DELETE RESTRICT`` so a catalog row cannot be
deleted out from under live closet / recommendation rows.

Enum values are inlined rather than imported from ``api.models.fit_signal``
because a migration represents a frozen historical schema — it must not
shift if the model later evolves.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_core_schema"
down_revision: str | Sequence[str] | None = "bd99cc399c81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    verdict_enum = postgresql.ENUM(*VERDICT_VALUES, name="fit_signal_verdict", create_type=False)
    source_enum = postgresql.ENUM(*SOURCE_VALUES, name="fit_signal_source", create_type=False)
    verdict_enum.create(bind, checkfirst=False)
    source_enum.create(bind, checkfirst=False)

    op.create_table(
        "user",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "preferred_units",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'cm'"),
        ),
        sa.Column("stated_fit_preference", sa.Text(), nullable=True),
        sa.Column("device_push_token", sa.Text(), nullable=True),
    )

    op.create_table(
        "garment_category",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("measurement_schema", postgresql.JSONB(), nullable=False),
        sa.Column("dimension_weights", postgresql.JSONB(), nullable=False),
    )

    op.create_table(
        "owned_garment",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "category_id",
            sa.Text(),
            sa.ForeignKey("garment_category.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("brand", sa.Text(), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=True),
        sa.Column("size_label", sa.Text(), nullable=False),
        sa.Column("measurements", postgresql.JSONB(), nullable=False),
        sa.Column("fabric_composition", sa.Text(), nullable=True),
        sa.Column("stretch_level", sa.Text(), nullable=True),
        sa.Column("overall_rating", sa.Text(), nullable=True),
        sa.Column(
            "use_cases",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_owned_garment_user_id_category_id",
        "owned_garment",
        ["user_id", "category_id"],
    )

    op.create_table(
        "fit_signal",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owned_garment_id",
            sa.Uuid(),
            sa.ForeignKey("owned_garment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column(
            "verdict",
            postgresql.ENUM(*VERDICT_VALUES, name="fit_signal_verdict", create_type=False),
            nullable=False,
        ),
        sa.Column("magnitude_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("use_case", sa.Text(), nullable=True),
        sa.Column(
            "source",
            postgresql.ENUM(*SOURCE_VALUES, name="fit_signal_source", create_type=False),
            nullable=False,
        ),
        sa.Column("raw_feedback_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "brand_product",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("brand", sa.Text(), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("product_url", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "category_id",
            sa.Text(),
            sa.ForeignKey("garment_category.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("fabric_composition", sa.Text(), nullable=True),
        sa.Column("stretch_level", sa.Text(), nullable=True),
        sa.Column("size_chart", postgresql.JSONB(), nullable=False),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scraper_version", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_brand_product_brand_category_id",
        "brand_product",
        ["brand", "category_id"],
    )

    op.create_table(
        "recommendation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "brand_product_id",
            sa.Uuid(),
            sa.ForeignKey("brand_product.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("recommended_size", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("fit_notes", postgresql.JSONB(), nullable=False),
        sa.Column(
            "reference_garment_ids",
            postgresql.ARRAY(sa.Uuid()),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
        sa.Column("use_case_assumed", sa.Text(), nullable=True),
        sa.Column(
            "outcome",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("outcome_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_recommendation_user_id_created_at",
        "recommendation",
        ["user_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_recommendation_user_id_created_at", table_name="recommendation")
    op.drop_table("recommendation")
    op.drop_index("ix_brand_product_brand_category_id", table_name="brand_product")
    op.drop_table("brand_product")
    op.drop_table("fit_signal")
    op.drop_index("ix_owned_garment_user_id_category_id", table_name="owned_garment")
    op.drop_table("owned_garment")
    op.drop_table("garment_category")
    op.drop_table("user")

    bind = op.get_bind()
    postgresql.ENUM(name="fit_signal_source").drop(bind, checkfirst=False)
    postgresql.ENUM(name="fit_signal_verdict").drop(bind, checkfirst=False)
