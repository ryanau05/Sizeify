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

import logging
from datetime import UTC, datetime

import anyio.to_thread
from fastapi import APIRouter, HTTPException, status

from api.auth import password as auth_password
from api.deps import (
    RATE_LIMITED_RESPONSE,
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
    AccountDeleteRequest,
    ExportResponse,
    OwnedGarmentExport,
    RecommendationExport,
    UserExport,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/export", responses={**UNAUTHORIZED_RESPONSE, **RATE_LIMITED_RESPONSE})
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


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**UNAUTHORIZED_RESPONSE, **RATE_LIMITED_RESPONSE},
)
async def delete_me(
    body: AccountDeleteRequest,
    user: CurrentUser,
    users: UserRepositoryDep,
) -> None:
    """Erase the authenticated user's account and all their data.

    A hard delete, not a tombstone: this is the GDPR Art. 17 / CCPA
    erasure path, and the point is that the data stops existing.
    ``owned_garment``'s soft delete (TKT-P1-09) exists so removing one
    shirt keeps past recommendations readable — there is nothing left to
    keep readable once the account itself is gone.

    Requires the account's current password in the body (PRD §11). Erasure
    is irreversible and cascades to every table the user owns, so a leaked or
    borrowed access token must not be sufficient on its own.

    Idempotent in the way that matters: the user row backs every
    ``CurrentUser`` resolution, so once it is gone the caller's access
    token stops authenticating and a repeat call answers 401 rather than
    204. Their refresh tokens are deleted with the account, so the pair
    cannot be rotated back into a working credential either. Neither
    response tells the caller whether the account ever existed — that is
    the same non-disclosure rule TKT-P1-08 applies to every unknown
    subject.
    """
    # Re-authenticate. The bearer token got the caller this far; proving they
    # know the password is what distinguishes the account's owner from anyone
    # holding a token that leaked. Verified off the event loop for the same
    # reason login is (argon2 is ~75-250 ms of CPU).
    if not await anyio.to_thread.run_sync(auth_password.verify, body.password, user.password_hash):
        logger.warning("me.account.erase_denied", extra={"user_id": str(user.id)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password does not match; account not deleted.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    async with transaction(users.session):
        await users.delete_account(user.id)

    # The one action in the API with no undo. Logged after the commit so the
    # record means "this happened", not "this was attempted".
    logger.warning("me.account.erased", extra={"user_id": str(user.id)})
