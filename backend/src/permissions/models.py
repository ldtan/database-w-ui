from __future__ import annotations
from datetime import datetime  # noqa: TC003 — resolved at runtime by Mapped[] introspection
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
import sqlalchemy as sa

from src.core.models import BaseModel

from .enums import ResourceType

if TYPE_CHECKING:
    from src.auth.users.models import User, UserGroup


class PermissionGrant(BaseModel):
    __tablename__ = "permission_grants"

    # Polymorphic pointer to the securable resource. No FK: resource_id refers to a
    # different table depending on resource_type. Existence is validated in the
    # service when a grant is created; the resolver only walks live resources.
    resource_type: Mapped[ResourceType] = mapped_column(
        sa.Enum(ResourceType, name="permission_resource_type")
    )
    resource_id: Mapped[int] = mapped_column(sa.Integer)

    # Exactly one principal is set (enforced by the check constraint below).
    user_id: Mapped[Optional[int]] = mapped_column(
        sa.Integer, sa.ForeignKey("users.id"), nullable=True
    )
    group_id: Mapped[Optional[int]] = mapped_column(
        sa.Integer, sa.ForeignKey("user_groups.id"), nullable=True
    )
    # Granted actions as their string values (see enums.Action).
    actions: Mapped[list] = mapped_column(JSONB, default=list)

    # When the grant stops conferring access. None means it never expires. Enforced
    # centrally in PermissionService._principal_condition, so an expired grant is
    # invisible to every authorization read at once.
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    user: Mapped[Optional["User"]] = relationship("User")
    group: Mapped[Optional["UserGroup"]] = relationship("UserGroup")

    __table_args__ = (
        sa.CheckConstraint(
            "(user_id IS NULL) <> (group_id IS NULL)",
            name="ck_permission_grants_one_principal",
        ),
        # One grant row per (resource, principal). Partial indexes because NULLs in
        # the unused principal column are distinct under Postgres uniqueness.
        sa.Index(
            "uq_permission_grants_user",
            "resource_type",
            "resource_id",
            "user_id",
            unique=True,
            postgresql_where=sa.text("user_id IS NOT NULL"),
        ),
        sa.Index(
            "uq_permission_grants_group",
            "resource_type",
            "resource_id",
            "group_id",
            unique=True,
            postgresql_where=sa.text("group_id IS NOT NULL"),
        ),
        sa.Index(
            "ix_permission_grants_resource", "resource_type", "resource_id"
        ),
    )
