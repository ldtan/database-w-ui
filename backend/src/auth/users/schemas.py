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


class UserRead(UserBase):
    uuid: UUID
    groups: List[ResourceLink] = Field(default_factory=list)
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
    model_config = ConfigDict(from_attributes=True)
