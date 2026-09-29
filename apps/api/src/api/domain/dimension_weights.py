"""Per-dimension importance weights used by the matching engine.

These are the canonical, hand-tuned values from PRD §6.4. They are the
single source of truth for two consumers:

* The ``garment_category`` row seeded by ``api.seeds.garment_categories``
  (stored in JSONB so categories can vary per garment type in future
  versions).
* The matching engine (TKT-P1-14), which multiplies per-dimension
  distances by the corresponding weight and sums.

Adding or renaming a dimension requires updating **all five** of:
``garment_category.measurement_schema`` (the seed), this module, the NLP
extraction prompt, the matching engine, and ``_DIM_LABEL`` in
``domain/recommendation.py`` — see CLAUDE.md "Workflow rules".

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


#: Weights by ``garment_category.id``. v1 seeds exactly one category, so this
#: has one entry — but the lookup is real rather than a constant return, which
#: is what makes "the weights depend on the category" true of the code and not
#: just of the comment above.
DIMENSION_WEIGHTS_BY_CATEGORY: Mapping[str, Mapping[str, float]] = MappingProxyType(
    {"mens_button_down_shirt": BUTTON_DOWN_DIMENSION_WEIGHTS}
)


class UnknownCategoryError(LookupError):
    """No weights are tuned for this ``category_id``.

    Raised rather than falling back to the button-down table. A silent
    fallback would rank a garment of one category against another's
    priorities and still return a confident-looking size — the exact
    silent degradation CLAUDE.md's five-place rule exists to prevent.
    """

    def __init__(self, category_id: str) -> None:
        super().__init__(
            f"no dimension weights tuned for category {category_id!r}; "
            f"known: {sorted(DIMENSION_WEIGHTS_BY_CATEGORY)}"
        )
        self.category_id = category_id


def weights_for_category(category_id: str) -> Mapping[str, float]:
    """The tuned weights for ``category_id``, or raise ``UnknownCategoryError``."""
    try:
        return DIMENSION_WEIGHTS_BY_CATEGORY[category_id]
    except KeyError as exc:
        raise UnknownCategoryError(category_id) from exc
