"""Per-dimension importance weights used by the matching engine.

These are the canonical, hand-tuned values from PRD §6.4. They are the
single source of truth for two consumers:

* The ``garment_category`` row seeded by ``api.seeds.garment_categories``
  (stored in JSONB so categories can vary per garment type in future
  versions).
* The matching engine (TKT-P1-14), which multiplies per-dimension
  distances by the corresponding weight and sums.

Adding or renaming a dimension requires updating **all four** of:
``garment_category.measurement_schema`` (the seed), this module, the NLP
extraction prompt, and the matching engine — see CLAUDE.md "Workflow rules".

Weights MUST sum to 1.0 (validated by unit test) so the weighted distance
stays in a comparable range across categories.
"""

from collections.abc import Mapping
from types import MappingProxyType

# PRD §6.4 v1 weights for men's button-down shirts. Keys match the dimension
# names used in ``measurement_schema.dimensions[].name``, in
# ``fit_signal.dimension``, and in the matching engine's per-dimension delta
# computation — do not rename in one place without the others.
BUTTON_DOWN_DIMENSION_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "chest": 0.35,
        "shoulder_width": 0.20,
        "body_length": 0.15,
        "sleeve_length": 0.15,
        "neck_circumference": 0.10,
        "cuff_circumference": 0.05,
    }
)
