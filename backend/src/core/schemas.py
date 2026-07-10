from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ResourceLink(BaseModel):
    uuid: UUID

    model_config = ConfigDict(from_attributes=True)
