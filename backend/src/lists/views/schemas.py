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


class ViewRead(BaseModel):
    uuid: UUID
    name: str
    display_name: str
    position: int
    config: dict

    model_config = ConfigDict(from_attributes=True)
