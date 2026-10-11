from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from src.people.schemas import PersonCreate

# The spellings table import already accepts, so someone who has used that file format
# does not have to learn a second one. See tables/transfer/services.py::_parse_cell.
_TRUE = {"true", "1", "yes", "y"}
_FALSE = {"false", "0", "no", "n"}


class BatchUserRow(BaseModel):
    """One validated line of the upload.

    Carries a fully-validated ``PersonCreate`` rather than loose name fields, so the
    profile rules -- lengths, both names required, birth date not in the future --
    are the *same* ones the /people endpoint applies. There is no second copy to
    drift out of step.

    ``extra="forbid"`` is belt-and-braces: the parser only ever passes keys it knows,
    so this fires when a header is added to ``KNOWN_HEADERS`` without a field to
    receive it -- which would otherwise be a column that silently does nothing.
    """

    username: str = Field(min_length=1, max_length=50)
    email: EmailStr
    # None means "the server should mint one". The summary reports which rows got one.
    password: Optional[str] = None
    is_active: bool = True
    is_superuser: bool = False
    groups: list[str] = Field(default_factory=list)
    person: Optional[PersonCreate] = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("is_active", "is_superuser", mode="before")
    @classmethod
    def _coerce_bool(cls, value: object) -> object:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError(f"'{value}' is not true/false; use true/false, 1/0 or yes/no")

    @field_validator("groups", mode="before")
    @classmethod
    def _split_groups(cls, value: object) -> object:
        if isinstance(value, list):
            return value
        return [name.strip() for name in str(value).split(";") if name.strip()]


class BatchUserError(BaseModel):
    row: int
    message: str


class CreatedUser(BaseModel):
    row: int
    uuid: UUID
    username: str
    # Set only when the server minted the password. Returned exactly once and never
    # stored -- there is no way to ask for it again, by design.
    generated_password: Optional[str] = None


class BatchUserSummary(BaseModel):
    created: list[CreatedUser] = Field(default_factory=list)
    errors: list[BatchUserError] = Field(default_factory=list)
    # Reported on its own so a typo'd `is_superuser` column is visible in the result
    # rather than buried among the successes.
    superusers_created: int = 0
