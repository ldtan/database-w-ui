from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.schemas import NameSlug


class FilterCondition(BaseModel):
    column_uuid: UUID
    operator: str
    value: Any = None


class FilterGroup(BaseModel):
    conjunction: Literal["and", "or"] = "and"
    conditions: list[FilterCondition] = Field(default_factory=list)


class SortSpec(BaseModel):
    column_uuid: UUID
    direction: Literal["asc", "desc"] = "asc"


class GroupBy(BaseModel):
    column_uuid: UUID


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
    config: ViewConfig = Field(default_factory=ViewConfig)


class ViewUpdate(BaseModel):
    name: Optional[NameSlug] = None
    display_name: Optional[str] = None
    position: Optional[int] = None
    config: Optional[ViewConfig] = None
    # Changing this requires 'share' on the view.
    permissions_inherit: Optional[bool] = None


class ViewRead(BaseModel):
    uuid: UUID
    name: str
    display_name: str
    position: int
    config: dict
    permissions_inherit: bool = True

    model_config = ConfigDict(from_attributes=True)
