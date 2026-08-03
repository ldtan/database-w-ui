from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# The most records one bulk-delete may name. Deliberately the same number as the
# item listing's own `limit` ceiling: a selection is made out of rows on screen,
# and no request can put more than a page of them there. It is a bound on the
# request's shape rather than on the work — the delete itself is a single UPDATE
# either way — and it exists because the permission batch builds one condition per
# record, which is a statement nobody should be able to make arbitrarily large.
BULK_DELETE_MAX = 1000


class BulkDeleteRequest(BaseModel):
    uuids: list[UUID] = Field(default_factory=list, max_length=BULK_DELETE_MAX)


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
