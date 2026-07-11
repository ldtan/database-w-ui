from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..views.schemas import FilterGroup, SortSpec


class ItemQuery(BaseModel):
    """Ad-hoc filter/sort/limit for reading records without a saved view
    (used by export)."""

    filters: FilterGroup = Field(default_factory=FilterGroup)
    sorts: list[SortSpec] = Field(default_factory=list)
    skip: int = Field(0, ge=0)
    limit: int = Field(100, ge=1, le=1000)


class ListItemCreate(BaseModel):
    # Keys are column uuids (as strings); values are validated per column type.
    data: dict = Field(default_factory=dict)


class ListItemUpdate(BaseModel):
    # Partial: only the provided keys are merged onto the existing record.
    data: dict = Field(default_factory=dict)


class ListItemRead(BaseModel):
    uuid: UUID
    data: dict

    model_config = ConfigDict(from_attributes=True)
