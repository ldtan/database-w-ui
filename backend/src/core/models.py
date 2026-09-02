from __future__ import annotations

from sqlalchemy.orm import Mapped, declared_attr, mapped_column
import sqlalchemy as sa

from src.infrastructure.database import Base
from .mixins import UUIDMixin, DatetimeTrackerMixin


class BaseModel(Base, UUIDMixin, DatetimeTrackerMixin):
    __abstract__ = True

    @declared_attr
    def id(cls) -> Mapped[int]:
        return mapped_column(sa.Integer, primary_key=True)
