from typing import Any, Literal, Optional, List, get_args
from uuid import UUID
from zoneinfo import available_timezones

from pydantic import (
    BaseModel,
    EmailStr,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from src.core.schemas import ResourceLink


# Kept in step with ThemeId/ThemeMode in frontend/src/lib/theme.tsx by hand — a
# palette added there without a matching value here is a 422 the moment anyone
# selects it. Spelled as Literals so `npm run gen:api` publishes real unions rather
# than a bare string, which is what makes the frontend side of this typed at all.
ThemeId = Literal["modern", "nordic", "brutalist", "luxury"]
ThemeMode = Literal["light", "dark"]

# Not spelled as a Literal like the two above: an IANA zone id comes from a set of
# several hundred that the tz database revises a few times a year, so it is checked
# against zoneinfo rather than pinned in the type. Resolved once at import — the
# lookup walks the tz database directory. An id, never a fixed offset: "+08:00"
# stops being true the next time the zone's DST rules change.
_TIMEZONES = available_timezones()

_SETTING_VALUES = {
    "theme": set(get_args(ThemeId)),
    "mode": set(get_args(ThemeMode)),
    "timezone": _TIMEZONES,
}


class UserSettings(BaseModel):
    """UI preferences that follow the account, restored wherever it logs in.

    Lenient on the way out: a stored blob can hold a key *or a value* this build has
    never heard of — written by a newer deploy, then rolled back — and a read of /me
    must not 500 over it. Writes are strict; see UserSettingsUpdate.

    Defaults mirror DEFAULT_THEME/DEFAULT_MODE in frontend/src/lib/theme.tsx, so an
    account that has never saved anything looks identical to a fresh browser.
    """

    theme: ThemeId = "modern"
    mode: ThemeMode = "light"
    # Where this account's "today" falls, for resolving a `today`/`now` column
    # default on the user's calendar instead of the server's (see
    # lists.columns.validation.resolve_dynamic_default). TimezoneSync reports the
    # browser's on load, so this is the value only until the user first appears.
    #
    # Defaulted rather than Optional, for the same reason theme and mode are: a null
    # default is what openapi-typescript reads as "may be absent", and the frontend's
    # Response<> in lib/types.ts — which promises every response field is present —
    # is shallow, so a nullable field here becomes an `undefined` the callers have to
    # thread through. Nothing needs to tell "never reported" from "reported UTC":
    # both resolve to UTC, which is where the server would have stamped anyway.
    timezone: str = "UTC"
    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _drop_unrecognised_values(cls, data: Any) -> Any:
        """Fall back to the default for a value outside the union.

        `extra="ignore"` covers a key we do not know; this covers a key we do know
        carrying a value we do not — the likelier rollback, since adding a palette
        means an existing key gains a value rather than a new key appearing. Dropping
        it here lets the field default apply, so the user sees the default theme for
        one session instead of an unloadable app. Only reads pass through this:
        writes are validated as UserSettingsUpdate, where the same value is a 422.
        """
        if not isinstance(data, dict):
            return data
        return {
            key: value
            for key, value in data.items()
            if key not in _SETTING_VALUES
            or (isinstance(value, str) and value in _SETTING_VALUES[key])
        }


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
    timezone: Optional[str] = None
    model_config = ConfigDict(extra="forbid")

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: Any) -> Any:
        """Refuse a zone this build's tz database cannot resolve.

        `theme` and `mode` get this for free from their Literal types; a zone id is
        an open set, so the check is explicit. Loudly, because the failure it
        prevents is silent: an unresolvable id falls back to UTC every time a
        `today` default is stamped, which looks like working software right up
        until someone's record is dated yesterday.
        """
        if value is not None and value not in _TIMEZONES:
            raise ValueError(f"'{value}' is not a known IANA timezone")
        return value

    @field_validator("theme", "mode", "timezone", mode="before")
    @classmethod
    def _reject_explicit_null(cls, value: Any) -> Any:
        """`None` means "not sent", so it must not be sendable.

        The merge keys off `exclude_unset`, which cannot tell an omitted key from one
        explicitly set to null — so without this an explicit null is stored, and then
        no read can deserialize it, 500ing /me and the all-users listing for everyone
        rather than just the caller. A validator rather than dropping it at the merge:
        the request is malformed, and the caller should hear so.
        """
        if value is None:
            raise ValueError("must not be null; omit the key to leave it unchanged")
        return value


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
