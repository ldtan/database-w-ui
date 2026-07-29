from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BinnedChildRead(BaseModel):
    """One soft-deleted view, form or metric.

    Narrower than the live read schemas on purpose, and the same shape for all three.
    A bin row exists to be recognised and then restored or destroyed, so it carries
    what identifies it and when it went -- not its ``config``. The cost is that a
    config is not previewable from the bin, which is the right trade for a recovery
    surface: restoring is cheap and reversible, so the way to inspect one is to bring
    it back and look at the real thing.

    A restore answers with the kind's own read schema rather than this one -- the row
    is live again by then, and there is no reason for the caller to see less of it
    than a ``GET`` would show.
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
