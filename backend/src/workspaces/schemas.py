from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.schemas import NameSlug


class WorkspaceBase(BaseModel):
    name: NameSlug
    display_name: str
    description: Optional[str] = None
    settings: dict = Field(default_factory=dict)


class WorkspaceCreate(WorkspaceBase):
    pass


class WorkspaceUpdate(BaseModel):
    name: Optional[NameSlug] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    settings: Optional[dict] = None


class WorkspaceRead(WorkspaceBase):
    uuid: UUID
    # True only for the per-user "Incoming" workspace transferred lists land in. The
    # UI hides rename/delete for it, because it is immutable server-side too.
    is_system: bool = False

    model_config = ConfigDict(from_attributes=True)


class DeletedWorkspaceRead(WorkspaceRead):
    # When the workspace was binned, so the UI can show how long it has left in the
    # retention window. Always set for a bin listing.
    deleted_at: datetime
