from datetime import datetime
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
    # Whether this list inherits permission grants from its workspace. Changing it
    # requires 'share' on the list.
    permissions_inherit: Optional[bool] = None


class ListRead(ListBase):
    uuid: UUID
    workspace: ResourceLink
    primary_column: Optional[ResourceLink] = None
    permissions_inherit: bool = True
    # The workspace owner's username. A disambiguator for the "Shared with me"
    # section, where several lists can share a display name; populated by the list
    # endpoint (None elsewhere).
    owner_username: Optional[str] = None
    # The workspace's display name, but *only* when the caller can view that
    # workspace. A list can be shared without its workspace (see "Shared with me"),
    # and naming a workspace the caller was denied would leak it — so it stays None
    # in exactly that case, and the UI falls back to the list's own slug.
    workspace_display_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class DeletedListRead(ListRead):
    # When the list was binned, so the UI can show how long it has left in the
    # retention window. Always set for a bin listing.
    deleted_at: datetime
