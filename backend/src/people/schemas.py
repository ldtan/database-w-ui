from datetime import date, timezone
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.schemas import ResourceLink


def _not_in_the_future(value: Optional[date]) -> Optional[date]:
    if value is not None and value > datetime.now(timezone.utc).date():
        raise ValueError("birth_date cannot be in the future")
    return value


class PersonBase(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    middle_name: Optional[str] = Field(default=None, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    birth_date: Optional[date] = None

    _check_birth_date = field_validator("birth_date")(_not_in_the_future)


class PersonCreate(PersonBase):
    # Defaults to the caller. Only a superuser may pass someone else's uuid.
    user_uuid: Optional[UUID] = None


class PersonUpdate(BaseModel):
    first_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    middle_name: Optional[str] = Field(default=None, max_length=100)
    last_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    birth_date: Optional[date] = None

    _check_birth_date = field_validator("birth_date")(_not_in_the_future)


class PersonRead(PersonBase):
    uuid: UUID
    user: ResourceLink

    model_config = ConfigDict(from_attributes=True)
