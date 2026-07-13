from datetime import datetime, timezone
from typing import Optional
import uuid

from sqlalchemy.orm import Mapped, declared_attr, mapped_column
import sqlalchemy as sa


class UUIDMixin:
    @declared_attr
    def uuid(cls) -> Mapped[uuid.UUID]:
        return mapped_column(sa.Uuid, unique=True, default=uuid.uuid7)


class DatetimeTrackerMixin:
    @declared_attr
    def created_at(cls) -> Mapped[sa.DateTime]:
        return mapped_column(
            sa.DateTime(timezone=True),
            default=lambda: datetime.now(timezone.utc),
        )

    @declared_attr
    def updated_at(cls) -> Mapped[sa.DateTime]:
        return mapped_column(
            sa.DateTime(timezone=True),
            default=lambda: datetime.now(timezone.utc),
            onupdate=lambda: datetime.now(timezone.utc),
        )

    @declared_attr
    def deleted_at(cls) -> Mapped[Optional[sa.DateTime]]:
        return mapped_column(sa.DateTime(timezone=True), nullable=True)

    @property
    def is_deleted(self) -> bool:
        """In the bin. A read schema can expose this without leaking *when* it went,
        which is nobody's business but the purge job's."""
        return self.deleted_at is not None
