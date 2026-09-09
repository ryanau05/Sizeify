"""Request/response schemas for ``/auth/{signup,login,refresh}``.

The auth flow itself lives in TKT-P1-07; the schemas land here so route
handlers in later tickets can wire up against a frozen contract.

PRD §11 invariants enforced at the schema layer:

* Signup captures a ``privacy_consent_accepted_at`` timestamp — GDPR/CCPA
  day-one requirement (PRD §11).
* Passwords are at least 10 characters (PRD §11 + TKT-P1-07).
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from api.schemas.enums import StatedFitPreference

# Min length enforced at the schema layer; argon2-based hashing (TKT-P1-05)
# enforces no upper bound. Mixed-class enforcement (per TKT-P1-07) layers
# on top via a validator in the route handler so signup-time errors carry
# a useful per-rule message.
_PasswordField = Annotated[str, Field(min_length=10, max_length=256)]


class SignupRequest(BaseModel):
    """``POST /auth/signup`` request body (TKT-P1-07).

    ``stated_fit_preference`` is optional at signup so the onboarding
    flow (PRD §10.1) can capture it on the next screen — the API contract
    just requires *eventual* capture, not signup-time capture.
    """

    email: EmailStr
    password: _PasswordField
    # GDPR/CCPA: explicit consent timestamp, recorded server-side as well
    # so a missing client clock doesn't silently degrade the compliance
    # trail.
    privacy_consent_accepted_at: datetime
    stated_fit_preference: StatedFitPreference | None = None


class LoginRequest(BaseModel):
    """``POST /auth/login`` request body."""

    email: EmailStr
    password: _PasswordField


class RefreshRequest(BaseModel):
    """``POST /auth/refresh`` request body.

    The refresh token is single-use (TKT-P1-06): reusing a rotated token
    returns 401 and revokes the user's entire refresh-token chain.
    """

    refresh_token: str = Field(min_length=1)


class TokenPair(BaseModel):
    """Response for every auth endpoint — short-lived access + long-lived
    refresh.

    ``access_expires_in`` / ``refresh_expires_in`` are seconds (matching
    the int-second TTLs in ``api.config``); the client decides how to
    schedule its rotation cadence around them.
    """

    model_config = ConfigDict(extra="forbid")

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    access_expires_in: int
    refresh_expires_in: int
