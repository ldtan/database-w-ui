from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
import sqlalchemy as sa

from src.core.models import BaseModel
from .enums import FormType


class Form(BaseModel):
    __tablename__ = "forms"

    list_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("lists.id"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(120))
    display_name: Mapped[str] = mapped_column(sa.String(255))
    type: Mapped[FormType] = mapped_column(
        sa.Enum(FormType, name="form_type"), default=FormType.custom
    )
    is_default: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    # Field layout: which columns show, order, enabled (see schemas.FormConfig).
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    # When True, inherits permission grants from its list; when False, stands alone
    # (used to target a form at a specific user/group). See src/permissions.
    permissions_inherit: Mapped[bool] = mapped_column(
        sa.Boolean, default=True, server_default=sa.true(), nullable=False
    )

    __table_args__ = (
        sa.Index(
            "uq_forms_list_name",
            "list_id",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
