from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.schemas import DisplayName, NameSlug


class WorkspaceBase(BaseModel):
    name: NameSlug
    display_name: DisplayName
    description: Optional[str] = None
    settings: dict = Field(default_factory=dict)


class WorkspaceCreate(WorkspaceBase):
    pass


class WorkspaceUpdate(BaseModel):
    name: Optional[NameSlug] = None
    display_name: Optional[DisplayName] = None
    description: Optional[str] = None
    settings: Optional[dict] = None


class WorkspaceTransfer(BaseModel):
    # The user to hand the workspace to. They become its owner, and with it the
    # owner of every list inside. Owner-only (enforced in the router).
    user_uuid: UUID


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
