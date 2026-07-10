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

    model_config = ConfigDict(from_attributes=True)
