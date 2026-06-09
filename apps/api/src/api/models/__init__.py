from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared SQLAlchemy declarative base.

    Every ORM entity inherits from this so they register on one
    ``MetaData``. Alembic diffs ``Base.metadata`` (see ``alembic/env.py``)
    to autogenerate migrations, so any entity module that is not imported
    below is invisible to autogenerate.
    """


# Import every entity module here so its table registers on Base.metadata
# before Alembic reads target_metadata. New entities must be added below.
from api.models.brand_product import BrandProduct  # noqa: E402
from api.models.fit_signal import FitSignal  # noqa: E402
from api.models.garment_category import GarmentCategory  # noqa: E402
from api.models.owned_garment import OwnedGarment  # noqa: E402
from api.models.recommendation import Recommendation  # noqa: E402
from api.models.refresh_token import RefreshToken  # noqa: E402
from api.models.user import User  # noqa: E402

__all__ = [
    "Base",
    "BrandProduct",
    "FitSignal",
    "GarmentCategory",
    "OwnedGarment",
    "Recommendation",
    "RefreshToken",
    "User",
]
