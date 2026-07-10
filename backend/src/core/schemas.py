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


class ResourceLink(BaseModel):
    uuid: UUID

    model_config = ConfigDict(from_attributes=True)
