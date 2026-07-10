from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
import sqlalchemy as sa

from src.core.models import BaseModel

if TYPE_CHECKING:
    from src.workspaces.models import Workspace
    from .columns.models import Column


class List(BaseModel):
    __tablename__ = "lists"

    workspace_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("workspaces.id"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(120))
    display_name: Mapped[str] = mapped_column(sa.String(255))
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    # The column used to label a row when it is referenced by a lookup (the
    # "primary field"). Nullable because columns are created after the list, so
    # use_alter defers the FK constraint to break the lists <-> columns cycle.
    primary_column_id: Mapped[Optional[int]] = mapped_column(
        sa.Integer,
        sa.ForeignKey("columns.id", use_alter=True, name="fk_lists_primary_column_id"),
        nullable=True,
    )
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    columns: Mapped[list["Column"]] = relationship(
        "Column",
        back_populates="list",
        foreign_keys="Column.list_id",
    )
    primary_column: Mapped[Optional["Column"]] = relationship(
        "Column",
        foreign_keys=[primary_column_id],
        post_update=True,
    )

    __table_args__ = (
        sa.Index(
            "uq_lists_workspace_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
