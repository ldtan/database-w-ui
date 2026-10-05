from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.config import settings


class BulkDeleteRequest(BaseModel):
    # Without a cap the caller sizes the server's work: every uuid is a row to
    # resolve and update, and nothing else on the request bounds how many arrive.
    # 500 is the house default for a bulk body (permissions/schemas.py, the user and
    # column bulk endpoints), and far above any selection the grid can produce at a
    # page size of 100.
    #
    # Read once, at import, which is what `max_length` requires — so this limit is
    # fixed for the life of the process and appears in the OpenAPI schema. See
    # `BULK_DELETE_MAX` in config.py for why it may be raised but not lowered
    # without changing the frontend too.
    uuids: list[UUID] = Field(
        default_factory=list, max_length=settings.BULK_DELETE_MAX
    )


class BulkDeleteResult(BaseModel):
    deleted: int


class BulkArchiveRequest(BaseModel):
    # Same cap as a bulk delete, and the same knob: both do one UPDATE over the
    # uuids given, so there is no reason for the two to disagree about how many is
    # too many, and one number is one thing for the frontend to mirror.
    uuids: list[UUID] = Field(
        default_factory=list, max_length=settings.BULK_DELETE_MAX
    )


class BulkArchiveResult(BaseModel):
    archived: int


class BulkUnarchiveRequest(BaseModel):
    # Same cap again, for the reason `BulkArchiveRequest` gives: the frontend mirrors
    # one number, so a surface that chunked against a different one would be a second
    # thing to keep in step for no gain.
    uuids: list[UUID] = Field(
        default_factory=list, max_length=settings.BULK_DELETE_MAX
    )


class BulkUnarchiveResult(BaseModel):
    # A count and nothing else, unlike a bulk restore's. Coming back from the archive
    # cannot fail per record — an archived row never released its `unique` values, so
    # there is no conflict to report (see `ListItemService.unarchive`).
    unarchived: int


class ListItemCountRead(BaseModel):
    total: int


class ListItemCreate(BaseModel):
    # Keys are column uuids (as strings); values are validated per column type.
    data: dict = Field(default_factory=dict)


class ListItemUpdate(BaseModel):
    # Partial: only the provided keys are merged onto the existing record.
    data: dict = Field(default_factory=dict)
    # Whether this record inherits permission grants from its list. Changing it
    # requires 'share' on the record.
    permissions_inherit: Optional[bool] = None


class ListItemRead(BaseModel):
    uuid: UUID
    data: dict
    permissions_inherit: bool = True

    model_config = ConfigDict(from_attributes=True)


class DeletedListItemRead(ListItemRead):
    # When the record was binned, so the UI can show "deleted 3 days ago" and how
    # much of the retention window is left. Always set for a bin listing.
    deleted_at: datetime


class ArchivedListItemRead(ListItemRead):
    # When the record was archived. There is no retention window to count down, so
    # this is only ever "archived 3 days ago". Always set for an archive listing.
    archived_at: datetime
