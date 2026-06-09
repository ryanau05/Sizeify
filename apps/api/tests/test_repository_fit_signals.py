"""CRUD tests for ``FitSignalRepository``.

Also exercises the FK cascade configured in migration 0001 — deleting the
owning garment should sweep its signals away.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from _factories import make_fit_signal, make_owned_garment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import FitSignal
from api.repositories.fit_signals import FitSignalRepository
from api.repositories.owned_garments import OwnedGarmentRepository


async def test_create_and_get(db_session: AsyncSession) -> None:
    garment = await make_owned_garment(db_session)
    repo = FitSignalRepository(db_session)

    signal = await repo.create(
        owned_garment_id=garment.id,
        dimension="chest",
        verdict="slightly_tight",
        magnitude_cm=Decimal("1.50"),
        use_case="layering",
        source="nlp_extracted",
        raw_feedback_text="The chest is a bit tight under a sweater.",
    )

    fetched = await repo.get(signal.id)
    assert fetched is not None
    assert fetched.dimension == "chest"
    assert fetched.verdict == "slightly_tight"
    # PG enum round-trips as the string value.
    assert fetched.source == "nlp_extracted"
    assert fetched.magnitude_cm == Decimal("1.50")


async def test_get_missing_returns_none(db_session: AsyncSession) -> None:
    repo = FitSignalRepository(db_session)
    assert await repo.get(uuid4()) is None


async def test_list_returns_inserted_rows(db_session: AsyncSession) -> None:
    garment = await make_owned_garment(db_session)
    await make_fit_signal(db_session, owned_garment=garment, dimension="chest")
    await make_fit_signal(db_session, owned_garment=garment, dimension="sleeve_length")

    repo = FitSignalRepository(db_session)
    rows = await repo.list()
    dimensions = {s.dimension for s in rows if s.owned_garment_id == garment.id}
    assert dimensions == {"chest", "sleeve_length"}


async def test_update_patches_verdict(db_session: AsyncSession) -> None:
    signal = await make_fit_signal(db_session)
    repo = FitSignalRepository(db_session)

    updated = await repo.update(signal.id, verdict="too_tight", source="user_edited")
    assert updated is not None
    assert updated.verdict == "too_tight"
    assert updated.source == "user_edited"


async def test_delete_removes_row(db_session: AsyncSession) -> None:
    signal = await make_fit_signal(db_session)
    repo = FitSignalRepository(db_session)

    assert await repo.delete(signal.id) is True
    assert await repo.get(signal.id) is None


async def test_garment_delete_cascades_to_signals(db_session: AsyncSession) -> None:
    # Schema-level invariant from migration 0001: deleting a garment via
    # its repo must sweep its signals away. Repos are thin — this is
    # really testing the FK cascade, which the repo layer relies on for
    # TKT-P1-09's DELETE handler.
    garment = await make_owned_garment(db_session)
    await make_fit_signal(db_session, owned_garment=garment)
    await make_fit_signal(db_session, owned_garment=garment)

    owned_repo = OwnedGarmentRepository(db_session)
    assert await owned_repo.delete(garment.id) is True

    remaining = (
        (
            await db_session.execute(
                select(FitSignal).where(FitSignal.owned_garment_id == garment.id)
            )
        )
        .scalars()
        .all()
    )
    assert remaining == []
