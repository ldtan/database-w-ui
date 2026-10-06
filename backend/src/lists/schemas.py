from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.core.schemas import DisplayName, NameSlug, ResourceLink


class QrSettings(BaseModel):
    """What a list's QR codes carry: `settings.qr`.

    **The first key of `lists.settings` anything reads**, and the reason it is validated
    while the rest of the blob is not. `settings` is documented as a free-form bag of
    "misc list-level settings", and it has to stay one — stripping a key this build has
    never heard of would destroy it on the next write. So validation is per *known* key:
    this one is checked, everything beside it is passed through untouched.

    Strict (`extra="forbid"`), for the reason `UserSettingsUpdate` is: a mistyped key here
    is a setting that silently never takes effect, and a 422 naming it is the kinder
    failure. A client sends the whole object, not a patch of it — `columns` and
    `include_uuid` are one coherent answer to "what is on the badge", and a partial update
    of it has no obvious meaning.

    **Columns are named, not uuid'd**, which is load-bearing in two places. It is the key
    the payload itself uses (so one vocabulary covers the setting, the code and the import
    that reads the code back), and it is what makes a *clone* work: `ListService.clone`
    deep-copies `settings`, and a clone keeps every column name — where uuids would need
    remapping through `col_map`, as a view's config does.
    """

    # An empty list is meaningful: a code carrying only `_uuid`, which is a perfectly good
    # badge for a system that will look the record up.
    columns: list[str] = Field(default_factory=list)
    include_uuid: bool = True

    model_config = ConfigDict(extra="forbid")


# Keys of `lists.settings` this build understands, and the model each is checked against.
# Adding a validated key means adding a line here; anything absent from it stays
# free-form, which is the blob's documented contract.
_SETTINGS_MODELS: dict[str, type[BaseModel]] = {"qr": QrSettings}


def _validated_settings(value: Any) -> Any:
    """Check the keys of `settings` this build knows, and pass the rest through.

    Raises so Pydantic reports it as a 422 against the field, naming the offending key —
    which is the whole point of validating a blob at all.
    """
    if not isinstance(value, dict):
        return value
    for key, model in _SETTINGS_MODELS.items():
        if key in value:
            try:
                model.model_validate(value[key])
            except ValidationError as exc:
                raise ValueError(f"settings.{key}: {exc.errors()[0]['msg']}") from exc
    return value


class ListBase(BaseModel):
    name: NameSlug
    display_name: DisplayName
    description: Optional[str] = None
    settings: dict = Field(default_factory=dict)

    _check_settings = field_validator("settings")(_validated_settings)


class ListCreate(ListBase):
    # The workspace this list belongs to (must be owned by the caller).
    workspace_uuid: UUID


class ListUpdate(BaseModel):
    name: Optional[NameSlug] = None
    display_name: Optional[DisplayName] = None
    description: Optional[str] = None
    # Point the list's primary/display field at one of its own columns.
    primary_column_uuid: Optional[UUID] = None
    # Move the list to a different workspace (must be owned by the caller).
    workspace_uuid: Optional[UUID] = None
    # Merged onto the stored blob rather than replacing it — only the keys sent are
    # touched, so a client that has never heard of a key cannot drop it. Shallow, at the
    # top level: sending `qr` replaces the whole `qr` object, which is right because it is
    # one coherent setting. See `ListService.update`.
    settings: Optional[dict] = None
    # Whether this list inherits permission grants from its workspace. Changing it
    # requires 'share' on the list.
    permissions_inherit: Optional[bool] = None

    _check_settings = field_validator("settings")(_validated_settings)


class ListTransfer(BaseModel):
    # The user to hand the list to. It moves into that user's immutable "Incoming"
    # workspace, making them its owner. Owner-only (enforced in the router).
    user_uuid: UUID


class ListClone(BaseModel):
    # Duplicate the list into the same workspace. Structure (columns, views, forms)
    # is always copied; records come along only when this is true.
    with_records: bool = False


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
