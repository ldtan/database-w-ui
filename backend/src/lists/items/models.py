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
        # The grid's query, exactly: a list's live records in id order (see
        # `apply_sorts`, which appends `id` as the tiebreaker and the default).
        # Without it the planner would rather scan the primary key — already in id
        # order — and throw away every row belonging to another list: measured at
        # 100k records across 50 lists, one page of 100 discarded 9,850 rows and read
        # 553 buffers to do it. What that cost tracks is how *sparse* the list is in
        # the table, not how big it is, and OFFSET paging multiplies it per page.
        #
        # Partial on the live rows because that is what every one of those reads asks
        # for, and it keeps the bin out of an index none of them wants. The plain
        # `list_id` index stays for the bin, which asks the opposite question. The
        # keyset page in `ListService.clone` and the maintenance loops read this way
        # too.
        sa.Index(
            "ix_list_items_list_id_id",
            "list_id",
            "id",
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
