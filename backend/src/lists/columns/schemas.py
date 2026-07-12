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

    # Common (all types)
    required: bool = False
    default: Any = None
    help_text: Optional[str] = None
    unique: bool = False  # enforced: no two records may share the value
    placeholder: Optional[str] = None  # UI hint, not enforced
    read_only: bool = False  # UI hint, not enforced
    # text / long_text
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None  # regex; value must fully match
    # number, date, time, datetime — min/max are interpreted per type
    # (a number for number columns; an ISO-8601 string for temporal columns)
    min: Optional[Any] = None
    max: Optional[Any] = None
    # number
    precision: Optional[int] = None
    number_format: Optional[str] = None  # plain | currency | percent
    integer_only: Optional[bool] = None
    # link
    allowed_schemes: Optional[list[str]] = None  # defaults to ["http", "https"]
    label: Optional[str] = None  # fixed anchor text; falls back to the URL itself
    # choice
    choices: Optional[str] = None  # shorthand input, e.g. "Todo;Doing;Done"
    options: Optional[list[ChoiceOption]] = None  # full control (ids, colors)
    multiple: Optional[bool] = None
    # lookup
    target_list_uuid: Optional[UUID] = None
    # which column of the target list to show as each row's label; defaults to
    # the target list's primary column when unset
    display_column_uuid: Optional[UUID] = None

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


class LookupOption(BaseModel):
    """One selectable row of a lookup's target list.

    ``label`` is the value of the lookup's display column; it is null when the row
    has none, in which case the caller shows the uuid — the only thing left that
    identifies it.
    """

    uuid: UUID
    label: Optional[str] = None


class ColumnRead(BaseModel):
    uuid: UUID
    name: str
    display_name: str
    type: ColumnType
    position: int
    config: dict

    model_config = ConfigDict(from_attributes=True)
