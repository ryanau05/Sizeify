"""Integration tests for recommendation persistence (TKT-P1-16).

The ticket's acceptance: run the recommendation flow, then assert the stored
row carries all four PRD §5.4 components and ``prompt_version='manual-v0'``.

These drive the *real* chain — closet rows → ``build_fit_profile`` →
``recommend`` → ``create_from`` → database — rather than persisting a
hand-built dataclass. That is the only way the test can catch the failure
that actually matters here: a component that exists in the domain output but
has nowhere to land in the schema.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from uuid import UUID

import pytest
import pytest_asyncio
from _factories import make_brand_product, make_owned_garment, make_user
from sqlalchemy.ext.asyncio import AsyncSession

from api.domain.fit_profile import (
    ClosetSnapshot,
    GarmentSnapshot,
    SignalSnapshot,
    build_fit_profile,
)
from api.domain.matching import BrandProduct
from api.domain.recommendation import recommend
from api.llm.versions import MANUAL_PROMPT_VERSION
from api.models import GarmentCategory, OwnedGarment, User
from api.repositories.recommendations import RecommendationRepository
from api.schemas.enums import OverallRating, StretchLevel, Verdict
from api.seeds.garment_categories import (
    MENS_BUTTON_DOWN_SHIRT_ID,
    seed_garment_categories,
)

# A chart whose M lands on the closet's preference and whose neighbours don't,
# so the flow produces a confident single-candidate recommendation.
PRODUCT = BrandProduct(
    brand="jcrew",
    product_name="Bowery Wrinkle-Free Dress Shirt",
    size_chart={
        "S": {"chest": 50.0, "shoulder_width": 43.0},
        "M": {"chest": 54.0, "shoulder_width": 46.0},
        "L": {"chest": 58.0, "shoulder_width": 49.0},
    },
)


@pytest_asyncio.fixture(autouse=True)
async def _seeded_category(db_session: AsyncSession) -> AsyncIterator[None]:
    await seed_garment_categories(db_session)
    await db_session.flush()
    yield


async def closet_rows(session: AsyncSession, user: User, count: int = 4) -> list[OwnedGarment]:
    """A small closet of real ``owned_garment`` rows."""
    category = await session.get(GarmentCategory, MENS_BUTTON_DOWN_SHIRT_ID)
    assert category is not None
    return [
        await make_owned_garment(
            session,
            user=user,
            category=category,
            brand=f"Brand {index}",
            product_name=f"Oxford {index}",
            measurements={
                "chest": {"value": 54.0, "unit": "cm", "source": "manual_tape"},
                "shoulder_width": {"value": 46.0, "unit": "cm", "source": "manual_tape"},
            },
            overall_rating="love",
            stretch_level="none",
        )
        for index in range(count)
    ]


def snapshot_from(rows: Sequence[OwnedGarment]) -> ClosetSnapshot:
    """Build the domain snapshot from database rows, ids included.

    This is the adapter TKT-P1-13 will own; doing it here keeps the test
    honest about where ``garment_id`` has to come from.
    """
    return ClosetSnapshot(
        category_id=MENS_BUTTON_DOWN_SHIRT_ID,
        garments=[
            GarmentSnapshot(
                label=f"{row.brand} {row.size_label}",
                brand=row.brand,
                size_label=row.size_label,
                measurements_cm={
                    dimension: value["value"] for dimension, value in row.measurements.items()
                },
                stretch_level=StretchLevel(row.stretch_level) if row.stretch_level else None,
                overall_rating=(OverallRating(row.overall_rating) if row.overall_rating else None),
                garment_id=row.id,
                signals=(SignalSnapshot("chest", Verdict.PREFERRED),),
            )
            for row in rows
        ],
    )


# ---------------------------------------------------------------------------
# The ticket's acceptance criterion.
# ---------------------------------------------------------------------------


async def test_recommendation_flow_persists_all_four_components(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session)
    rows = await closet_rows(db_session, user)
    product_row = await make_brand_product(db_session)

    output = recommend(build_fit_profile(snapshot_from(rows)), PRODUCT)
    stored = await RecommendationRepository(db_session).create_from(
        output, user_id=user.id, brand_product_id=product_row.id
    )

    # (1) Size.
    assert stored.recommended_size == output.primary.size_label == "M"
    # (2) Confidence — full NUMERIC(4, 3) precision, not a float artifact.
    assert stored.confidence == Decimal(str(output.confidence))
    # (3) Fit notes: the per-dimension narrative the user was shown.
    assert stored.fit_notes["primary"]["fit_notes"] == list(output.primary.fit_notes)
    assert stored.fit_notes["primary"]["fit_notes"]  # non-empty
    # (4) Reference garments: ids that point at real closet rows…
    assert stored.reference_garment_ids
    assert set(stored.reference_garment_ids) <= {row.id for row in rows}
    # …and the "why" sentences, which the ids alone cannot reconstruct.
    assert [ref["why"] for ref in stored.fit_notes["reference_garments"]] == [
        ref.why for ref in output.reference_garments
    ]

    # The prompt-version invariant (CLAUDE.md).
    assert stored.prompt_version == MANUAL_PROMPT_VERSION == "manual-v0"


async def test_persisted_row_is_readable_back(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    rows = await closet_rows(db_session, user)
    product_row = await make_brand_product(db_session)
    repo = RecommendationRepository(db_session)

    output = recommend(build_fit_profile(snapshot_from(rows)), PRODUCT)
    created = await repo.create_from(output, user_id=user.id, brand_product_id=product_row.id)
    await db_session.flush()

    fetched = await repo.get(created.id)

    assert fetched is not None
    assert fetched.recommended_size == "M"
    assert fetched.user_id == user.id
    assert fetched.brand_product_id == product_row.id
    # Server-side default: the outcome loop (PRD §5.5) starts here.
    assert fetched.outcome == "pending"
    assert fetched.outcome_confirmed_at is None
    assert fetched.created_at is not None


# ---------------------------------------------------------------------------
# Reference garment ids.
# ---------------------------------------------------------------------------


async def test_reference_ids_point_at_the_garments_that_drove_the_call(
    db_session: AsyncSession,
) -> None:
    """PRD §5.4's fourth component has to survive as data, not just prose —
    ``reference_garment_ids`` is what lets a stored recommendation be traced
    back to the closet rows behind it."""
    user = await make_user(db_session)
    rows = await closet_rows(db_session, user)
    product_row = await make_brand_product(db_session)

    output = recommend(build_fit_profile(snapshot_from(rows)), PRODUCT)
    stored = await RecommendationRepository(db_session).create_from(
        output, user_id=user.id, brand_product_id=product_row.id
    )

    assert len(stored.reference_garment_ids) == len(output.reference_garments)
    assert all(isinstance(value, UUID) for value in stored.reference_garment_ids)


async def test_reference_ids_survive_the_garment_being_deleted(
    db_session: AsyncSession,
) -> None:
    """The reason the column has no foreign key, and the reason
    ``owned_garment`` soft-deletes (TKT-P1-09): a recommendation stays
    explainable after the user clears out their closet.
    """
    user = await make_user(db_session)
    rows = await closet_rows(db_session, user)
    product_row = await make_brand_product(db_session)

    output = recommend(build_fit_profile(snapshot_from(rows)), PRODUCT)
    stored = await RecommendationRepository(db_session).create_from(
        output, user_id=user.id, brand_product_id=product_row.id
    )
    cited = list(stored.reference_garment_ids)

    from api.repositories.owned_garments import OwnedGarmentRepository

    for garment_id in cited:
        assert await OwnedGarmentRepository(db_session).soft_delete(garment_id, user.id)

    await db_session.refresh(stored)
    assert list(stored.reference_garment_ids) == cited


async def test_profile_without_garment_ids_stores_an_empty_reference_array(
    db_session: AsyncSession,
) -> None:
    """A hand-built profile (a unit test, a fixture) carries no ids. The
    column is NOT NULL, so it must land as an empty array rather than null."""
    user = await make_user(db_session)
    rows = await closet_rows(db_session, user)
    product_row = await make_brand_product(db_session)

    snapshot = snapshot_from(rows)
    anonymous = ClosetSnapshot(
        category_id=snapshot.category_id,
        garments=[
            GarmentSnapshot(
                label=g.label,
                brand=g.brand,
                size_label=g.size_label,
                measurements_cm=g.measurements_cm,
                stretch_level=g.stretch_level,
                overall_rating=g.overall_rating,
                signals=g.signals,
            )
            for g in snapshot.garments
        ],
    )

    output = recommend(build_fit_profile(anonymous), PRODUCT)
    stored = await RecommendationRepository(db_session).create_from(
        output, user_id=user.id, brand_product_id=product_row.id
    )

    assert stored.reference_garment_ids == []
    # The narrative still survives, so the row remains explainable.
    assert stored.fit_notes["reference_garments"]


# ---------------------------------------------------------------------------
# prompt_version and use_case_assumed.
# ---------------------------------------------------------------------------


async def test_prompt_version_can_be_overridden_for_phase_3(
    db_session: AsyncSession,
) -> None:
    """The sentinel is a default, not a hard-coded write — a real prompt
    version has to be able to travel with the row without reworking callers."""
    user = await make_user(db_session)
    rows = await closet_rows(db_session, user)
    product_row = await make_brand_product(db_session)

    output = recommend(build_fit_profile(snapshot_from(rows)), PRODUCT)
    stored = await RecommendationRepository(db_session).create_from(
        output,
        user_id=user.id,
        brand_product_id=product_row.id,
        prompt_version="fit_extraction/v2",
    )

    assert stored.prompt_version == "fit_extraction/v2"


def test_manual_sentinel_is_not_a_plausible_prompt_version() -> None:
    """It names the mechanism rather than posing as ``v1``, so a Phase 1 row
    can never be mistaken for one an LLM produced."""
    assert MANUAL_PROMPT_VERSION == "manual-v0"
    assert "manual" in MANUAL_PROMPT_VERSION


async def test_use_case_assumed_defaults_to_unconditioned(
    db_session: AsyncSession,
) -> None:
    """PRD §6.4 wants the assumed use case recorded. ``recommend`` does not
    select a variant yet, so ``None`` is the honest value — and the column is
    nullable precisely for it."""
    user = await make_user(db_session)
    rows = await closet_rows(db_session, user)
    product_row = await make_brand_product(db_session)

    output = recommend(build_fit_profile(snapshot_from(rows)), PRODUCT)
    repo = RecommendationRepository(db_session)

    default = await repo.create_from(output, user_id=user.id, brand_product_id=product_row.id)
    conditioned = await repo.create_from(
        output, user_id=user.id, brand_product_id=product_row.id, use_case_assumed="work"
    )

    assert default.use_case_assumed is None
    assert conditioned.use_case_assumed == "work"


# ---------------------------------------------------------------------------
# Two-candidate recommendations.
# ---------------------------------------------------------------------------


async def test_two_candidate_recommendation_persists_the_alternate(
    db_session: AsyncSession,
) -> None:
    """A low-confidence call returns two candidates with trade-offs (PRD
    §5.4); both have to survive, or the stored row misrepresents what the
    user was shown."""
    user = await make_user(db_session)
    rows = await closet_rows(db_session, user, count=2)  # thin closet → cold start
    product_row = await make_brand_product(db_session)

    output = recommend(build_fit_profile(snapshot_from(rows)), PRODUCT)
    assert output.alternate is not None, "fixture should produce two candidates"

    stored = await RecommendationRepository(db_session).create_from(
        output, user_id=user.id, brand_product_id=product_row.id
    )

    assert stored.fit_notes["alternate"]["size_label"] == output.alternate.size_label
    assert stored.fit_notes["alternate"]["tradeoff"] == output.alternate.tradeoff
    assert stored.fit_notes["primary"]["tradeoff"] == output.primary.tradeoff
    # The recommended size column still names the primary, not the alternate.
    assert stored.recommended_size == output.primary.size_label


async def test_single_candidate_recommendation_stores_no_alternate_key(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session)
    rows = await closet_rows(db_session, user)
    product_row = await make_brand_product(db_session)

    output = recommend(build_fit_profile(snapshot_from(rows)), PRODUCT)
    assert output.alternate is None

    stored = await RecommendationRepository(db_session).create_from(
        output, user_id=user.id, brand_product_id=product_row.id
    )

    assert "alternate" not in stored.fit_notes


# ---------------------------------------------------------------------------
# Export interaction.
# ---------------------------------------------------------------------------


async def test_persisted_recommendation_reaches_the_gdpr_export(
    db_session: AsyncSession,
) -> None:
    """TKT-P1-17 exports whatever this writes, so the two have to agree on
    the shape — including ``prompt_version``, which keeps an exported
    recommendation interpretable across prompt migrations."""
    user = await make_user(db_session)
    rows = await closet_rows(db_session, user)
    product_row = await make_brand_product(db_session)
    repo = RecommendationRepository(db_session)

    output = recommend(build_fit_profile(snapshot_from(rows)), PRODUCT)
    await repo.create_from(output, user_id=user.id, brand_product_id=product_row.id)

    exported = await repo.list_for_user(user.id)

    assert len(exported) == 1
    assert exported[0].prompt_version == MANUAL_PROMPT_VERSION
    assert exported[0].recommended_size == "M"


@pytest.mark.parametrize("count", [1, 2, 4, 5])
async def test_flow_persists_for_any_closet_size(db_session: AsyncSession, count: int) -> None:
    """Confidence and candidate count vary with closet depth; persistence
    must not."""
    user = await make_user(db_session)
    rows = await closet_rows(db_session, user, count=count)
    product_row = await make_brand_product(db_session)

    output = recommend(build_fit_profile(snapshot_from(rows)), PRODUCT)
    stored = await RecommendationRepository(db_session).create_from(
        output, user_id=user.id, brand_product_id=product_row.id
    )

    assert stored.recommended_size
    assert stored.prompt_version == MANUAL_PROMPT_VERSION
    assert stored.fit_notes["primary"]["fit_notes"]
    assert Decimal("0") <= stored.confidence <= Decimal("1")


async def test_each_stored_reference_carries_its_own_garment_id(
    db_session: AsyncSession,
) -> None:
    """``reference_garment_ids`` holds only the references that had an id, so
    a stored "why" must not depend on lining up positionally with it —
    attributing a sentence to the wrong shirt is worse than omitting it."""
    user = await make_user(db_session)
    rows = await closet_rows(db_session, user)
    product_row = await make_brand_product(db_session)

    output = recommend(build_fit_profile(snapshot_from(rows)), PRODUCT)
    stored = await RecommendationRepository(db_session).create_from(
        output, user_id=user.id, brand_product_id=product_row.id
    )

    stored_refs = stored.fit_notes["reference_garments"]
    assert all(ref["garment_id"] is not None for ref in stored_refs)
    assert [UUID(ref["garment_id"]) for ref in stored_refs] == list(stored.reference_garment_ids)
    # And each id belongs to the garment the sentence names.
    by_id = {str(row.id): row for row in rows}
    for ref in stored_refs:
        assert by_id[ref["garment_id"]].brand == ref["brand"]
