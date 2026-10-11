from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
import sqlalchemy as sa

from src.core.models import BaseModel

if TYPE_CHECKING:
    from src.auth.users.models import User


class Workspace(BaseModel):
    __tablename__ = "workspaces"

    owner_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("users.id"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(120))
    display_name: Mapped[str] = mapped_column(sa.String(255))
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    # The per-user "Incoming" workspace that transferred tables land in. Lazily
    # created on the first transfer to a user, and immutable thereafter: it cannot
    # be renamed or deleted (see WorkspaceService._guard_system_workspace), so it
    # stays a stable drop point. Its *tables* are ordinary — the recipient moves them
    # out into a real workspace, or deletes them to reject the transfer.
    is_system: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, server_default=sa.false(), nullable=False
    )

    owner: Mapped["User"] = relationship("User")

    __table_args__ = (
        sa.Index(
            "uq_workspaces_owner_name",
            "owner_id",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
