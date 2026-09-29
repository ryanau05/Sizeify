"""Request/response schemas for ``/closet/garments`` and its nested
``/fit-signals`` sub-resource.

PRD §A.9 forward-compat lives here: every measurement carries an explicit
``source`` so the CV-assist iteration can ship the same wire format
without a breaking change. ``unit`` is fixed to ``"cm"`` — display-unit
conversion happens at the UI boundary (CLAUDE.md).
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.schemas.enums import (
    FitSignalSource,
    OverallRating,
    StretchLevel,
    Verdict,
)


class MeasurementValue(BaseModel):
    """One measurement on a single dimension (PRD §5.2 + §A.9).

    ``source`` discriminates measurement provenance for the matching
    engine's confidence calculation — CV-assisted measurements have
    different error bars than tape measurements, and ``imported`` rows
    (e.g. backfilled from outcome confirmations) sit somewhere in
    between. v1 only writes ``manual_tape``; the other values are
    reserved so v2 can land without a schema bump.
    """

    model_config = ConfigDict(extra="forbid")

    value: float = Field(gt=0, allow_inf_nan=False)
    unit: Literal["cm"] = "cm"
    source: Literal["manual_tape", "cv_assisted", "imported"]


# Keys are dimension names from ``garment_category.measurement_schema``
# (``chest``, ``body_length``, …). Validation against the category
# schema lives in the route handler (TKT-P1-09) because the schema is
# a dynamic per-category JSONB blob.
#: The v1 category declares six dimensions; the cap is well clear of that and
#: of any plausible successor. Bounded because every *string* here had a
#: length limit but neither collection had a size limit, so one request body
#: could carry hundreds of thousands of unknown keys — each parsed into a
#: model, each turned into a MeasurementProblem, each rendered into the 422
#: body, amplifying the input through three full materializations.
MAX_MEASUREMENT_DIMENSIONS = 32

GarmentMeasurements = Annotated[
    dict[str, MeasurementValue], Field(max_length=MAX_MEASUREMENT_DIMENSIONS)
]

#: PRD §6.4 use cases are a short human list ("work", "gym", "weekend").
#: Unbounded, a single array landed in the ARRAY(Text) column and was re-read
#: by every closet list, fit-profile build and GDPR export thereafter.
MAX_USE_CASES = 20


# Free-text labels carry user data into the LLM extraction pipeline —
# trim and length-cap at the schema layer so the prompt construction in
# Phase 3 doesn't have to defend against pathologically long input.
_BrandField = Annotated[str, Field(min_length=1, max_length=100)]
_ProductNameField = Annotated[str, Field(min_length=1, max_length=200)]
_SizeLabelField = Annotated[str, Field(min_length=1, max_length=50)]
_FabricCompositionField = Annotated[str, Field(min_length=1, max_length=200)]
_UseCaseField = Annotated[str, Field(min_length=1, max_length=50)]


class _OwnedGarmentBase(BaseModel):
    """Fields shared between create / update / response for ``owned_garment``."""

    brand: _BrandField
    product_name: _ProductNameField | None = None
    size_label: _SizeLabelField
    measurements: GarmentMeasurements
    fabric_composition: _FabricCompositionField | None = None
    stretch_level: StretchLevel | None = None
    overall_rating: OverallRating | None = None
    use_cases: list[_UseCaseField] = Field(default_factory=list, max_length=MAX_USE_CASES)


class OwnedGarmentCreate(_OwnedGarmentBase):
    """``POST /closet/garments`` body (TKT-P1-09).

    ``category_id`` is the garment_category slug (e.g.
    ``"mens_button_down_shirt"``). v1 only accepts the seeded slug; the
    route validates against ``garment_category`` before insert.

    ``extra="forbid"`` here and not on ``_OwnedGarmentBase`` so the response
    model stays permissive: strictness belongs on what clients send, not on
    what we build from a database row. Without it, create silently dropped
    unknown fields while PATCH rejected them, so a client typo failed loudly
    on one verb and silently on the other.
    """

    model_config = ConfigDict(extra="forbid")

    category_id: str = Field(min_length=1, max_length=100)


#: Fields whose columns are NOT NULL. Omitting them means "leave alone";
#: sending them as ``null`` is a client error, not a clear-the-field request.
_NOT_NULLABLE_ON_UPDATE = ("brand", "size_label", "measurements", "use_cases")


class OwnedGarmentUpdate(BaseModel):
    """``PATCH /closet/garments/{id}`` body (TKT-P1-09).

    All fields optional for partial-update semantics. ``category_id`` is
    intentionally omitted — re-categorizing an existing garment would
    invalidate its fit signals (different dimension set), so the client
    must delete-and-recreate instead.

    "Optional" here means *omittable*, not *nullable*. Four of these back
    NOT NULL columns and are rejected when sent explicitly as ``null``;
    only ``product_name``, ``fabric_composition``, ``stretch_level`` and
    ``overall_rating`` can actually be cleared.

    That distinction is load-bearing rather than pedantic. SQLAlchemy's
    JSON type maps Python ``None`` onto JSON ``null`` instead of SQL NULL,
    so ``{"measurements": null}`` used to slip past the NOT NULL constraint
    and store ``'null'::jsonb`` — after which every read of that garment
    (the closet list, the fit profile, and the GDPR export) raised, for
    good. One ordinary PATCH could permanently brick an account.
    """

    model_config = ConfigDict(extra="forbid")

    @field_validator(*_NOT_NULLABLE_ON_UPDATE, mode="before")
    @classmethod
    def _reject_explicit_null(cls, value: Any) -> Any:
        """422 on a present-but-null key backing a NOT NULL column.

        A *field* validator, not a model one, so the error carries the field
        in its ``loc``. The model-level version produced ``loc: []`` and named
        the offending fields only inside the prose message, which meant a
        client could not highlight the input that was wrong and a second
        nulled field arrived joined by a comma rather than as its own entry —
        neither of which matches how every other 422 on this endpoint reads.

        The distinction being enforced is "was the key present at all", and
        this still draws it: Pydantic skips a field validator when the key is
        absent and the field has a default, so an omitted field never reaches
        here while an explicit ``null`` does.
        """
        if value is None:
            raise ValueError("cannot be cleared; omit the field to leave it unchanged")
        return value

    brand: _BrandField | None = None
    product_name: _ProductNameField | None = None
    size_label: _SizeLabelField | None = None
    measurements: GarmentMeasurements | None = None
    fabric_composition: _FabricCompositionField | None = None
    stretch_level: StretchLevel | None = None
    overall_rating: OverallRating | None = None
    use_cases: list[_UseCaseField] | None = Field(default=None, max_length=MAX_USE_CASES)


class OwnedGarmentResponse(_OwnedGarmentBase):
    """``GET / POST / PATCH`` response for a single owned garment.

    ``from_attributes=True`` lets route handlers do
    ``OwnedGarmentResponse.model_validate(orm_obj)`` without a manual
    field-by-field copy.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    category_id: str
    created_at: datetime


class OwnedGarmentListResponse(BaseModel):
    """``GET /closet/garments`` response.

    Wrapped in an object (rather than a bare ``list[...]``) so future
    pagination metadata (cursor, total) lands without a breaking change.
    """

    items: list[OwnedGarmentResponse]


class FitSignalCreate(BaseModel):
    """``POST /closet/garments/{id}/fit-signals`` body (TKT-P1-10).

    The manual-entry path: signals created via this endpoint always
    land with ``source = "user_added"`` and the route synthesizes the
    ``raw_feedback_text`` column from the dimension+verdict combo when
    the client doesn't supply one (CLAUDE.md: column is never null).

    Extras are forbidden so a body carrying ``source`` — the one field this
    endpoint deliberately refuses to let clients set — is a 422 naming it. It
    used to be dropped in silence, so a client that believed it was writing
    ``nlp_extracted`` rows never found out otherwise.
    """

    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(min_length=1, max_length=100)
    verdict: Verdict
    # Bounded to the NUMERIC(6, 2) column it lands in. Unbounded, an ordinary
    # request body (magnitude_cm: 100000.0, or a JSON `Infinity` literal, which
    # stdlib json accepts) reached Postgres and came back as an unhandled
    # NumericValueOutOfRangeError — a 500 where the contract says 422. The
    # ceiling is deliberately far past any real garment: no sleeve is 1 m off.
    magnitude_cm: float | None = Field(default=None, ge=0, le=100.0, allow_inf_nan=False)
    use_case: _UseCaseField | None = None
    raw_feedback_text: str | None = Field(default=None, max_length=2000)


class FitSignalResponse(BaseModel):
    """Single ``fit_signal`` row in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owned_garment_id: UUID
    dimension: str
    verdict: Verdict
    magnitude_cm: float | None
    use_case: str | None
    source: FitSignalSource
    raw_feedback_text: str
    created_at: datetime
