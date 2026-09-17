"""Round-trip tests for every Phase 1 Pydantic schema.

Acceptance criterion from TKT-P1-04: ``serialize → JSON → deserialize``
for one example of every schema. Each test builds a fully-populated
instance, dumps it via ``model_dump_json()``, parses the JSON via
``model_validate_json()``, and asserts equality.

Equality holds because Pydantic v2's ``BaseModel.__eq__`` compares
declared fields. ``datetime`` values use timezone-aware UTC throughout so
ISO serialization round-trips exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from api.main import create_app
from api.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    SignupRequest,
    TokenPair,
)
from api.schemas.closet import (
    FitSignalCreate,
    FitSignalResponse,
    MeasurementValue,
    OwnedGarmentCreate,
    OwnedGarmentListResponse,
    OwnedGarmentResponse,
    OwnedGarmentUpdate,
)
from api.schemas.enums import (
    FitSignalSource,
    Outcome,
    OverallRating,
    PreferredUnits,
    ProfileMaturity,
    StatedFitPreference,
    StretchLevel,
    Verdict,
)
from api.schemas.fit_profile import DimensionPreference, FitProfileResponse
from api.schemas.me import (
    ExportResponse,
    OwnedGarmentExport,
    RecommendationExport,
    UserExport,
)


def round_trip(instance: BaseModel) -> None:
    """Serialize through JSON and assert deep equality on the rebuilt model.

    This is the canonical wire-contract assertion: anything that survives
    ``json.dumps`` / ``json.loads`` is safe to put in an HTTP response.
    """
    rebuilt = instance.__class__.model_validate_json(instance.model_dump_json())
    assert rebuilt == instance


# ---------------------------------------------------------------------------
# enums.py — sanity check that every member serializes as its string value.
# Not a "schema" round-trip but worth covering since downstream schemas
# embed these by reference.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "enum_cls, sample_member, expected_value",
    [
        (Verdict, Verdict.SLIGHTLY_SHORT, "slightly_short"),
        (FitSignalSource, FitSignalSource.NLP_EXTRACTED, "nlp_extracted"),
        (StretchLevel, StretchLevel.MODERATE, "moderate"),
        (OverallRating, OverallRating.LOVE, "love"),
        (ProfileMaturity, ProfileMaturity.COLD_START, "cold_start"),
        (PreferredUnits, PreferredUnits.CM, "cm"),
        (StatedFitPreference, StatedFitPreference.SLIM, "slim"),
        (Outcome, Outcome.PENDING, "pending"),
    ],
)
def test_enum_member_matches_db_value(
    enum_cls: type, sample_member: object, expected_value: str
) -> None:
    # The DB-side ENUM types and TEXT-CHECK constants use the lowercase
    # string values; the Python member is the uppercase identifier.
    assert sample_member.value == expected_value  # type: ignore[attr-defined]
    # Round-trip through the enum constructor proves the lookup-by-value
    # path the Pydantic deserializer relies on.
    assert enum_cls(expected_value) is sample_member


# ---------------------------------------------------------------------------
# closet.py — MeasurementValue + owned garment + fit signal
# ---------------------------------------------------------------------------


def _measurements() -> dict[str, MeasurementValue]:
    return {
        "chest": MeasurementValue(value=54.0, unit="cm", source="manual_tape"),
        "body_length": MeasurementValue(value=71.0, unit="cm", source="manual_tape"),
    }


def test_measurement_value_round_trip() -> None:
    round_trip(MeasurementValue(value=54.0, unit="cm", source="manual_tape"))


def test_measurement_value_rejects_non_cm_unit() -> None:
    # Wire-contract invariant: stored measurements are cm-only (CLAUDE.md).
    with pytest.raises(ValidationError):
        MeasurementValue(value=54.0, unit="in", source="manual_tape")  # type: ignore[arg-type]


def test_measurement_value_rejects_unknown_source() -> None:
    # ``imported`` / ``cv_assisted`` reserved for forward-compat (PRD §A.9);
    # anything else is a typo.
    with pytest.raises(ValidationError):
        MeasurementValue(value=54.0, unit="cm", source="ai_guessed")  # type: ignore[arg-type]


def test_owned_garment_create_round_trip() -> None:
    round_trip(
        OwnedGarmentCreate(
            category_id="mens_button_down_shirt",
            brand="J.Crew",
            product_name="Bowery Stretch Oxford",
            size_label="M",
            measurements=_measurements(),
            fabric_composition="98% cotton, 2% elastane",
            stretch_level=StretchLevel.SLIGHT,
            overall_rating=OverallRating.LOVE,
            use_cases=["casual", "work"],
        )
    )


def test_owned_garment_update_round_trip_partial() -> None:
    """PATCH semantics — only some fields populated.

    Round-tripped with ``exclude_unset`` because that is the actual wire
    format for a partial update. A full ``model_dump_json`` writes every
    omitted field as an explicit ``null``, which is a different request:
    ``{"brand": null}`` asks to clear a NOT NULL column and is now a 422.
    """
    partial = OwnedGarmentUpdate(overall_rating=OverallRating.LIKE, size_label="L")

    rebuilt = OwnedGarmentUpdate.model_validate_json(partial.model_dump_json(exclude_unset=True))

    assert rebuilt == partial
    assert rebuilt.model_dump(exclude_unset=True) == {
        "overall_rating": OverallRating.LIKE,
        "size_label": "L",
    }


def test_owned_garment_update_rejects_clearing_a_not_null_column() -> None:
    """``{"measurements": null}`` used to store ``'null'::jsonb`` — SQLAlchemy
    maps Python ``None`` onto JSON null rather than SQL NULL, so the NOT NULL
    constraint never fired and every subsequent read of that garment raised."""
    for field in ("brand", "size_label", "measurements", "use_cases"):
        with pytest.raises(ValidationError, match="cannot be cleared"):
            OwnedGarmentUpdate(**{field: None})


def test_owned_garment_update_still_allows_clearing_optional_fields() -> None:
    cleared = OwnedGarmentUpdate(product_name=None, overall_rating=None)

    assert cleared.model_dump(exclude_unset=True) == {
        "product_name": None,
        "overall_rating": None,
    }


def test_owned_garment_response_round_trip() -> None:
    round_trip(
        OwnedGarmentResponse(
            id=uuid4(),
            user_id=uuid4(),
            category_id="mens_button_down_shirt",
            brand="Uniqlo",
            product_name="Oxford",
            size_label="M",
            measurements=_measurements(),
            fabric_composition=None,
            stretch_level=None,
            overall_rating=OverallRating.LOVE,
            use_cases=["casual"],
            created_at=datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
        )
    )


def test_owned_garment_list_response_round_trip() -> None:
    item = OwnedGarmentResponse(
        id=uuid4(),
        user_id=uuid4(),
        category_id="mens_button_down_shirt",
        brand="Uniqlo",
        product_name="Oxford",
        size_label="M",
        measurements=_measurements(),
        created_at=datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
    )
    round_trip(OwnedGarmentListResponse(items=[item]))


def test_fit_signal_create_round_trip() -> None:
    round_trip(
        FitSignalCreate(
            dimension="chest",
            verdict=Verdict.SLIGHTLY_TIGHT,
            magnitude_cm=1.5,
            use_case="layering",
            raw_feedback_text="Chest is tight under a sweater",
        )
    )


def test_fit_signal_response_round_trip() -> None:
    round_trip(
        FitSignalResponse(
            id=uuid4(),
            owned_garment_id=uuid4(),
            dimension="chest",
            verdict=Verdict.PREFERRED,
            magnitude_cm=None,
            use_case=None,
            source=FitSignalSource.NLP_EXTRACTED,
            raw_feedback_text="The chest fits great.",
            created_at=datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
        )
    )


# ---------------------------------------------------------------------------
# auth.py
# ---------------------------------------------------------------------------


def test_signup_request_round_trip() -> None:
    round_trip(
        SignupRequest(
            email="alice@example.com",
            password="Correct-Horse-Battery-Staple",
            privacy_consent_accepted_at=datetime(2026, 5, 26, tzinfo=UTC),
            stated_fit_preference=StatedFitPreference.SLIM,
        )
    )


def test_signup_request_rejects_short_password() -> None:
    # PRD §11: passwords ≥ 10 characters. Schema-level enforcement.
    with pytest.raises(ValidationError):
        SignupRequest(
            email="alice@example.com",
            password="Sh0rt!",
            privacy_consent_accepted_at=datetime(2026, 5, 26, tzinfo=UTC),
        )


def test_signup_request_rejects_single_class_password() -> None:
    # TKT-P1-07: long is not enough on its own; the password must mix
    # character classes.
    with pytest.raises(ValidationError):
        SignupRequest(
            email="alice@example.com",
            password="correcthorsebatterystaple",
            privacy_consent_accepted_at=datetime(2026, 5, 26, tzinfo=UTC),
        )


def test_signup_request_normalizes_naive_consent_timestamp_to_utc() -> None:
    # The column is TIMESTAMPTZ; a client that omits the offset gets read
    # as UTC rather than losing its consent record to a 422.
    request = SignupRequest(
        email="alice@example.com",
        password="Correct-Horse-Battery-Staple",
        privacy_consent_accepted_at=datetime(2026, 5, 26, 12, 0),  # noqa: DTZ001
    )

    assert request.privacy_consent_accepted_at == datetime(2026, 5, 26, 12, 0, tzinfo=UTC)


def test_login_request_accepts_a_password_that_would_fail_signup_policy() -> None:
    # Login must not enforce the new-password policy: a stale or simply
    # wrong password belongs in a 401 from the credential check, not a 422
    # from the schema (see api/schemas/auth.py).
    assert LoginRequest(email="alice@example.com", password="x").password == "x"


def test_login_request_round_trip() -> None:
    round_trip(LoginRequest(email="alice@example.com", password="hunter2hunter"))


def test_refresh_request_round_trip() -> None:
    round_trip(RefreshRequest(refresh_token="opaque.refresh.token"))


def test_token_pair_round_trip() -> None:
    round_trip(
        TokenPair(
            access_token="opaque.access.jwt",
            refresh_token="opaque.refresh.jwt",
            access_expires_in=900,
            refresh_expires_in=2592000,
        )
    )


# ---------------------------------------------------------------------------
# fit_profile.py
# ---------------------------------------------------------------------------


def test_dimension_preference_round_trip() -> None:
    round_trip(DimensionPreference(preferred_cm=54.0, spread_cm=1.2, sample_size=8))


def test_fit_profile_response_cold_start_round_trip() -> None:
    # Empty closet — the TKT-P1-13 cold-start branch.
    round_trip(
        FitProfileResponse(
            category_id="mens_button_down_shirt",
            maturity=ProfileMaturity.COLD_START,
            sample_size=0,
            hint="Add 3 shirts to your closet to get recommendations.",
        )
    )


def test_fit_profile_response_populated_round_trip() -> None:
    round_trip(
        FitProfileResponse(
            category_id="mens_button_down_shirt",
            maturity=ProfileMaturity.DEVELOPING,
            sample_size=5,
            dimensions={
                "chest": DimensionPreference(preferred_cm=54.0, spread_cm=1.0, sample_size=5),
                "sleeve_length": DimensionPreference(
                    preferred_cm=62.0, spread_cm=1.5, sample_size=5
                ),
            },
            use_case_variants={
                "layering": {
                    "sleeve_length": DimensionPreference(
                        preferred_cm=63.5, spread_cm=1.5, sample_size=2
                    ),
                },
            },
        )
    )


# ---------------------------------------------------------------------------
# me.py
# ---------------------------------------------------------------------------


def test_user_export_round_trip() -> None:
    round_trip(
        UserExport(
            id=uuid4(),
            email="alice@example.com",
            created_at=datetime(2026, 5, 26, tzinfo=UTC),
            privacy_consent_accepted_at=datetime(2026, 5, 26, tzinfo=UTC),
            preferred_units=PreferredUnits.CM,
            stated_fit_preference=StatedFitPreference.REGULAR,
            device_push_token="apns-token-abc123",
        )
    )


def test_recommendation_export_round_trip() -> None:
    round_trip(
        RecommendationExport(
            id=uuid4(),
            brand_product_id=uuid4(),
            recommended_size="M",
            confidence=Decimal("0.870"),
            fit_notes={"chest": {"delta_cm": -1.0, "narrative": "..."}},
            reference_garment_ids=[uuid4(), uuid4()],
            use_case_assumed="casual",
            outcome=Outcome.PENDING,
            outcome_confirmed_at=None,
            prompt_version="manual-v0",
            created_at=datetime(2026, 5, 26, tzinfo=UTC),
        )
    )


def test_export_response_round_trip() -> None:
    user = UserExport(
        id=uuid4(),
        email="alice@example.com",
        created_at=datetime(2026, 5, 26, tzinfo=UTC),
        privacy_consent_accepted_at=datetime(2026, 5, 26, tzinfo=UTC),
        preferred_units=PreferredUnits.CM,
        stated_fit_preference=None,
        device_push_token=None,
    )
    garment = OwnedGarmentExport(
        id=uuid4(),
        user_id=user.id,
        category_id="mens_button_down_shirt",
        brand="Uniqlo",
        product_name="Oxford",
        size_label="M",
        measurements=_measurements(),
        created_at=datetime(2026, 5, 26, tzinfo=UTC),
        deleted_at=None,
    )
    signal = FitSignalResponse(
        id=uuid4(),
        owned_garment_id=garment.id,
        dimension="chest",
        verdict=Verdict.PREFERRED,
        magnitude_cm=None,
        use_case=None,
        source=FitSignalSource.USER_ADDED,
        raw_feedback_text="User-added: chest preferred",
        created_at=datetime(2026, 5, 26, tzinfo=UTC),
    )
    recommendation = RecommendationExport(
        id=uuid4(),
        brand_product_id=uuid4(),
        recommended_size="M",
        confidence=Decimal("0.850"),
        fit_notes={},
        reference_garment_ids=[],
        use_case_assumed=None,
        outcome=Outcome.PENDING,
        outcome_confirmed_at=None,
        prompt_version="manual-v0",
        created_at=datetime(2026, 5, 26, tzinfo=UTC),
    )

    round_trip(
        ExportResponse(
            exported_at=datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
            user=user,
            closet=[garment],
            signals=[signal],
            recommendations=[recommendation],
        )
    )


@pytest.mark.parametrize(
    ("model", "valid", "unknown_field"),
    [
        (
            SignupRequest,
            {
                "email": "alice@example.com",
                "password": "Sizeify-Pass-1",
                "privacy_consent_accepted_at": "2026-09-17T00:00:00Z",
            },
            "stated_fit_pref",  # typo of stated_fit_preference
        ),
        (
            LoginRequest,
            {"email": "alice@example.com", "password": "Sizeify-Pass-1"},
            "scope",
        ),
        (RefreshRequest, {"refresh_token": "a.b.c"}, "user_id"),
    ],
)
def test_auth_request_bodies_reject_unknown_fields(
    model: type[BaseModel], valid: dict[str, Any], unknown_field: str
) -> None:
    """Auth bodies forbid extras, like every other request body.

    The closet and ``/me`` bodies all set ``extra="forbid"``; these three did
    not, so an unknown key was a 422 on every closet verb and silently dropped
    on every auth verb. Concretely, ``POST /auth/signup`` with a mistyped
    ``stated_fit_pref`` returned 202 and discarded the user's onboarding
    answer — the exact failure the closet bodies added the guard to prevent.
    """
    assert model.model_validate(valid) is not None

    with pytest.raises(ValidationError) as exc:
        model.model_validate({**valid, unknown_field: "x"})

    assert any(unknown_field in str(error["loc"]) for error in exc.value.errors())


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/closet/garments", "get"),
        ("/closet/garments", "post"),
        ("/closet/garments/{garment_id}", "patch"),
        ("/closet/garments/{garment_id}", "delete"),
        ("/closet/garments/{garment_id}/fit-signals", "post"),
        ("/closet/fit-profile", "get"),
        ("/me/export", "get"),
        ("/me", "delete"),
    ],
)
def test_rate_limited_routes_declare_429(path: str, method: str) -> None:
    """Every path behind a ``RateLimitMiddleware`` prefix must publish its 429.

    The limiter is middleware, so FastAPI cannot infer the response — it has
    to be declared. Only the three ``/auth/*`` paths were, even though a
    second limiter covers ``/closet/*`` and ``/me``, so a client generated
    from this spec had no Retry-After handling on the closet write path.
    """
    spec = create_app().openapi()

    assert "429" in spec["paths"][path][method]["responses"]


def test_error_responses_publish_a_body_schema() -> None:
    """A declared status that returns a body must say so.

    ``_NOT_FOUND_RESPONSE`` carried a description but no ``model``, so the 404
    published no schema while 401 and 429 both published ``ErrorDetail`` — a
    typed client got ``void`` for that branch and could not read the ``detail``
    the handler actually sends.
    """
    spec = create_app().openapi()
    patch_responses = spec["paths"]["/closet/garments/{garment_id}"]["patch"]["responses"]

    for code in ("401", "404", "429"):
        assert "content" in patch_responses[code], f"{code} publishes no body schema"


def test_the_closet_ceiling_is_discoverable() -> None:
    """``POST /closet/garments`` raises 409 when the closet is full; a client
    that cannot see it in the spec has no branch for it."""
    spec = create_app().openapi()

    assert "409" in spec["paths"]["/closet/garments"]["post"]["responses"]
