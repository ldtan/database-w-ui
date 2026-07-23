from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.schemas import NameSlug

from .enums import ColumnType


class ColumnConfig(BaseModel):
    """Type-specific column settings. Only the keys relevant to a column's
    ``type`` are meaningful; the service validates the combination."""

    # Common (all types)
    required: bool = False
    default: Any = None
    # date/time/datetime/user only: a dynamic token ("today"/"now"/"current_user")
    # re-stamped onto the cell on create and on every edit (see columns/validation).
    onupdate: Any = None
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
    # choice — options are plain labels; a cell stores the label itself
    choices: Optional[str] = None  # shorthand input, e.g. "Todo;Doing;Done"
    options: Optional[list[str]] = None  # canonical form, e.g. ["Todo", "Doing"]
    multiple: Optional[bool] = None
    # lookup
    target_list_uuid: Optional[UUID] = None
    # which column of the target list to show as each row's label; defaults to
    # the target list's primary column when unset
    display_column_uuid: Optional[UUID] = None
    # nested lookup: this column's options are constrained by what a sibling
    # lookup (the "parent") holds. `parent_column_uuid` names that sibling on the
    # same list; `target_filter_column_uuid` names the lookup column *on this
    # column's target list* that points at the parent's target list — a target
    # row is offered only when its filter cell matches the parent's value.
    # Set and unset together; the service validates the pairing.
    parent_column_uuid: Optional[UUID] = None
    target_filter_column_uuid: Optional[UUID] = None
    # calculated — an expression over other columns of this list, by name. The
    # column stores nothing; the value is computed on read. See columns/formula.py.
    formula: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class ColumnCreate(BaseModel):
    name: NameSlug
    display_name: str
    type: ColumnType
    position: int = 0
    description: Optional[str] = None
    config: ColumnConfig = Field(default_factory=ColumnConfig)


class ColumnUpdate(BaseModel):
    name: Optional[NameSlug] = None
    display_name: Optional[str] = None
    position: Optional[int] = None
    description: Optional[str] = None
    config: Optional[ColumnConfig] = None


class LookupOption(BaseModel):
    """One selectable row of a lookup's target list.

    ``label`` is the value of the lookup's display column; it is null when the row
    has none, in which case the caller shows the uuid — the only thing left that
    identifies it.
    """

    uuid: UUID
    label: Optional[str] = None
    # The row is in the bin. Returned only when resolving specific uuids — a search
    # never offers one — so the caller can render an existing link as unavailable
    # rather than pretending the relationship is gone.
    deleted: bool = False


class ColumnRead(BaseModel):
    uuid: UUID
    name: str
    display_name: str
    type: ColumnType
    position: int
    description: Optional[str] = None
    config: dict

    model_config = ConfigDict(from_attributes=True)
