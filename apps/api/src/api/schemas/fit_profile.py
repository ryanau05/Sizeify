"""Response schema for ``GET /closet/fit-profile`` (TKT-P1-13).

The exact ``FitProfile`` dataclass lands in TKT-P1-12 (Bayesian
construction); this is the wire-shape it must serialize to. Keeping the
fields here forward-looking but minimal means the API contract can be
nailed down before the domain code lands.
"""

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.enums import ProfileMaturity


class DimensionPreference(BaseModel):
    """Preferred range on a single measurement dimension (PRD §6.2).

    Stored as a centre + spread (rather than a min/max pair) because the
    Bayesian update in TKT-P1-12 maintains a posterior mean and variance.
    The UI renders ranges by widening ``preferred_cm`` by ``spread_cm``.
    """

    model_config = ConfigDict(extra="forbid")

    preferred_cm: float = Field(gt=0)
    # Uncertainty estimate — one-sigma equivalent. Narrows as more
    # garments contribute (PRD §6.2: "with 15+ garments, ranges narrow").
    spread_cm: float = Field(ge=0)
    sample_size: int = Field(ge=0)


class FitProfileResponse(BaseModel):
    """``GET /closet/fit-profile`` response.

    ``dimensions`` is the unconditioned profile — the matching engine's
    default when no use case is detected from a product URL.
    ``use_case_variants`` overlays per-use-case adjustments (PRD §6.5).

    Empty closet ⇒ ``maturity == COLD_START`` with ``hint`` populated and
    both dimension maps empty — TKT-P1-13 acceptance criterion.
    """

    category_id: str = Field(min_length=1, max_length=100)
    maturity: ProfileMaturity
    sample_size: int = Field(ge=0)
    dimensions: dict[str, DimensionPreference] = Field(default_factory=dict)
    use_case_variants: dict[str, dict[str, DimensionPreference]] = Field(default_factory=dict)
    # PRD §6.2 cold-start guidance, surfaced to the UI as a banner.
    hint: str | None = None
