"""Request/response schemas for ``/auth/{signup,login,refresh}``.

Routes in ``api.routes.auth`` (TKT-P1-07) wire up against these.

PRD §11 invariants enforced at the schema layer:

* Signup captures a ``privacy_consent_accepted_at`` timestamp — GDPR/CCPA
  day-one requirement (PRD §11).
* Signup passwords are at least 10 characters and draw on at least
  ``MIN_PASSWORD_CLASSES`` character classes (PRD §11 + TKT-P1-07).
* Emails are RFC-validated via ``EmailStr`` (``email-validator``, which
  ships with ``fastapi[standard]``).
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)

from api.schemas.enums import StatedFitPreference

MIN_PASSWORD_LENGTH = 10

# Character classes a *new* password must draw on: lowercase, uppercase,
# digit, other (symbols, punctuation, whitespace, non-ASCII). Three of the
# four is the conventional "password complexity" bar and is what TKT-P1-07
# means by "mixed classes". Kept as one named constant so tightening or
# relaxing the rule is a one-line change with one place to re-document.
MIN_PASSWORD_CLASSES = 3

# Upper bound is a DoS guard, not a policy: argon2 cost is independent of
# input length, but there is no reason to accept megabyte passwords.
MAX_PASSWORD_LENGTH = 256


def _character_classes(password: str) -> set[str]:
    """Which of the four classes ``password`` draws on."""
    classes: set[str] = set()
    for char in password:
        if char.islower():
            classes.add("lower")
        elif char.isupper():
            classes.add("upper")
        elif char.isdigit():
            classes.add("digit")
        else:
            classes.add("other")
    return classes


def _validate_password_complexity(password: str) -> str:
    """Enforce the mixed-class rule. Length is handled by ``Field``.

    Raises ``ValueError``, which Pydantic surfaces as a per-field 422 entry
    naming ``password`` — so the client can highlight the right input
    rather than showing a whole-form error.
    """
    found = _character_classes(password)
    if len(found) < MIN_PASSWORD_CLASSES:
        raise ValueError(
            f"password must combine at least {MIN_PASSWORD_CLASSES} of: "
            "lowercase, uppercase, digit, symbol"
        )
    return password


# New-password field: full policy. Used at signup (and by any future
# password-change / reset endpoint).
_NewPasswordField = Annotated[
    str,
    Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH),
    AfterValidator(_validate_password_complexity),
]

# Login field: deliberately policy-free beyond a length cap. A password
# that fails today's policy must produce 401 from the credential check,
# not 422 from the schema — otherwise the error code tells an attacker
# which guesses are worth making, and tightening the policy later would
# lock existing users out at the schema layer instead of prompting a
# reset.
_SubmittedPasswordField = Annotated[str, Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)]


class SignupRequest(BaseModel):
    """``POST /auth/signup`` request body (TKT-P1-07).

    ``stated_fit_preference`` is optional at signup so the onboarding
    flow (PRD §10.1) can capture it on the next screen — the API contract
    just requires *eventual* capture, not signup-time capture.
    """

    # Same guard as the closet bodies. Without it a mistyped
    # ``stated_fit_preference`` was silently discarded and signup still
    # returned 202, so the user's onboarding answer vanished.
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: _NewPasswordField
    # GDPR/CCPA: explicit consent timestamp. Required — omitting it is a
    # 422, which is the point: there is no code path that creates a user
    # without a consent record (PRD §11).
    privacy_consent_accepted_at: datetime
    stated_fit_preference: StatedFitPreference | None = None

    @field_validator("privacy_consent_accepted_at")
    @classmethod
    def _require_utc(cls, value: datetime) -> datetime:
        """Normalize the consent timestamp to tz-aware UTC.

        The column is ``TIMESTAMP WITH TIME ZONE``; handing asyncpg a naive
        datetime is an error waiting to happen. Naive input is read as UTC
        rather than rejected — a client that sends ``2026-09-09T12:00:00``
        has a clock-formatting bug, not a missing consent record, and
        failing the signup over it would lose the consent we just got.
        """
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


#: The one sentence ``POST /auth/signup`` ever returns. Module-level so the
#: tests assert against the same string the schema serves, rather than a
#: copy that can drift from it.
SIGNUP_ACCEPTED_DETAIL = (
    "If that email address is available, an account has been created. Sign in to continue."
)


class SignupAccepted(BaseModel):
    """``POST /auth/signup`` response — identical whether or not the address
    was already registered.

    Signup used to return 201 plus a token pair, which made it an
    account-existence oracle: a 409 said "this address is registered" to
    anyone who asked. Login is carefully defended against exactly that (a
    dummy argon2 verify equalizes the response time so an unknown email and a
    wrong password are indistinguishable), and signup undercut that work.

    Returning tokens and being non-enumerable are mutually exclusive: a token
    pair can only be issued for an account we just created, so its presence
    *is* the answer. Hence 202 and no tokens.

    The client follows with ``POST /auth/login`` using the same credentials.
    For a genuinely new account that succeeds immediately — the password was
    just set — so onboarding is still one tap with one extra round trip. For
    an address that was already taken the password was never applied, so login
    returns its ordinary 401 and the caller learns nothing it did not already
    know.
    """

    model_config = ConfigDict(extra="forbid")

    #: Fixed text. Anything derived from whether the account existed would
    #: reintroduce the oracle this response exists to close.
    #:
    #: A ``str`` with a default, not a single-valued ``Literal``. The Literal
    #: published as a one-member enum, so a generated client pinned this exact
    #: sentence and rewording it became a breaking schema change — for copy
    #: that is expected to change: ``_log_existing_address`` already notes the
    #: "someone tried to register your address" line that lands here once
    #: there is an email pipeline. The guarantee that matters is that the body
    #: is *constant*, which a default gives just as well, and which
    #: ``test_signup_does_not_reveal_that_an_address_is_taken`` already pins
    #: where it belongs — by comparing two responses to each other rather than
    #: either to a hard-coded sentence.
    detail: str = SIGNUP_ACCEPTED_DETAIL


class LoginRequest(BaseModel):
    """``POST /auth/login`` request body."""

    # Same guard as the closet bodies: an unexpected key is a client bug
    # worth surfacing, not swallowing.
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: _SubmittedPasswordField


class RefreshRequest(BaseModel):
    """``POST /auth/refresh`` request body.

    The refresh token is single-use (TKT-P1-06): reusing a rotated token
    returns 401 and revokes the user's entire refresh-token chain.
    """

    # Same guard as the closet bodies: an unexpected key is a client bug
    # worth surfacing, not swallowing.
    model_config = ConfigDict(extra="forbid")

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
