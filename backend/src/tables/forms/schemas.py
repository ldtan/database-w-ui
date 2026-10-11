from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.schemas import DisplayName, NameSlug

from ..views.schemas import FilterGroup
from .enums import FormType


class FieldCondition(BaseModel):
    """When a field applies, and what happens to it when it does not.

    ``when`` is a view's `FilterGroup`, borrowed rather than reinvented. It is the same
    question over the same columns — "which records match these conditions?" asked of
    the one record being filled in — so it gets the same operators, the same stored
    shape and the same editor. A second condition language for the same job would only
    be a second place for the two to disagree.

    An empty ``when.conditions`` means **always**, matching `apply_filters`, which
    filters nothing when handed no conditions. So a block left half-built in the editor
    is inert rather than hiding the field it was meant to reveal.

    Nothing on the server reads this. `TableRecordService._validate_data` enforces
    ``required``, ``unique`` and the rest whatever a form says, and CSV import, a QR fill
    and a direct API write have no form at all — see docs/known-gaps.md.
    """

    when: FilterGroup = Field(default_factory=FilterGroup)
    # What an unmet condition does. Hiding is the default because that is the ordinary
    # want — a field that does not apply is not asked for.
    effect: Literal["hide", "disable"] = "hide"

    model_config = ConfigDict(extra="forbid")


class FormField(BaseModel):
    column_uuid: UUID
    included: bool = True
    enabled: bool = True
    order: int = 0
    label_override: Optional[str] = None
    # Absent means unconditional, which is every field that existed before conditions
    # did — so old configs validate unchanged and none of this needs a migration.
    condition: Optional[FieldCondition] = None


class FormConfig(BaseModel):
    fields: list[FormField] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class FormCreate(BaseModel):
    name: NameSlug
    display_name: DisplayName
    type: FormType = FormType.custom
    is_default: bool = False
    config: FormConfig = Field(default_factory=FormConfig)


class FormUpdate(BaseModel):
    name: Optional[NameSlug] = None
    display_name: Optional[DisplayName] = None
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
