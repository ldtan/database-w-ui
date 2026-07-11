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


class FormConfig(BaseModel):
    fields: list[FormField] = Field(default_factory=list)

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
