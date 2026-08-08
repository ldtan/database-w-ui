from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.schemas import NameSlug

from .enums import FormType


class FormField(BaseModel):
    column_uuid: UUID
    included: bool = True
    enabled: bool = True
    order: int = 0
    label_override: Optional[str] = None


class MergedField(BaseModel):
    """One field of the *linked* list on a merged form.

    Structurally a `FormField`, and deliberately not one. Records are keyed by column
    uuid and these uuids belong to a different list, so anything that iterates "this
    form's fields" and reaches for a column of this list must not be handed them by
    accident. A separate class makes that a type error rather than a puzzle.
    """

    column_uuid: UUID
    included: bool = True
    enabled: bool = True
    order: int = 0
    label_override: Optional[str] = None


class MergeConfig(BaseModel):
    """Lays a second list's fields onto this form, reached through one lookup column.

    Only `link_column_uuid` is stored about the other list. Which list it is comes
    from that column's own `target_list_uuid` on every read, so retargeting the lookup
    cannot leave a stale pointer here to disagree with it.
    """

    link_column_uuid: UUID
    fields: list[MergedField] = Field(default_factory=list)
    # False is 1:1 — a linked record may be reached by at most one record of this
    # list, enforced on write like any unique column. True permits 1:many, which is
    # what makes editing a linked record change what other rows show and what makes
    # the mirror refresh touch more than one row.
    allow_shared: bool = False

    model_config = ConfigDict(extra="forbid")


class FormConfig(BaseModel):
    fields: list[FormField] = Field(default_factory=list)
    # Absent means an ordinary single-list form, which is every form that existed
    # before merging did — so old configs validate unchanged and no migration is
    # needed for any of this (the whole blob is JSONB).
    merge: Optional[MergeConfig] = None

    model_config = ConfigDict(extra="forbid")


class FormCreate(BaseModel):
    name: NameSlug
    display_name: str
    type: FormType = FormType.custom
    is_default: bool = False
    config: FormConfig = Field(default_factory=FormConfig)


class FormUpdate(BaseModel):
    name: Optional[NameSlug] = None
    display_name: Optional[str] = None
    type: Optional[FormType] = None
    is_default: Optional[bool] = None
    config: Optional[FormConfig] = None
    # Changing this requires 'share' on the form.
    permissions_inherit: Optional[bool] = None


class FormRead(BaseModel):
    uuid: UUID
    name: str
    display_name: str
    type: FormType
    is_default: bool
    config: dict
    permissions_inherit: bool = True

    model_config = ConfigDict(from_attributes=True)
