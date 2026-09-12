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

    No ``password_hash`` — a credential is not "the user's data" in any
    useful sense, and shipping one in a downloadable file is a liability.
    No ``device_push_token`` either: it identifies a handset, is rotated by
    the OS, and is meaningless outside our own push pipeline.

    ``privacy_consent_accepted_at`` *is* included. It is personal data the
    controller holds about the user, and GDPR Art. 15 covers it — a user
    asking what we know about them is entitled to see the consent record
    we are relying on.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    created_at: datetime
    privacy_consent_accepted_at: datetime
    preferred_units: PreferredUnits
    stated_fit_preference: StatedFitPreference | None


class OwnedGarmentExport(OwnedGarmentResponse):
    """A closet row as it appears in the export.

    Identical to the read-endpoint shape plus ``deleted_at``. The export
    includes soft-deleted garments, because they are still stored and still
    processed (historic recommendations cite them) — so Art. 15 covers
    them too. Without the extra field a deleted garment would appear in the
    dump indistinguishable from a live one, which would make the export
    actively misleading rather than merely incomplete.
    """

    deleted_at: datetime | None


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
    closet: list[OwnedGarmentExport]
    signals: list[FitSignalResponse]
    recommendations: list[RecommendationExport]
