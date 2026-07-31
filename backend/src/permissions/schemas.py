from datetime import datetime, timezone
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
    # When the grant should stop conferring access. None (the default) never expires.
    expires_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _exactly_one_principal(self):
        if (self.user_uuid is None) == (self.group_uuid is None):
            raise ValueError("Provide exactly one of user_uuid or group_uuid")
        return self

    @model_validator(mode="after")
    def _expiry_in_the_future(self):
        if self.expires_at is not None:
            # Accept naive datetimes as UTC so an ISO string without an offset does
            # not slip past this check; a past expiry is inert, so reject it plainly.
            when = self.expires_at
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when <= datetime.now(timezone.utc):
                raise ValueError("expires_at must be in the future")
        return self


class GrantRead(BaseModel):
    uuid: UUID
    resource_type: ResourceType
    resource_uuid: UUID
    user_uuid: Optional[UUID] = None
    user_username: Optional[str] = None
    group_uuid: Optional[UUID] = None
    # The label to render, so this is the group's `display_name` — the counterpart of
    # `user_username` above, not of the group's `name`. That slug rides along
    # separately because `display_name` is not unique: the Share panel prints it under
    # the label so two groups reading alike can still be told apart before someone
    # grants access to the wrong one.
    group_name: Optional[str] = None
    group_slug: Optional[str] = None
    actions: list[Action]
    # None means the grant never expires. A grant already past this is listed (so the
    # owner can see and renew or remove it) but confers nothing.
    expires_at: Optional[datetime] = None


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
