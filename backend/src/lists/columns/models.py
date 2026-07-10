from __future__ import annotations
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
import sqlalchemy as sa

from src.core.models import BaseModel
from .enums import ColumnType

if TYPE_CHECKING:
    from ..models import List


class Column(BaseModel):
    __tablename__ = "columns"

    list_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("lists.id"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(120))
    display_name: Mapped[str] = mapped_column(sa.String(255))
    type: Mapped[ColumnType] = mapped_column(sa.Enum(ColumnType, name="column_type"))
    position: Mapped[int] = mapped_column(sa.Integer, default=0)
    # Type-specific settings (options, precision, target list, required, ...).
    config: Mapped[dict] = mapped_column(JSONB, default=dict)

    list: Mapped["List"] = relationship(
        "List", back_populates="columns", foreign_keys=[list_id]
    )

    __table_args__ = (
        sa.Index(
            "uq_columns_list_name",
            "list_id",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
