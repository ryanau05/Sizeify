"""Response schema for ``GET /me/export`` (TKT-P1-17, PRD §11).

The export is a full GDPR/CCPA data dump. Closet and signal payloads
reuse the response schemas from ``api.schemas.closet`` so the wire shape
is consistent with read endpoints — a user importing their export
elsewhere sees the same JSON they saw in-app.

The export deliberately excludes:

* Server-side secrets (password hash, refresh-token chain).
* Shared catalog tables (``brand_product``, ``garment_category``) —
  those aren't the user's data (PRD §11 minimization principle).

``DELETE /me`` has no body so no schema lives here for it (TKT-P1-18).
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr

from api.schemas.closet import FitSignalResponse, OwnedGarmentResponse
from api.schemas.enums import (
    Outcome,
    PreferredUnits,
    StatedFitPreference,
)


class UserExport(BaseModel):
    """User-profile section of the export.

    No ``password_hash``, no ``device_push_token`` — neither belongs in a
    "the user's data" dump.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    created_at: datetime
    preferred_units: PreferredUnits
    stated_fit_preference: StatedFitPreference | None


class RecommendationExport(BaseModel):
    """One ``recommendation`` row in the export.

    No standalone recommendation-response schema exists yet (Phase 6
    territory), so this doubles as the read shape if any Phase 1 endpoint
    needs to surface a stored recommendation. ``prompt_version`` is
    included so an exported recommendation remains interpretable across
    LLM-prompt migrations.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    brand_product_id: UUID
    recommended_size: str
    confidence: Decimal
    fit_notes: dict[str, object]
    reference_garment_ids: list[UUID]
    use_case_assumed: str | None
    outcome: Outcome
    outcome_confirmed_at: datetime | None
    prompt_version: str
    created_at: datetime


class ExportResponse(BaseModel):
    """Top-level GDPR/CCPA export payload.

    Versioned via ``export_schema_version`` so future shape changes can
    be detected on the consumer side without breaking importers.
    """

    export_schema_version: Literal["1"] = "1"
    exported_at: datetime
    user: UserExport
    closet: list[OwnedGarmentResponse]
    signals: list[FitSignalResponse]
    recommendations: list[RecommendationExport]
