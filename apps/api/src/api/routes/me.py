"""``/me`` — the user's own data (GDPR/CCPA). TKT-P1-17.

``GET /me/export`` is the Art. 15 / CCPA right-of-access implementation:
one JSON object containing everything the system stores *about this user*,
and nothing else. ``DELETE /me`` is the Art. 17 erasure counterpart —
together they are the two flows PRD §11 requires from day one.

What "about this user" means here
---------------------------------
Included: the profile row, the whole closet (soft-deleted garments too —
see below), every fit signal on those garments, and every recommendation
generated for them.

Excluded, deliberately:

* ``password_hash`` and the refresh-token chain. Credentials are not
  usefully "the user's data" and putting them in a downloadable file is a
  liability, not a service.
* ``brand_product`` size charts and ``garment_category`` schemas. Those are
  shared catalog state that happens to be *referenced* by the user's rows,
  not personal data. ``brand_product_id`` is exported so a recommendation
  stays identifiable; the chart behind it is not the user's to take.

Soft-deleted garments are included because they are still stored and still
processed — historic recommendations cite them by id. They carry
``deleted_at`` so the export never presents a garment the user removed as
though it were still in their closet.

Completeness
------------
Every query runs unbounded (``limit=None``). A paginated export that
silently stopped at the repositories' default page size would be a
compliance failure that looks exactly like a working endpoint, which is
the worst way for this to break.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, status

from api.deps import (
    UNAUTHORIZED_RESPONSE,
    CurrentUser,
    FitSignalRepositoryDep,
    OwnedGarmentRepositoryDep,
    RecommendationRepositoryDep,
    UserRepositoryDep,
)
from api.repositories.base import transaction
from api.schemas.closet import FitSignalResponse
from api.schemas.me import (
    ExportResponse,
    OwnedGarmentExport,
    RecommendationExport,
    UserExport,
)

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/export", responses={**UNAUTHORIZED_RESPONSE})
async def export(
    user: CurrentUser,
    garments: OwnedGarmentRepositoryDep,
    signals: FitSignalRepositoryDep,
    recommendations: RecommendationRepositoryDep,
) -> ExportResponse:
    """Everything stored about the authenticated user, as one JSON object.

    Scoping is inherited rather than re-implemented: each repository's
    ``*_for_user`` helper takes the owner id as a required argument, so
    there is no query here that could accidentally reach another user's
    rows. ``user`` comes from the bearer token, never from a parameter —
    there is no way to ask for somebody else's export.
    """
    closet = await garments.list_for_user(user.id, include_deleted=True, limit=None)

    return ExportResponse(
        exported_at=datetime.now(UTC),
        user=UserExport.model_validate(user),
        closet=[OwnedGarmentExport.model_validate(row) for row in closet],
        signals=[
            FitSignalResponse.model_validate(row)
            for row in await signals.list_for_user(user.id, limit=None)
        ],
        recommendations=[
            RecommendationExport.model_validate(row)
            for row in await recommendations.list_for_user(user.id, limit=None)
        ],
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, responses={**UNAUTHORIZED_RESPONSE})
async def delete_me(
    user: CurrentUser,
    users: UserRepositoryDep,
) -> None:
    """Erase the authenticated user's account and all their data.

    A hard delete, not a tombstone: this is the GDPR Art. 17 / CCPA
    erasure path, and the point is that the data stops existing.
    ``owned_garment``'s soft delete (TKT-P1-09) exists so removing one
    shirt keeps past recommendations readable — there is nothing left to
    keep readable once the account itself is gone.

    Idempotent in the way that matters: the user row backs every
    ``CurrentUser`` resolution, so once it is gone the caller's access
    token stops authenticating and a repeat call answers 401 rather than
    204. Their refresh tokens are deleted with the account, so the pair
    cannot be rotated back into a working credential either. Neither
    response tells the caller whether the account ever existed — that is
    the same non-disclosure rule TKT-P1-08 applies to every unknown
    subject.
    """
    async with transaction(users.session):
        await users.delete_account(user.id)
