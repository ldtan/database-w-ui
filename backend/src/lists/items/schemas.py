from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ListItemCreate(BaseModel):
    # Keys are column uuids (as strings); values are validated per column type.
    data: dict = Field(default_factory=dict)


class ListItemUpdate(BaseModel):
    # Partial: only the provided keys are merged onto the existing record.
    data: dict = Field(default_factory=dict)


class ListItemRead(BaseModel):
    uuid: UUID
    data: dict

    model_config = ConfigDict(from_attributes=True)
