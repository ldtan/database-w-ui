from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, mapped_column, relationship
import sqlalchemy as sa

from src.core.models import BaseModel

# Avoid circular import for type hints
if TYPE_CHECKING:
    from ..users.models import User


class RefreshToken(BaseModel):
    __tablename__ = "refresh_tokens"

    token: Mapped[str] = mapped_column(sa.String(256), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(sa.Integer, sa.ForeignKey("users.id"))
    user: Mapped["User"] = relationship("User")
    expires_at: Mapped[sa.DateTime] = mapped_column(sa.DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(sa.Boolean, default=False)
