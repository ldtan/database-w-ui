from typing import Literal, Optional

from pydantic import BaseModel, Field

from ..views.schemas import FilterGroup, SortSpec


class ExportRequest(BaseModel):
    """Ad-hoc export selection. Columns are named by their ``name`` (not
    display name); an empty list means every column in position order."""

    format: Literal["csv", "json"] = "csv"
    columns: list[str] = Field(default_factory=list)
    filters: FilterGroup = Field(default_factory=FilterGroup)
    sorts: list[SortSpec] = Field(default_factory=list)
    limit: Optional[int] = Field(default=None, ge=1)  # None = all matching rows
    # Archived records are left out by default, because an export is a read of the
    # list and every other read excludes them. The flag exists so that archiving
    # cannot make records permanently unexportable — without it the archive would be
    # a one-way door for anyone trying to get their data out.
    include_archived: bool = False


class ImportRowError(BaseModel):
    """A row the import skipped, and enough of it to fix and send back.

    ``values`` is the row exactly as the file gave it — every header, including
    ones this list has no column for — stringified the way `export` writes cells,
    so a client can put the failures back into a file that imports cleanly once
    the bad cells are corrected. Named for the row rather than for the error, and
    not `ImportError`, which is a builtin.
    """

    row: int
    message: str
    values: dict[str, str] = Field(default_factory=dict)


class ImportSummary(BaseModel):
    created: int
    errors: list[ImportRowError] = Field(default_factory=list)
