from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
import sqlalchemy as sa

from src.core.models import BaseModel


class ListItem(BaseModel):
    __tablename__ = "list_items"

    list_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("lists.id"), index=True
    )
    # The whole record, keyed by column uuid (string) -> value.
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    # When True, inherits permission grants from its list; when False, stands alone.
    permissions_inherit: Mapped[bool] = mapped_column(
        sa.Boolean, default=True, server_default=sa.true(), nullable=False
    )

    __table_args__ = (
        sa.Index("ix_list_items_data", "data", postgresql_using="gin"),
    )
