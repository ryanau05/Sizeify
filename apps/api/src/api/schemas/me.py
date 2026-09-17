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

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from api.schemas.auth import MAX_PASSWORD_LENGTH
from api.schemas.closet import FitSignalResponse, OwnedGarmentResponse
from api.schemas.enums import (
    Outcome,
    PreferredUnits,
    StatedFitPreference,
)


class AccountDeleteRequest(BaseModel):
    """``DELETE /me`` body (PRD §11).

    Erasure is the one irreversible operation in the API, so it is gated on
    something the caller knows rather than only on something they hold. A
    15-minute access token that leaked, or a handset left unlocked, is enough
    to reach every other endpoint; it should not be enough to destroy the
    account.

    Password rather than a re-issued token because the point is to prove the
    *person* is present, not that the session is fresh.
    """

    model_config = ConfigDict(extra="forbid")

    # No complexity rules here, the same as login: this is a credential check
    # whose only correct failure is 401. The bound is imported rather than
    # repeated — the literal 256 here and in ``schemas.auth`` were the same
    # number by coincidence, so raising one would have left the other behind.
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class UserExport(BaseModel):
    """User-profile section of the export.

    No ``password_hash`` — a credential is not "the user's data" in any
    useful sense, and shipping one in a downloadable file is a liability.
    The refresh-token chain is out for the same reason.

    ``device_push_token`` *is* included. The earlier reasoning was that it
    identifies a handset, is OS-rotated, and is meaningless outside our own
    push pipeline — all true, and none of it the test Art. 15 applies. A
    device identifier the controller stores against a named user is personal
    data whether or not it is portable.

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
    device_push_token: str | None


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
