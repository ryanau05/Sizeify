"""Unit tests for fit-profile construction (DEMO-02 / TKT-P1-12)."""

from __future__ import annotations

from api.domain.fit_profile import (
    ClosetSnapshot,
    GarmentSnapshot,
    SignalSnapshot,
    build_fit_profile,
)
from api.schemas.enums import ProfileMaturity, StretchLevel, Verdict


def _garment(label, chest, **kw):
    return GarmentSnapshot(
        label=label,
        brand=kw.get("brand", "brand"),
        size_label=kw.get("size_label", "M"),
        measurements_cm={"chest": chest, "shoulder_width": kw.get("shoulder", 45.0)},
        stretch_level=kw.get("stretch_level"),
        overall_rating=kw.get("overall_rating"),
        signals=kw.get("signals", ()),
    )


def test_empty_closet_is_cold_start():
    profile = build_fit_profile(ClosetSnapshot("mens_button_down_shirt", []))
    assert profile.maturity is ProfileMaturity.COLD_START
    assert profile.dimensions == {}
    resp = profile.to_response()
    assert resp.maturity is ProfileMaturity.COLD_START
    assert resp.hint is not None  # cold-start banner populated


def test_five_garments_is_developing():
    closet = ClosetSnapshot(
        "mens_button_down_shirt",
        [_garment(f"g{i}", 105.0 + i) for i in range(5)],
    )
    profile = build_fit_profile(closet)
    assert profile.maturity is ProfileMaturity.DEVELOPING
    assert profile.sample_size == 5
    assert "chest" in profile.dimensions
    assert profile.dimensions["chest"].spread_cm >= 1.0


def test_chest_pulled_up_by_slightly_tight_signal():
    # Same closet twice; the only difference is one garment's chest verdict.
    def closet_with(verdict):
        return ClosetSnapshot(
            "mens_button_down_shirt",
            [
                _garment("a", 105.0),
                _garment("b", 107.0),
                _garment(
                    "c",
                    103.0,
                    signals=(SignalSnapshot("chest", verdict),),
                ),
            ],
        )

    pref_tight = build_fit_profile(closet_with(Verdict.SLIGHTLY_TIGHT)).dimensions["chest"].preferred_cm
    pref_ok = build_fit_profile(closet_with(Verdict.PREFERRED)).dimensions["chest"].preferred_cm
    # A "slightly tight" garment means the user wants MORE room → preferred rises.
    assert pref_tight > pref_ok


def test_stretch_raises_effective_measurement():
    no_stretch = build_fit_profile(
        ClosetSnapshot("c", [_garment("a", 105.0, stretch_level=StretchLevel.NONE)])
    ).dimensions["chest"].preferred_cm
    high_stretch = build_fit_profile(
        ClosetSnapshot("c", [_garment("a", 105.0, stretch_level=StretchLevel.HIGH)])
    ).dimensions["chest"].preferred_cm
    assert high_stretch > no_stretch  # +3.5 cm effective offset


def test_use_case_tagged_signal_excluded_from_unconditioned_profile():
    base = build_fit_profile(
        ClosetSnapshot("c", [_garment("a", 105.0)])
    ).dimensions["chest"].preferred_cm
    with_gym_sig = build_fit_profile(
        ClosetSnapshot(
            "c",
            [_garment("a", 105.0, signals=(SignalSnapshot("chest", Verdict.SLIGHTLY_TIGHT, use_case="gym"),))],
        )
    ).dimensions["chest"].preferred_cm
    assert with_gym_sig == base  # gym-tagged signal does not move the default profile
