from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.core.config import settings

from ..views.schemas import FilterGroup, SortSpec


class ExportRequest(BaseModel):
    """Ad-hoc export selection. Columns are named by their ``name`` (not
    display name); an empty table means every column in position order."""

    format: Literal["csv", "json"] = "csv"
    columns: list[str] = Field(default_factory=list)
    filters: FilterGroup = Field(default_factory=FilterGroup)
    sorts: list[SortSpec] = Field(default_factory=list)
    limit: Optional[int] = Field(default=None, ge=1)  # None = all matching rows
    # Archived records are left out by default, because an export is a read of the
    # table and every other read excludes them. The flag exists so that archiving
    # cannot make records permanently unexportable — without it the archive would be
    # a one-way door for anyone trying to get their data out.
    include_archived: bool = False


class ImportRowError(BaseModel):
    """A row the import skipped, and enough of it to fix and send back.

    ``values`` is the row exactly as the file gave it — every header, including
    ones this table has no column for — stringified the way `export` writes cells,
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


class BulkCreateRequest(BaseModel):
    """Records to create, as objects of column **name** to value.

    The same shape a JSON import file carries, which is the point: this is the
    import without the multipart envelope, for a client that has records in hand
    rather than a file to upload. It goes through `import_records` unchanged, so
    the coercion a file gets — `1:30` as a duration, `a;b` split for a `multiple`
    column, a lookup cell given as a *label* — is the coercion a body gets, and
    there is no second parser to keep in step.

    ``max_length`` rather than only `import_records`' own check, which stays as
    the authority for the upload route. A JSON array can be measured by the schema
    before anything is read, so the limit is published in the OpenAPI document and
    costs nothing to enforce; an over-long body is a 422 here where an over-long
    *file* is the 400 the route words itself. Total body size is bounded
    separately and bluntly by `MAX_REQUEST_BYTES`.
    """

    records: list[dict] = Field(
        default_factory=list, max_length=settings.IMPORT_MAX_ROWS
    )
