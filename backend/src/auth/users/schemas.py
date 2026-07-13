from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, EmailStr, ConfigDict, Field

from src.core.schemas import ResourceLink


class UserBase(BaseModel):
    username: str
    email: EmailStr
    is_active: bool = True


class UserCreate(UserBase):
    password: str


class UserUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
    # Promotion/demotion. The router rejects this from non-superusers, otherwise
    # any account could promote itself.
    is_superuser: Optional[bool] = None
    # Changing your *own* password requires proving you know the current one, so a
    # stolen access token cannot be turned into a permanent account takeover. A
    # superuser resetting someone else's password does not need it.
    current_password: Optional[str] = None


class UserRead(UserBase):
    uuid: UUID
    is_superuser: bool = False
    groups: List[ResourceLink] = Field(default_factory=list)
    # Deleted accounts come back only when a caller resolves a uuid one of its
    # records already holds, so the cell can render "(deleted)" rather than a raw id.
    is_deleted: bool = False
    model_config = ConfigDict(from_attributes=True)


class UserGroupBase(BaseModel):
    name: str
    description: Optional[str] = None


class UserGroupCreate(UserGroupBase):
    user_uuids: Optional[list[UUID]] = None


class UserGroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    user_uuids: Optional[list[UUID]] = None


class UserGroupRead(UserGroupBase):
    uuid: UUID
    users: List[ResourceLink] = Field(default_factory=list)
    is_deleted: bool = False
    model_config = ConfigDict(from_attributes=True)
