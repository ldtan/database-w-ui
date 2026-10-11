from __future__ import annotations
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
import sqlalchemy as sa

from src.core.models import BaseModel
from .enums import ColumnType

if TYPE_CHECKING:
    from ..models import Table


class Column(BaseModel):
    __tablename__ = "columns"

    table_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("tables.id"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(120))
    display_name: Mapped[str] = mapped_column(sa.String(255))
    type: Mapped[ColumnType] = mapped_column(sa.Enum(ColumnType, name="column_type"))
    position: Mapped[int] = mapped_column(sa.Integer, default=0)
    description: Mapped[str] = mapped_column(sa.Text, nullable=True)
    # Type-specific settings (options, precision, target table, required, ...).
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Live, but not part of the table's shape. **Nothing sets this any more.** It
    # existed for the staged type conversion, which built its target column here and
    # backfilled into it so half-converted data stayed invisible until the cutover —
    # and the gap that opened between building and cutting over is precisely what
    # lost writes (known-gaps §11). A conversion is one transaction now and stages
    # nothing.
    #
    # The field survives for two reasons: `src/maintenance/drop_conversion_scratch.py`
    # needs it to find the rows that design stranded, and "invisible to readers but
    # not marked for the purge" remains a distinct state from `deleted_at` if
    # anything ever wants it again. Nothing should set it casually — a hidden column
    # is one no code path will ever read, finish or offer to clean up.
    hidden: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, server_default=sa.false(), nullable=False
    )

    table: Mapped["Table"] = relationship(
        "Table", back_populates="columns", foreign_keys=[table_id]
    )

    __table_args__ = (
        sa.Index(
            "uq_columns_table_name",
            "table_id",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )


def in_shape():
    """The predicate for "this column is part of the table's shape".

    Live *and* not hidden. Every reader that answers "what fields does a record
    have" — the grid, the record service, export, metrics, forms, the primary
    column, a lookup's label — must filter on this.

    Nothing sets `hidden` any more (see the field), so in practice this is "not
    deleted" plus a clause that currently excludes only the rows the old staged
    conversion stranded. Keep the clause: it is what makes those rows invisible
    until the one-off cleanup removes them, and it is the guard if anything wants
    the state back.

    Two readers deliberately do **not** use it and take `deleted_at` alone: the
    reference scrub and the shape reconcile. A column being invisible does not
    exempt the uuids it holds from being cleaned up when their target is purged.
    """
    return sa.and_(Column.deleted_at.is_(None), Column.hidden.is_(False))
