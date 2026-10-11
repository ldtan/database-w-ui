from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
import sqlalchemy as sa

from src.core.models import BaseModel

if TYPE_CHECKING:
    from src.workspaces.models import Workspace
    from .columns.models import Column


class Table(BaseModel):
    __tablename__ = "tables"

    workspace_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("workspaces.id"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(120))
    display_name: Mapped[str] = mapped_column(sa.String(255))
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    # The column used to label a row when it is referenced by a lookup (the
    # "primary field"). Nullable because columns are created after the table, so
    # use_alter defers the FK constraint to break the tables <-> columns cycle.
    primary_column_id: Mapped[Optional[int]] = mapped_column(
        sa.Integer,
        sa.ForeignKey("columns.id", use_alter=True, name="fk_tables_primary_column_id"),
        nullable=True,
    )
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    # When True, this table combines its own permission grants with the workspace's;
    # when False ("unique"), it stands alone. See src/permissions.
    permissions_inherit: Mapped[bool] = mapped_column(
        sa.Boolean, default=True, server_default=sa.true(), nullable=False
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    columns: Mapped[list["Column"]] = relationship(
        "Column",
        back_populates="table",
        foreign_keys="Column.table_id",
    )
    primary_column: Mapped[Optional["Column"]] = relationship(
        "Column",
        foreign_keys=[primary_column_id],
        post_update=True,
    )

    __table_args__ = (
        sa.Index(
            "uq_tables_workspace_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
