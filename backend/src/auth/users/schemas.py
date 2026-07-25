from typing import Literal, Optional, List
from uuid import UUID

from pydantic import BaseModel, EmailStr, ConfigDict, Field

from src.core.schemas import ResourceLink


# Kept in step with ThemeId/ThemeMode in frontend/src/lib/theme.tsx by hand — a
# palette added there without a matching value here is a 422 the moment anyone
# selects it. Spelled as Literals so `npm run gen:api` publishes real unions rather
# than a bare string, which is what makes the frontend side of this typed at all.
ThemeId = Literal["modern", "nordic", "brutalist", "luxury"]
ThemeMode = Literal["light", "dark"]


class UserSettings(BaseModel):
    """UI preferences that follow the account, restored wherever it logs in.

    Lenient on the way out: a stored blob can hold a key this build has never heard
    of — written by a newer deploy, then rolled back — and a read of /me must not
    500 over it. Writes are strict; see UserSettingsUpdate.

    Defaults mirror DEFAULT_THEME/DEFAULT_MODE in frontend/src/lib/theme.tsx, so an
    account that has never saved anything looks identical to a fresh browser.
    """

    theme: ThemeId = "modern"
    mode: ThemeMode = "light"
    model_config = ConfigDict(extra="ignore")


class UserSettingsUpdate(BaseModel):
    """PATCH body for preferences.

    Every field is optional because a write merges onto the stored blob and touches
    only the keys actually sent — two tabs changing different preferences do not
    clobber each other. `extra="forbid"` so a mistyped key is a 422 rather than a
    preference that silently never takes effect. There is no null-to-clear: to reset
    one, send its default value, which keeps "absent means untouched" the only
    reading of a missing key.
    """

    theme: Optional[ThemeId] = None
    mode: Optional[ThemeMode] = None
    model_config = ConfigDict(extra="forbid")


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
    # Typed rather than a bare dict, unlike the older config blobs (ViewConfig,
    # FormConfig) whose Read schemas say `dict` and leave frontend/src/lib/types.ts
    # to re-declare the shape by hand. Typing it here means gen:api publishes it and
    # there is nothing to keep in step.
    settings: UserSettings = Field(default_factory=UserSettings)
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
    # A system group (e.g. "Authenticated Users") is immutable and its membership is
    # implicit — an empty `users` means *everyone*, not nobody, so the UI reads this
    # flag to render it correctly and to hide edit/delete.
    is_system: bool = False
    is_deleted: bool = False
    model_config = ConfigDict(from_attributes=True)
