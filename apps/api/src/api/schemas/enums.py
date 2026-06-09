"""Shared domain enums for request/response schemas.

Values match the PostgreSQL ENUM types declared in migration 0001 and the
TEXT-with-discrete-values columns (``overall_rating``, ``outcome``,
``stated_fit_preference``, …). Adding a new value here without also
updating the migration / model is a silent bug — Pydantic will accept it
but the DB will reject the insert.

Member names follow ``UPPER_SNAKE_CASE`` Python convention; the wire /
DB value is the lowercase string assigned to each member. Inheriting from
``str`` lets Pydantic and SQLAlchemy serialize members as their string
value with no extra adaptor.
"""

from enum import StrEnum


class Verdict(StrEnum):
    """Directional fit verdict on a single dimension (PRD §6.1).

    The ``_SHORT`` analogues only apply to length-axis dimensions (sleeve
    length, body length) — the matching engine ignores them on
    circumference dimensions.
    """

    TOO_TIGHT = "too_tight"
    SLIGHTLY_TIGHT = "slightly_tight"
    PREFERRED = "preferred"
    SLIGHTLY_LOOSE = "slightly_loose"
    TOO_LOOSE = "too_loose"
    SLIGHTLY_SHORT = "slightly_short"
    TOO_SHORT = "too_short"


class FitSignalSource(StrEnum):
    """Provenance of a single ``fit_signal`` row (CLAUDE.md domain conventions)."""

    NLP_EXTRACTED = "nlp_extracted"
    USER_EDITED = "user_edited"
    USER_ADDED = "user_added"


class StretchLevel(StrEnum):
    """Coarse 4-level fabric-stretch indicator (PRD §6.3).

    PRD gotcha: do not propose finer granularity without scope approval.
    """

    NONE = "none"
    SLIGHT = "slight"
    MODERATE = "moderate"
    HIGH = "high"


class OverallRating(StrEnum):
    """User's overall feeling about a garment (PRD §5.1).

    Weights the garment's contribution to the fit profile — ``LOVE``
    anchors the preferred range; ``DISLIKE`` sets exclusion bounds.
    """

    LOVE = "love"
    LIKE = "like"
    TOLERABLE = "tolerable"
    DISLIKE = "dislike"


class ProfileMaturity(StrEnum):
    """Fit-profile confidence band (PRD §6.2).

    ``COLD_START`` triggers the empty-closet hint in
    ``FitProfileResponse``. Distinctions beyond this are TKT-P1-12's call;
    the wire format reserves the three obvious levels so the API doesn't
    need a version bump when the domain layer decides.
    """

    COLD_START = "cold_start"
    DEVELOPING = "developing"
    MATURE = "mature"


class PreferredUnits(StrEnum):
    """Display-unit preference (PRD §5.2).

    Conversion happens at the UI boundary — internal measurements are
    always cm (CLAUDE.md).
    """

    CM = "cm"
    IN = "in"


class StatedFitPreference(StrEnum):
    """Onboarding fit preference (PRD §10.1 step 3).

    Seeds the weak prior for fit-profile construction before the user has
    enough garments for the Bayesian update to dominate.
    """

    SLIM = "slim"
    REGULAR = "regular"
    RELAXED = "relaxed"


class Outcome(StrEnum):
    """Recommendation-outcome state (PRD §5.5 / §8).

    ``PENDING`` is the server-side default; the user moves it to
    ``CORRECT`` / ``INCORRECT`` via the outcome endpoint, or the
    14-day-prompt scheduler moves it to ``UNCONFIRMED`` if no answer
    arrives.
    """

    PENDING = "pending"
    CORRECT = "correct"
    INCORRECT = "incorrect"
    UNCONFIRMED = "unconfirmed"
