from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
import sqlalchemy as sa

from src.core.models import BaseModel


class Metric(BaseModel):
    """A named aggregate over a list's records — "Total hours", "Average cost".

    A sibling of views and forms: list-scoped, named, ordered, its behaviour in a
    JSONB `config` (D5). It stores only the *question*; the answer is computed per
    request against the records the reader may see, so a metric has no value of
    its own to keep in sync.

    Unlike views and forms there is no `permissions_inherit`: a metric is always
    its list's, readable by anyone who can view the list and editable by anyone
    who can configure it. It cannot leak past that boundary, because the scan
    behind it runs through the same `item_view_filter` every record read does.
    """

    __tablename__ = "metrics"

    list_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("lists.id"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(120))
    display_name: Mapped[str] = mapped_column(sa.String(255))
    position: Mapped[int] = mapped_column(sa.Integer, default=0)
    description: Mapped[str] = mapped_column(sa.Text, nullable=True)
    # The formula and how to render its value (see schemas.MetricConfig).
    config: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        sa.Index(
            "uq_metrics_list_name",
            "list_id",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
