"""DEMO-only dimension-name normalization.

The demo fixtures use shorthand dimension names (``neck``, ``shoulder``,
``sleeve``) and size charts use a ``_cm`` suffix (``neck_cm``). The real
engine and ``dimension_weights`` use the canonical names
(``neck_circumference``, ``shoulder_width``, ``sleeve_length``). This module
maps the throwaway fixture vocabulary onto the canonical one so the *engine*
never has to know about the shorthand. THROWAWAY — deleted with the demo.
"""

from __future__ import annotations

from collections.abc import Mapping

# Shorthand / suffixed name -> canonical dimension name.
_ALIASES: Mapping[str, str] = {
    "neck": "neck_circumference",
    "neck_circumference": "neck_circumference",
    "chest": "chest",
    "shoulder": "shoulder_width",
    "shoulder_width": "shoulder_width",
    "sleeve": "sleeve_length",
    "sleeve_length": "sleeve_length",
    "body_length": "body_length",
    "cuff": "cuff_circumference",
    "cuff_circumference": "cuff_circumference",
}


def canonical_dim(name: str) -> str:
    """Map a fixture dimension key to its canonical name.

    Strips a trailing ``_cm`` (size-chart style) before aliasing. Unknown
    names pass through unchanged so a typo surfaces loudly downstream rather
    than being silently dropped.
    """
    key = name.lower()
    if key.endswith("_cm"):
        key = key[:-3]
    return _ALIASES.get(key, key)


def normalize_measurements(raw: Mapping[str, float]) -> dict[str, float]:
    """Canonicalize the keys of a {dimension: cm} mapping."""
    return {canonical_dim(k): float(v) for k, v in raw.items()}


def normalize_size_chart(
    raw: Mapping[str, Mapping[str, float]],
) -> dict[str, dict[str, float]]:
    """Canonicalize every size's measurement keys in a size chart."""
    return {size: normalize_measurements(meas) for size, meas in raw.items()}
