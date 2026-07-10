from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.schemas import NameSlug

from .enums import ColumnType


class ChoiceOption(BaseModel):
    id: str
    label: str
    color: Optional[str] = None


class ColumnConfig(BaseModel):
    """Type-specific column settings. Only the keys relevant to a column's
    ``type`` are meaningful; the service validates the combination."""

    # Common
    required: bool = False
    default: Any = None
    help_text: Optional[str] = None
    unique: bool = False
    # text
    max_length: Optional[int] = None
    # number
    precision: Optional[int] = None
    number_format: Optional[str] = None  # plain | currency | percent
    min: Optional[float] = None
    max: Optional[float] = None
    # choice
    options: Optional[list[ChoiceOption]] = None
    multiple: Optional[bool] = None
    # lookup
    target_list_uuid: Optional[UUID] = None

    model_config = ConfigDict(extra="forbid")


class ColumnCreate(BaseModel):
    name: NameSlug
    display_name: str
    type: ColumnType
    position: int = 0
    config: ColumnConfig = Field(default_factory=ColumnConfig)


class ColumnUpdate(BaseModel):
    name: Optional[NameSlug] = None
    display_name: Optional[str] = None
    position: Optional[int] = None
    config: Optional[ColumnConfig] = None


class ColumnRead(BaseModel):
    uuid: UUID
    name: str
    display_name: str
    type: ColumnType
    position: int
    config: dict

    model_config = ConfigDict(from_attributes=True)
