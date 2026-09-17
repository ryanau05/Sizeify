"""``Recommendation`` repository.

``create_from`` is the only way a recommendation should reach the database:
it is what guarantees the four PRD §5.4 components and the
``prompt_version`` invariant survive persistence, rather than leaving each
call site to remember them.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from api.domain.recommendation import Recommendation as RecommendationOutput
from api.llm.versions import MANUAL_PROMPT_VERSION
from api.models import Recommendation
from api.repositories.base import Repository


class _Unset:
    """Sentinel: distinguishes "not passed" from an explicit ``None``."""


_UNSET = _Unset()


def _narrative_payload(output: RecommendationOutput) -> dict[str, Any]:
    """The parts of PRD §5.4's reasoning that have no column of their own.

    ``recommended_size`` and ``confidence`` are columns; the per-dimension
    narrative, the two-candidate trade-off, and the reference garments' "why"
    strings are not, so they are frozen here as JSONB::

        {
          "primary":   {"size_label": ..., "fit_notes": [...], "tradeoff": ...},
          "alternate": {...},                     # only when one was offered
          "reference_garments": [{"label", "brand", "size_label", "why",
                                  "garment_id"}]
        }

    The reference narratives are stored alongside ``reference_garment_ids``
    rather than instead of them, because the ids alone cannot reconstruct the
    sentence the user was shown: the "why" was generated from the closet as it
    stood at recommendation time, and the closet moves. Keeping both is what
    makes a months-old recommendation still explainable (PRD §12.2).

    Each stored reference repeats its own ``garment_id`` rather than relying
    on lining up positionally with ``reference_garment_ids``. That column
    holds only the references that *had* an id, so the two arrays fall out of
    step the moment a profile mixes database-backed and hand-built garments —
    and a "why" attributed to the wrong shirt is worse than no "why" at all.
    """
    payload: dict[str, Any] = {
        "primary": output.primary.to_wire(),
        "reference_garments": [
            {
                **ref.to_wire(),
                "garment_id": str(ref.garment_id) if ref.garment_id is not None else None,
            }
            for ref in output.reference_garments
        ],
    }
    if output.alternate is not None:
        payload["alternate"] = output.alternate.to_wire()
    return payload


class RecommendationRepository(Repository[Recommendation, UUID]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Recommendation)

    async def create_from(
        self,
        output: RecommendationOutput,
        user_id: UUID,
        brand_product_id: UUID,
        *,
        prompt_version: str = MANUAL_PROMPT_VERSION,
        use_case_assumed: str | None | _Unset = _UNSET,
    ) -> Recommendation:
        """Persist a ``domain.recommendation.Recommendation`` (TKT-P1-16).

        ``prompt_version`` defaults to the Phase 1 sentinel because nothing in
        this path involves an LLM yet. It stays a parameter rather than a
        hard-coded write so Phase 3 can pass the real version without
        reworking the call site — and so the value is always something the
        caller chose, never something a default quietly invented.

        ``use_case_assumed`` defaults to whatever the engine actually
        conditioned on (``output.use_case_assumed``), so PRD §6.4's "explicitly
        notes which use case was assumed" cannot drift from what happened by a
        caller forgetting to pass it. An explicit value still overrides, and an
        explicit ``None`` still records "unconditioned".

        ``confidence`` converts through ``str`` so the ``NUMERIC(4, 3)``
        column stores the value the engine computed rather than the nearest
        binary float to it.
        """
        return await self.create(
            user_id=user_id,
            brand_product_id=brand_product_id,
            recommended_size=output.primary.size_label,
            confidence=Decimal(str(output.confidence)),
            fit_notes=_narrative_payload(output),
            reference_garment_ids=[
                ref.garment_id for ref in output.reference_garments if ref.garment_id is not None
            ],
            use_case_assumed=(
                output.use_case_assumed
                if isinstance(use_case_assumed, _Unset)
                else use_case_assumed
            ),
            prompt_version=prompt_version,
        )

    async def list_for_user(
        self, user_id: UUID, *, limit: int | None = None
    ) -> list[Recommendation]:
        """One user's recommendations, oldest first.

        Ascending rather than the reverse-chronological order the §8.2
        index serves, because the only caller today is the GDPR export
        (TKT-P1-17) and a dump reads as a timeline. The app's
        recent-recommendations view lands in Phase 6 and should sort DESC
        to use that index; the export is not on any latency budget.
        """
        stmt = (
            sa.select(Recommendation)
            .where(Recommendation.user_id == user_id)
            .order_by(Recommendation.created_at, Recommendation.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())
