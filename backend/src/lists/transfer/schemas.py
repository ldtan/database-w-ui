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


class ImportError(BaseModel):
    row: int
    message: str


class ImportSummary(BaseModel):
    created: int
    errors: list[ImportError] = Field(default_factory=list)
