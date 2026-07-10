from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.schemas import NameSlug, ResourceLink


class ListBase(BaseModel):
    name: NameSlug
    display_name: str
    description: Optional[str] = None
    settings: dict = Field(default_factory=dict)


class ListCreate(ListBase):
    # The workspace this list belongs to (must be owned by the caller).
    workspace_uuid: UUID


class ListUpdate(BaseModel):
    name: Optional[NameSlug] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    # Point the list's primary/display field at one of its own columns.
    primary_column_uuid: Optional[UUID] = None
    # Move the list to a different workspace (must be owned by the caller).
    workspace_uuid: Optional[UUID] = None
    settings: Optional[dict] = None


class ListRead(ListBase):
    uuid: UUID
    workspace: ResourceLink
    primary_column: Optional[ResourceLink] = None

    model_config = ConfigDict(from_attributes=True)
