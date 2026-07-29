from enum import Enum


class BinnedKind(str, Enum):
    """The list children that have a bin of their own.

    Records and columns are deliberately absent, for different reasons. Records
    already have one, older and shaped around per-record permissions, at
    ``/lists/{uuid}/items/deleted``. Columns are schema rather than config: a record
    keeps a deleted column's values in its ``data`` JSONB, so restoring a column
    un-hides data that may have been hidden on purpose, and destroying one has to
    strip that key from every row it appears in. That is a different decision from
    restoring a saved view, and it is not this surface's to make.
    """

    views = "views"
    forms = "forms"
    metrics = "metrics"
