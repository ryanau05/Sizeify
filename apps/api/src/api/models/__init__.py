from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared SQLAlchemy declarative base.

    Every ORM entity inherits from this so they register on one
    ``MetaData``. Alembic diffs ``Base.metadata`` (see ``alembic/env.py``)
    to autogenerate migrations, so any entity module that is not imported
    below is invisible to autogenerate.
    """


# Import every entity module here so its table registers on Base.metadata
# before Alembic reads target_metadata. Entities land in TKT-P1-01.
__all__ = ["Base"]
