from typing import Annotated, Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.core.schemas import NameSlug


# What a filter, a sort or a grouping may name.
#
# A column's uuid, or a **mirror key** — `"<link column uuid>:<linked column uuid>"`,
# the copy of a linked record's value that a merged form keeps beside the record (see
# `items/mirror.py`). Both are keys into `list_items.data`, which is what these configs
# have always really held; `filtering.py` looks the key up in a dict and never parses
# it. Typed as `UUID` before mirrors existed, which read as tighter than it was — the
# uuid is not resolved against anything here either way.
#
# The pattern is kept narrow on purpose. Accepting a bare string would let a view store
# a key naming nothing, which `_condition` silently skips, so a filter would come back
# saying it had filtered.
_KEY = r"^[0-9a-fA-F-]{36}(:[0-9a-fA-F-]{36})?$"
ColumnKey = Annotated[str, StringConstraints(pattern=_KEY)]


class FilterCondition(BaseModel):
    column_uuid: ColumnKey
    operator: str
    value: Any = None


class FilterGroup(BaseModel):
    conjunction: Literal["and", "or"] = "and"
    conditions: list[FilterCondition] = Field(default_factory=list)


class SortSpec(BaseModel):
    column_uuid: ColumnKey
    direction: Literal["asc", "desc"] = "asc"


class GroupBy(BaseModel):
    column_uuid: ColumnKey


class ViewConfig(BaseModel):
    filters: FilterGroup = Field(default_factory=FilterGroup)
    sorts: list[SortSpec] = Field(default_factory=list)
    group_by: Optional[GroupBy] = None
    # Columns to hide in this view; empty means every column is shown, so
    # newly-added columns appear automatically until explicitly hidden.
    hidden_columns: list[UUID] = Field(default_factory=list)
    # Per-view column order (overrides the list's default column.position).
    # Columns absent from this list fall after, in their position order, so
    # newly-added columns still appear. Empty means pure position order.
    column_order: list[UUID] = Field(default_factory=list)
    page_size: int = 50

    model_config = ConfigDict(extra="forbid")


class ViewCreate(BaseModel):
    name: NameSlug
    display_name: str
    position: int = 0
    description: Optional[str] = None
    config: ViewConfig = Field(default_factory=ViewConfig)


class ViewUpdate(BaseModel):
    name: Optional[NameSlug] = None
    display_name: Optional[str] = None
    position: Optional[int] = None
    description: Optional[str] = None
    config: Optional[ViewConfig] = None
    # Changing this requires 'share' on the view.
    permissions_inherit: Optional[bool] = None


class ViewRead(BaseModel):
    uuid: UUID
    name: str
    display_name: str
    position: int
    description: Optional[str] = None
    config: dict
    permissions_inherit: bool = True

    model_config = ConfigDict(from_attributes=True)
