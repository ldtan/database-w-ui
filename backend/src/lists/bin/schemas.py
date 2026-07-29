from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BinnedChildRead(BaseModel):
    """One soft-deleted view, form or metric.

    Narrower than the live read schemas on purpose, and the same shape for all three.
    A bin row exists to be recognised and then restored or destroyed, so it carries
    what identifies it and when it went -- not its ``config``. One schema is also what
    lets the three kinds share a route; the price is that ``config`` is not previewable
    from the bin, which is the right trade for a recovery surface.
    """

    uuid: UUID
    name: str
    display_name: str
    description: Optional[str] = None
    # When it was binned, so a caller can show "deleted 3 days ago" and how much of
    # the retention window is left -- the field DeletedListItemRead carries for the
    # same reason. Always set here: the listing selects on it being non-null.
    deleted_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RestoredChildRead(BaseModel):
    """What a restore hands back: enough to confirm which row came back, and to
    address it.

    No ``deleted_at`` -- it is null again, which is the whole point. No ``config``
    either: the child's own endpoint is live the moment this returns, and is the one
    place that describes each kind with its real schema.
    """

    uuid: UUID
    name: str
    display_name: str
    description: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
