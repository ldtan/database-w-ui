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
