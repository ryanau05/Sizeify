"""``/closet`` — garment CRUD (TKT-P1-09), manual fit-signal entry
(TKT-P1-10), and the derived fit profile (TKT-P1-13).

Every endpoint is scoped to ``CurrentUser`` (TKT-P1-08) and every read
goes through ``OwnedGarmentRepository``'s ``*_for_user`` helpers, which
take the owner id as a required argument. Ownership isolation is
therefore structural rather than something each handler remembers: there
is no unscoped query in this module.

Not-found policy
----------------
"No such garment", "belongs to another user", and "already deleted" all
return the same bare 404. Distinguishing them would turn the endpoint
into an oracle for which garment ids exist and who owns them, and there
is nothing a legitimate client would do differently in each case.

Measurement validation
----------------------
Two layers, because the rules come from two places:

* ``schemas.closet.MeasurementValue`` enforces what is static — cm-only
  unit, positive value, a known provenance ``source``. Failures here are
  Pydantic 422s and never reach a handler.
* ``domain.measurements.validate_measurements`` enforces what the
  *category* declares — which dimensions exist, which are required, and
  the PRD §5.2 range on each. That schema is a per-category JSONB blob
  loaded at request time, so it cannot live in a Pydantic model.

Both surface as 422 with the same ``loc``/``msg``/``type`` entry shape, so
a client parses one error format regardless of which layer rejected it.
The same split applies to ``POST .../fit-signals``: the ``verdict`` enum is
static and Pydantic's, while ``dimension`` is checked against the category
schema here.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from api.deps import (
    UNAUTHORIZED_RESPONSE,
    CurrentUser,
    FitSignalRepositoryDep,
    GarmentCategoryRepositoryDep,
    OwnedGarmentRepositoryDep,
)
from api.domain.measurements import (
    MeasurementProblem,
    dimension_names,
    synthesize_feedback_text,
    validate_measurements,
)
from api.models import GarmentCategory
from api.repositories import GarmentCategoryRepository
from api.repositories.base import transaction
from api.schemas.closet import (
    FitSignalCreate,
    FitSignalResponse,
    GarmentMeasurements,
    OwnedGarmentCreate,
    OwnedGarmentListResponse,
    OwnedGarmentResponse,
    OwnedGarmentUpdate,
)
from api.schemas.enums import FitSignalSource
from api.schemas.fit_profile import FitProfileResponse
from api.seeds.garment_categories import MENS_BUTTON_DOWN_SHIRT_ID
from api.services.fit_profile import build_user_fit_profile

router = APIRouter(prefix="/closet", tags=["closet"])

# v1 accepts exactly one garment category (CLAUDE.md "v1 scope rules", PRD
# §3.2). The category must also exist in ``garment_category`` — that row
# owns the measurement schema — but the DB check alone is not the scope
# gate: seeding a second category should not silently open the API to it.
# Widening v1 means editing this set *and* the seed *and* the four things
# CLAUDE.md's "Workflow rules" lists, which is exactly the deliberate,
# greppable change it should be.
SUPPORTED_CATEGORY_IDS = frozenset({MENS_BUTTON_DOWN_SHIRT_ID})

_NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "description": "No such garment in the authenticated user's closet."
    }
}


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Garment not found in your closet.",
    )


def _unprocessable(errors: list[dict[str, Any]]) -> HTTPException:
    """422 whose body matches FastAPI's own validation-error shape.

    FastAPI renders ``RequestValidationError`` as ``{"detail": [{"loc":
    [...], "msg": ..., "type": ...}]}``. Hand-built validation failures use
    the same envelope so a client has one parser and can highlight the
    offending input either way.
    """
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=errors)


def _measurement_errors(
    problems: list[MeasurementProblem], *, body_field: str = "measurements"
) -> list[dict[str, Any]]:
    """Render domain problems as per-field validation errors."""
    return [
        {
            "loc": ["body", body_field, *problem.field_path],
            "msg": problem.message,
            "type": f"value_error.measurement.{problem.kind}",
        }
        for problem in problems
    ]


def _require_supported_category(category_id: str) -> None:
    """Reject a category outside v1 scope.

    Create-time only. A PATCH does not re-run this: the client did not send
    ``category_id`` (it isn't updatable), so reporting an error against that
    field would point at input they never provided. A garment whose category
    later leaves the supported set stays editable, which is the right
    behavior — the alternative strands the user's data.
    """
    if category_id not in SUPPORTED_CATEGORY_IDS:
        raise _unprocessable(
            [
                {
                    "loc": ["body", "category_id"],
                    "msg": (
                        f"Unsupported garment category {category_id!r}. "
                        f"v1 supports: {', '.join(sorted(SUPPORTED_CATEGORY_IDS))}."
                    ),
                    "type": "value_error.category.unsupported",
                }
            ]
        )


async def _load_category(
    category_id: str, categories: GarmentCategoryRepository
) -> GarmentCategory:
    """Fetch the category row that owns the measurement schema."""
    category = await categories.get(category_id)
    if category is None:
        # In scope but absent from the DB: the seed has not been run against
        # this database. A 500 is right — the request is well-formed and the
        # server is the thing that is misconfigured.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Garment category {category_id!r} is not seeded. "
                "Run: uv run python -m api.seeds.garment_categories"
            ),
        )
    return category


def _validate_against_category(
    measurements: GarmentMeasurements, category: GarmentCategory
) -> None:
    problems = validate_measurements(measurements, category.measurement_schema)
    if problems:
        raise _unprocessable(_measurement_errors(problems))


def _serialize_measurements(measurements: GarmentMeasurements) -> dict[str, Any]:
    """Wire shape → JSONB shape.

    They are the same shape on purpose: the stored blob is the submitted
    measurement objects verbatim, keyed by canonical dimension name. Keeping
    provenance (``source``) in the row is what lets PRD §A.9's CV-assist
    flow land without a migration, and what lets a read round-trip exactly
    what was written.
    """
    return {name: value.model_dump() for name, value in measurements.items()}


@router.get(
    "/garments",
    responses={**UNAUTHORIZED_RESPONSE},
)
async def list_garments(
    user: CurrentUser,
    garments: OwnedGarmentRepositoryDep,
) -> OwnedGarmentListResponse:
    """The authenticated user's closet, oldest garment first.

    Soft-deleted garments are omitted. No pagination parameters yet — a
    v1 closet is a handful of shirts (PRD §10.1 asks for 3-5 at
    onboarding) — but the response is an object rather than a bare array
    so a cursor can be added without breaking clients.

    ``limit=None`` is explicit because the repository's default is 100.
    Inheriting that default made this endpoint disagree with two others:
    ``GET /me/export`` reads unbounded, and so does the fit-profile
    builder, so garment 101 onward shaped the user's recommendations
    while being invisible in their closet and impossible to edit or
    delete (the client never learned its id). A silently short list is
    worse than a slow one at this scale.
    """
    rows = await garments.list_for_user(user.id, limit=None)
    return OwnedGarmentListResponse(
        items=[OwnedGarmentResponse.model_validate(row) for row in rows]
    )


@router.post(
    "/garments",
    status_code=status.HTTP_201_CREATED,
    responses={**UNAUTHORIZED_RESPONSE},
)
async def create_garment(
    body: OwnedGarmentCreate,
    user: CurrentUser,
    garments: OwnedGarmentRepositoryDep,
    categories: GarmentCategoryRepositoryDep,
) -> OwnedGarmentResponse:
    """Add a garment to the authenticated user's closet."""
    _require_supported_category(body.category_id)
    category = await _load_category(body.category_id, categories)
    _validate_against_category(body.measurements, category)

    async with transaction(garments.session):
        garment = await garments.create(
            user_id=user.id,
            category_id=body.category_id,
            brand=body.brand,
            product_name=body.product_name,
            size_label=body.size_label,
            measurements=_serialize_measurements(body.measurements),
            fabric_composition=body.fabric_composition,
            stretch_level=body.stretch_level,
            overall_rating=body.overall_rating,
            use_cases=body.use_cases,
        )

    return OwnedGarmentResponse.model_validate(garment)


@router.patch(
    "/garments/{garment_id}",
    responses={**UNAUTHORIZED_RESPONSE, **_NOT_FOUND_RESPONSE},
)
async def update_garment(
    garment_id: UUID,
    body: OwnedGarmentUpdate,
    user: CurrentUser,
    garments: OwnedGarmentRepositoryDep,
    categories: GarmentCategoryRepositoryDep,
) -> OwnedGarmentResponse:
    """Partially update a garment.

    Only submitted fields change: ``exclude_unset`` is what separates
    "omitted, leave it alone" from an explicit ``null`` that clears an
    optional field. ``category_id`` is not updatable — re-categorizing
    would leave the garment's fit signals pointing at dimensions its new
    category may not have (see ``schemas.closet.OwnedGarmentUpdate``).
    """
    garment = await garments.get_for_user(garment_id, user.id)
    if garment is None:
        raise _not_found()

    changes = body.model_dump(exclude_unset=True)

    # ``measurements`` replaces wholesale rather than merging per dimension.
    # A partial merge would let a client end up with a set it never saw as a
    # whole, and there is no way to *remove* a dimension under merge
    # semantics. The client sends the full measurement set it wants stored.
    if "measurements" in changes and body.measurements is not None:
        category = await _load_category(garment.category_id, categories)
        _validate_against_category(body.measurements, category)
        changes["measurements"] = _serialize_measurements(body.measurements)

    if changes:
        async with transaction(garments.session):
            # Ownership was already established by ``get_for_user`` above, so
            # the repository's by-primary-key ``update`` is safe here and
            # keeps the mutation inside the repository layer.
            updated = await garments.update(garment_id, **changes)
        if updated is not None:
            garment = updated

    return OwnedGarmentResponse.model_validate(garment)


@router.delete(
    "/garments/{garment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**UNAUTHORIZED_RESPONSE, **_NOT_FOUND_RESPONSE},
)
async def delete_garment(
    garment_id: UUID,
    user: CurrentUser,
    garments: OwnedGarmentRepositoryDep,
) -> None:
    """Soft-delete a garment: sets ``deleted_at``, keeps the row.

    The row survives because ``recommendation.reference_garment_ids``
    cites it — a hard delete would make every past recommendation that
    referenced this garment unexplainable (PRD §5.4 requires reference
    garments in the reasoning).

    Deleting an already-deleted garment is a 404, matching every other
    read of a tombstoned row rather than reporting success for a change
    that did not happen.
    """
    async with transaction(garments.session):
        deleted = await garments.soft_delete(garment_id, user.id)

    if not deleted:
        raise _not_found()


@router.post(
    "/garments/{garment_id}/fit-signals",
    status_code=status.HTTP_201_CREATED,
    responses={**UNAUTHORIZED_RESPONSE, **_NOT_FOUND_RESPONSE},
)
async def create_fit_signal(
    garment_id: UUID,
    body: FitSignalCreate,
    user: CurrentUser,
    garments: OwnedGarmentRepositoryDep,
    categories: GarmentCategoryRepositoryDep,
    signals: FitSignalRepositoryDep,
) -> FitSignalResponse:
    """Record a fit signal on one of the user's garments — TKT-P1-10.

    This is the v1 manual-entry path, so every row lands with ``source =
    'user_added'``. It is not settable from the request: the other two
    provenances (``nlp_extracted``, ``user_edited``) describe how the LLM
    pipeline produced a signal, and letting a client claim either would
    corrupt the extraction-quality metrics that ``source`` exists to
    support (PRD §6.1, CLAUDE.md domain conventions).

    ``raw_feedback_text`` is likewise never null. The client may supply the
    user's own words; otherwise the row gets a synthesized rendering of the
    structured signal (see ``domain.measurements.synthesize_feedback_text``).
    """
    garment = await garments.get_for_user(garment_id, user.id)
    if garment is None:
        raise _not_found()

    category = await _load_category(garment.category_id, categories)
    _validate_dimension(body.dimension, category)

    async with transaction(signals.session):
        signal = await signals.create(
            owned_garment_id=garment.id,
            dimension=body.dimension,
            verdict=body.verdict.value,
            magnitude_cm=_as_numeric(body.magnitude_cm),
            use_case=body.use_case,
            source=FitSignalSource.USER_ADDED.value,
            # ``or`` rather than ``is not None``: an empty string would
            # read as "the user said nothing", which is exactly the
            # confusion the column is meant to avoid, so it falls through
            # to the synthesized rendering too.
            raw_feedback_text=body.raw_feedback_text
            or synthesize_feedback_text(
                body.dimension,
                body.verdict.value,
                magnitude_cm=body.magnitude_cm,
                use_case=body.use_case,
            ),
        )

    return FitSignalResponse.model_validate(signal)


def _validate_dimension(dimension: str, category: GarmentCategory) -> None:
    """Reject a dimension the garment's category does not declare.

    Same reasoning as the measurement validator: a signal on a dimension
    the matching engine never reads is worse than a rejection, because the
    user believes they recorded feedback that will never affect a
    recommendation.
    """
    known = dimension_names(category.measurement_schema)
    if dimension in known:
        return
    raise _unprocessable(
        [
            {
                "loc": ["body", "dimension"],
                "msg": (
                    f"Unknown fit dimension {dimension!r} for category "
                    f"{category.id!r}. Expected one of: {', '.join(sorted(known)) or '(none)'}."
                ),
                "type": "value_error.dimension.unknown",
            }
        ]
    )


def _as_numeric(magnitude_cm: float | None) -> Decimal | None:
    """Float → ``Decimal`` for the ``NUMERIC(6, 2)`` column.

    Converted via ``str`` so the value that lands is the one the client
    wrote, not the nearest binary float to it (``Decimal(1.1)`` is
    ``1.100000000000000088…``, which then rounds by a different path than
    the user's ``1.1``).
    """
    if magnitude_cm is None:
        return None
    return Decimal(str(magnitude_cm))


@router.get("/fit-profile", responses={**UNAUTHORIZED_RESPONSE})
async def get_fit_profile(
    user: CurrentUser,
    garments: OwnedGarmentRepositoryDep,
    signals: FitSignalRepositoryDep,
) -> FitProfileResponse:
    """The user's current fit profile, derived from their closet (PRD §6.2).

    Nothing is stored: the profile is recomputed from the closet on every
    request, so it can never disagree with the garments and signals it claims
    to summarize. That is affordable at v1 scale — a closet is a handful of
    shirts — and PRD §9.2's 100 ms budget applies to the share-sheet path,
    where a cache keyed on the user and invalidated on closet change is the
    documented answer. Adding that cache before it is measured would be the
    wrong order (CLAUDE.md: Redis "once measured to be needed").

    An empty closet is a 200, not a 404: "you have no profile yet" is a
    normal state for a new user, and the response says so with
    ``maturity='cold_start'``, empty dimension maps, and a hint the app shows
    as a banner. Returning an error would make onboarding look broken.

    v1 has exactly one garment category, so the profile is implicitly the
    button-down one. A ``category_id`` parameter lands when a second category
    does (CLAUDE.md v1 scope rules).
    """
    profile = await build_user_fit_profile(user, garments, signals)
    return profile.to_response()
