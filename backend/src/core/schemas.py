from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints


# The code-facing identifier for lists/columns/views/forms: a slug that must
# start with a lowercase letter and contain only lowercase letters, digits, and
# underscores. `display_name` (free text) is what the UI shows.
NameSlug = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=63),
]

# The human-facing label, bounded to what the column can actually hold.
#
# Every model storing one declares `sa.String(255)`, and every schema accepting one
# used to declare a bare `str` — so a longer value passed validation, reached
# Postgres, and came back as asyncpg's `StringDataRightTruncationError`. That
# surfaces as SQLAlchemy's `DataError`, which is **not** an `IntegrityError`, so the
# `except IntegrityError` each service wraps its flush in did not catch it and the
# request 500'd on what is plainly a bad argument.
#
# `conversion_service` already worked around this locally — it truncates a source
# name before building `<name>__before_conversion` and its comment notes that
# `ColumnCreate.display_name` has no `max_length`, "so such a column is creatable
# today". This is that root cause, fixed once for all six schemas that carry one:
# columns, lists, views, forms, metrics and workspaces.
DisplayName = Annotated[str, StringConstraints(max_length=255)]


class ResourceLink(BaseModel):
    uuid: UUID

    model_config = ConfigDict(from_attributes=True)
