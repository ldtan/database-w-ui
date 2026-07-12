from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from .enums import Action, ResourceType


class GrantCreate(BaseModel):
    """Grant (or overwrite) a principal's actions on a resource."""

    resource_type: ResourceType
    resource_uuid: UUID
    user_uuid: Optional[UUID] = None
    group_uuid: Optional[UUID] = None
    actions: list[Action] = Field(min_length=1)

    @model_validator(mode="after")
    def _exactly_one_principal(self):
        if (self.user_uuid is None) == (self.group_uuid is None):
            raise ValueError("Provide exactly one of user_uuid or group_uuid")
        return self


class GrantRead(BaseModel):
    uuid: UUID
    resource_type: ResourceType
    resource_uuid: UUID
    user_uuid: Optional[UUID] = None
    user_username: Optional[str] = None
    group_uuid: Optional[UUID] = None
    group_name: Optional[str] = None
    actions: list[Action]


class EffectiveActions(BaseModel):
    actions: list[Action]


class ResourceRef(BaseModel):
    resource_type: ResourceType
    resource_uuid: UUID


class EffectiveActionsQuery(BaseModel):
    """Ask for the caller's actions on many resources in one round trip — the UI
    gates per-row controls (a page of records, a workspace's lists)."""

    resources: list[ResourceRef] = Field(default_factory=list, max_length=500)


class ResourceActions(ResourceRef):
    actions: list[Action]
