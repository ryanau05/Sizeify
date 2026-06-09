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
from uuid import uuid4

import pytest
from pydantic import BaseModel

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
from api.schemas.me import ExportResponse, RecommendationExport, UserExport


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
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MeasurementValue(value=54.0, unit="in", source="manual_tape")  # type: ignore[arg-type]


def test_measurement_value_rejects_unknown_source() -> None:
    # ``imported`` / ``cv_assisted`` reserved for forward-compat (PRD §A.9);
    # anything else is a typo.
    from pydantic import ValidationError

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
    # PATCH semantics — only some fields populated.
    round_trip(
        OwnedGarmentUpdate(
            overall_rating=OverallRating.LIKE,
            size_label="L",
        )
    )


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
            password="correcthorsebatterystaple",
            privacy_consent_accepted_at=datetime(2026, 5, 26, tzinfo=UTC),
            stated_fit_preference=StatedFitPreference.SLIM,
        )
    )


def test_signup_request_rejects_short_password() -> None:
    # PRD §11: passwords ≥ 10 characters. Schema-level enforcement.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SignupRequest(
            email="alice@example.com",
            password="short",
            privacy_consent_accepted_at=datetime(2026, 5, 26, tzinfo=UTC),
        )


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
            preferred_units=PreferredUnits.CM,
            stated_fit_preference=StatedFitPreference.REGULAR,
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
        preferred_units=PreferredUnits.CM,
        stated_fit_preference=None,
    )
    garment = OwnedGarmentResponse(
        id=uuid4(),
        user_id=user.id,
        category_id="mens_button_down_shirt",
        brand="Uniqlo",
        product_name="Oxford",
        size_label="M",
        measurements=_measurements(),
        created_at=datetime(2026, 5, 26, tzinfo=UTC),
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
