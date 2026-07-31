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
    description: Mapped[str] = mapped_column(sa.Text, nullable=True)
    # Type-specific settings (options, precision, target list, required, ...).
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Live, but not part of the list's shape yet. A type conversion builds its
    # target column here and backfills into it, so the half-converted data is
    # invisible to every reader until the cutover swaps it in. Distinct from
    # `deleted_at`, which also hides a column but marks it for the purge job —
    # a stalled conversion must not have its work collected.
    hidden: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, server_default=sa.false(), nullable=False
    )

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


def in_shape():
    """The predicate for "this column is part of the list's shape".

    Live *and* not hidden. Every reader that answers "what fields does a record
    have" — the grid, the record service, export, metrics, forms, the primary
    column, a lookup's label — must filter on this, or a conversion's half-filled
    scratch column shows up as a field of the list.

    Two readers deliberately do **not** use it and take `deleted_at` alone: the
    reference scrub and the shape reconcile. A column being invisible does not
    exempt the uuids it holds from being cleaned up when their target is purged,
    and a conversion that runs across a nightly reconcile should come out the far
    side in the shape it declares.
    """
    return sa.and_(Column.deleted_at.is_(None), Column.hidden.is_(False))
