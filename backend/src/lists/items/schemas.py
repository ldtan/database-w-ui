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


class QrPayloadRequest(BaseModel):
    """Which records to build QR payloads for, and what goes in them."""

    # Same cap and the same knob as every other bulk body: a sheet of badges is a bulk
    # read, and there is no reason for it to disagree with the writes about how many
    # records one request may name. The frontend chunks against its mirror of it.
    item_uuids: list[UUID] = Field(
        default_factory=list, max_length=settings.BULK_DELETE_MAX
    )
    # Column **names**, not uuids — the same key an export's `columns` takes and the same
    # key the payload itself is written under, so one vocabulary covers the request, the
    # code, and the import that reads the code back.
    #
    # An unknown name is **ignored rather than refused** (`_ordered_columns` drops it).
    # A saved selection outlives the columns it names: renaming or deleting one should
    # quietly stop it appearing on the badge, not make every badge un-printable until
    # somebody edits a setting they have never seen.
    columns: list[str] = Field(default_factory=list)
    # Whether to include the record's own uuid, under `_uuid`. On by default: a badge that
    # cannot say which record it belongs to is only a label.
    include_uuid: bool = True


class QrPayload(BaseModel):
    """One record's QR payload.

    `item_uuid` is echoed outside `payload` as well as (usually) inside it, because the
    caller needs to match a code to the row it came from even when `include_uuid` is off.
    """

    item_uuid: UUID
    payload: dict


class BulkArchiveRequest(BaseModel):
    # Same cap as a bulk delete, and the same knob: both do one UPDATE over the
    # uuids given, so there is no reason for the two to disagree about how many is
    # too many, and one number is one thing for the frontend to mirror.
    uuids: list[UUID] = Field(
        default_factory=list, max_length=settings.BULK_DELETE_MAX
    )


class BulkArchiveResult(BaseModel):
    archived: int


class BulkRestoreRequest(BaseModel):
    # Same cap as the rest, and the same knob — but this is the one bulk body whose
    # cost is not a single UPDATE: see `ListItemService.bulk_restore` for the
    # uniqueness pass the cap is really bounding here.
    uuids: list[UUID] = Field(
        default_factory=list, max_length=settings.BULK_DELETE_MAX
    )


class BulkRestoreConflict(BaseModel):
    """A record the restore left in the bin, and why.

    Named for the record rather than for the error, like `ImportRowError`: the uuid
    is what the client has, and the message is written for the person who asked.
    """

    uuid: UUID
    message: str


class BulkRestoreResult(BaseModel):
    """Partial success, deliberately.

    A restore can fail per record — a `unique` value may have been taken while the
    row sat in the bin — and all-or-nothing would let one stale duplicate block the
    other 499. So the conflicts come back *beside* the count, the way the user batch
    import reports a bad row beside the rows that were saved. A bare count would
    hide them: "restored 497" does not say which three, or why.

    Silent skips are not conflicts and are not listed: an unknown uuid, one from
    another list, one that is not in the bin, and one past the retention window all
    simply do not appear in `restored`. Nothing was wrong with the request.
    """

    restored: int
    conflicts: list[BulkRestoreConflict] = Field(default_factory=list)


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
