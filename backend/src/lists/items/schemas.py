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


class MergedItemCreate(BaseModel):
    """One submit that writes a record here and a record in the linked list.

    The two sides are separate blobs rather than one, because `_validate_data` refuses
    a key that is not a column of the list it is validating — a guard worth keeping, so
    the split has to happen before validation rather than inside it.
    """

    # Which merged form this write is for. Its `merge` block decides the link column,
    # the linked list, and whether that list's records may be shared.
    form_uuid: UUID
    # This list's cells, keyed by column uuid.
    data: dict = Field(default_factory=dict)
    # The linked list's cells, keyed by *its* column uuids.
    linked_data: dict = Field(default_factory=dict)
    # An existing record to link to. None mints a new one from `linked_data`.
    linked_uuid: Optional[UUID] = None
    # Whether `linked_data` is written onto an existing `linked_uuid`. False by
    # default so a *create* here cannot silently rewrite a record that other rows may
    # already be showing — with `allow_shared` on, one edit would change all of them.
    # Turning it on raises what this write needs from `create` on the linked list to
    # `edit` on that particular record.
    update_linked: bool = False


class MergedItemUpdate(BaseModel):
    """Edit both sides of an existing pair.

    No `linked_uuid`: which record this row is paired with is the link cell's job, and
    moving it is an ordinary edit of that cell. Letting a merged update re-point the
    link *and* write through it in one request would make "whose values are these"
    depend on the order the server happened to apply them in.
    """

    form_uuid: UUID
    data: dict = Field(default_factory=dict)
    linked_data: dict = Field(default_factory=dict)
    permissions_inherit: Optional[bool] = None


class MergedItemRead(BaseModel):
    item: ListItemRead
    # None when the row is unlinked, or when the caller cannot view the linked
    # record — the same degradation `LookupCell` already makes on a 403, rather than
    # a refusal that would make an unreadable link unreadable for everyone.
    linked_item: Optional[ListItemRead] = None


class DeletedListItemRead(ListItemRead):
    # When the record was binned, so the UI can show "deleted 3 days ago" and how
    # much of the retention window is left. Always set for a bin listing.
    deleted_at: datetime


class ArchivedListItemRead(ListItemRead):
    # When the record was archived. There is no retention window to count down, so
    # this is only ever "archived 3 days ago". Always set for an archive listing.
    archived_at: datetime
