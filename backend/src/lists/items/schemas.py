from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BulkDeleteRequest(BaseModel):
    # 500 is the house cap for a bulk body (permissions/schemas.py, the user and
    # column bulk endpoints). Without it the caller sizes the server's work: every
    # uuid is a row to resolve and update, and nothing else on the request bounds
    # how many arrive. The grid pages at 100 at most, so this is far above any
    # selection the UI can produce.
    uuids: list[UUID] = Field(default_factory=list, max_length=500)


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
