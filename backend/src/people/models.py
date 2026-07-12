from datetime import date
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.models import BaseModel

if TYPE_CHECKING:
    from src.auth.users.models import User


class Person(BaseModel):
    """A user's personal details, kept off the account row itself.

    One profile per user at most, and every profile belongs to a user — but an
    account can exist without one, so this is optional from the User side.
    """

    __tablename__ = "people"

    user_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("users.id"), nullable=False
    )
    first_name: Mapped[str] = mapped_column(sa.String(100))
    middle_name: Mapped[Optional[str]] = mapped_column(sa.String(100), nullable=True)
    last_name: Mapped[str] = mapped_column(sa.String(100))
    birth_date: Mapped[Optional[date]] = mapped_column(sa.Date, nullable=True)

    user: Mapped["User"] = relationship("User")

    __table_args__ = (
        # One profile per user. Partial so a soft-deleted profile does not block
        # creating a new one — every other unique in this schema works the same way.
        sa.Index(
            "uq_people_user",
            "user_id",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
