from typing import Optional
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


class BulkDeleteRequest(BaseModel):
    uuids: list[UUID] = Field(default_factory=list)


class BulkDeleteResult(BaseModel):
    deleted: int


class ListItemCreate(BaseModel):
    # Keys are column uuids (as strings); values are validated per column type.
    data: dict = Field(default_factory=dict)


class ListItemUpdate(BaseModel):
    # Partial: only the provided keys are merged onto the existing record.
    data: dict = Field(default_factory=dict)
    # Whether this record inherits permission grants from its list. Changing it
    # requires 'share' on the record.
    permissions_inherit: Optional[bool] = None


class ListItemRead(BaseModel):
    uuid: UUID
    data: dict
    permissions_inherit: bool = True

    model_config = ConfigDict(from_attributes=True)
