"""Request/response schemas for ``/closet/garments`` and its nested
``/fit-signals`` sub-resource.

PRD §A.9 forward-compat lives here: every measurement carries an explicit
``source`` so the CV-assist iteration can ship the same wire format
without a breaking change. ``unit`` is fixed to ``"cm"`` — display-unit
conversion happens at the UI boundary (CLAUDE.md).
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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

    value: float = Field(gt=0)
    unit: Literal["cm"] = "cm"
    source: Literal["manual_tape", "cv_assisted", "imported"]


# Keys are dimension names from ``garment_category.measurement_schema``
# (``chest``, ``body_length``, …). Validation against the category
# schema lives in the route handler (TKT-P1-09) because the schema is
# a dynamic per-category JSONB blob.
GarmentMeasurements = dict[str, MeasurementValue]


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
    use_cases: list[_UseCaseField] = Field(default_factory=list)


class OwnedGarmentCreate(_OwnedGarmentBase):
    """``POST /closet/garments`` body (TKT-P1-09).

    ``category_id`` is the garment_category slug (e.g.
    ``"mens_button_down_shirt"``). v1 only accepts the seeded slug; the
    route validates against ``garment_category`` before insert.
    """

    category_id: str = Field(min_length=1, max_length=100)


class OwnedGarmentUpdate(BaseModel):
    """``PATCH /closet/garments/{id}`` body (TKT-P1-09).

    All fields optional for partial-update semantics. ``category_id`` is
    intentionally omitted — re-categorizing an existing garment would
    invalidate its fit signals (different dimension set), so the client
    must delete-and-recreate instead.
    """

    model_config = ConfigDict(extra="forbid")

    brand: _BrandField | None = None
    product_name: _ProductNameField | None = None
    size_label: _SizeLabelField | None = None
    measurements: GarmentMeasurements | None = None
    fabric_composition: _FabricCompositionField | None = None
    stretch_level: StretchLevel | None = None
    overall_rating: OverallRating | None = None
    use_cases: list[_UseCaseField] | None = None


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
    """

    dimension: str = Field(min_length=1, max_length=100)
    verdict: Verdict
    magnitude_cm: float | None = Field(default=None, ge=0)
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
