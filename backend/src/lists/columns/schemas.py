from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    # text
    # How the cell is edited: "single" (one line) or "multiline" (a box). Not a
    # storage difference — both hold a plain string, and every filter, sort,
    # search, import and export treats them alike — so it is config rather than
    # two column types, which also makes it changeable after creation, as `type`
    # is not. Unset means "single".
    text_mode: Optional[str] = None
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
    # calculated — an expression over other columns of this list, by name, including
    # other calculated columns. The value is computed on every write and *stored* like
    # any other cell, which is what lets it be searched, filtered and sorted. See
    # columns/formula.py for the language and columns/chain.py for the ordering.
    formula: Optional[str] = None
    # Where this column sits in the chain: lower is computed first. Maintained by the
    # service, which derives it from the formula's dependencies and keeps it consistent
    # — you may set it, and an explicit value is honoured as a floor, so raising it
    # pins the order among columns that do not reference each other. A value that
    # contradicts a real dependency is refused rather than silently ignored.
    compute_order: Optional[int] = None

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


class ConversionStart(BaseModel):
    """The column to convert *into*: a type, and that type's settings.

    The config is validated from scratch against the new type rather than merged
    onto the old one — a `choice`'s options and a `lookup`'s target mean nothing to
    a `number`, and carrying them over would be carrying over the previous type's
    assumptions. For `text` -> `choice`, leaving `options` unset mints one per
    distinct value in the source.

    **Naming it makes it a duplicate.** With a `name`, the new column goes live
    *beside* the source when committed rather than replacing it, and the source is
    left exactly as it was — so this is equally "copy this column" and "copy it,
    converting on the way". Without a `name` it is a conversion, and the column
    keeps the source's identity.

    Both endings are switched off for now; the route answers 503 either way.
    """

    type: ColumnType
    config: ColumnConfig = Field(default_factory=ColumnConfig)
    name: Optional[NameSlug] = None
    display_name: Optional[str] = None

    @model_validator(mode="after")
    def _display_name_needs_a_name(self):
        # Otherwise a caller who sent only a display name gets a conversion when
        # they plainly meant a duplicate, and finds their column replaced.
        if self.display_name is not None and self.name is None:
            raise ValueError(
                "display_name is only meaningful with name, which is what makes "
                "this a duplicate rather than a conversion"
            )
        return self


class ConversionFailure(BaseModel):
    record_uuid: UUID
    message: str


class ConversionReport(BaseModel):
    """What a backfill did, for a person to read before committing to it.

    Nothing about the list has changed at this point: the values live in a hidden
    column that no view, filter, form or formula can see. Committing swaps them in;
    abandoning throws them away.
    """

    target_uuid: UUID
    # What the column becomes if this is committed. Echoed back rather than left
    # to the caller to remember, so a report is readable on its own.
    new_type: ColumnType
    # True when committing adds a column beside the source rather than replacing
    # it — which changes what the commit means, so the report says so.
    duplicate: bool
    converted: int
    failed: int
    errors: list[ConversionFailure]
    # True when more cells failed than the report names individually.
    errors_truncated: bool


class ColumnRead(BaseModel):
    uuid: UUID
    name: str
    display_name: str
    type: ColumnType
    position: int
    description: Optional[str] = None
    config: dict

    # `calculated` only, and always False for every other type: this column's stored
    # values are not known to match its formula as the schema stands now. Derived per
    # response from `backfill.pending_keys` rather than stored, so it cannot get stuck
    # on or be cleared while a second backfill is still running — see that function for
    # why the answer is deliberately pessimistic.
    #
    # It means "a recompute is running, or one did not finish", which are worth telling
    # apart but are not: the values are equally untrustworthy either way, and the
    # remedy — wait, reload, and run the maintenance script if it persists — is the
    # same. What it never means is that the *formula* is wrong.
    recompute_pending: bool = False

    model_config = ConfigDict(from_attributes=True)


class RestoredColumnRead(ColumnRead):
    """A restored column, plus what came back with it and what did not.

    Restoring only clears `deleted_at`. Deleting a column also nulls the list's
    `primary_column_id` if it pointed here, and un-nests every lookup whose pairing
    referenced it — neither is reversed, for the same reason a list's restore does not
    undo its cascade-scrub: nothing recorded what the state was, and guessing at it a
    month later is worse than saying so.

    The list's own field is at least *observable*, so the caller is told rather than
    left to notice. The un-nested lookups are not: the pairing was cleared on the
    dependents, and a lookup with no parent is indistinguishable from one that never
    had it. Reporting those would need the delete to write down what it broke.
    """

    # True when the list currently has no primary column — which this delete may have
    # caused. Deliberately about the list's state now, not about this column's history:
    # if a different primary was chosen while this sat in the bin, there is nothing to
    # fix and this is False.
    list_has_no_primary_column: bool
