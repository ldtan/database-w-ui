from typing import Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.schemas import DisplayName, NameSlug


class MetricConfig(BaseModel):
    """What a metric is and how its number should read.

    Typed rather than a bare ``dict`` so the generated OpenAPI schema carries the
    shape through to the frontend — `ColumnRead.config` is a `dict`, which is why
    the frontend has to restate it by hand.
    """

    # A metric *is* its formula; the rest is presentation.
    formula: str
    # Decimal places. None leaves the value as computed, which is what a count
    # wants and what a currency does not.
    precision: Optional[int] = Field(default=None, ge=0, le=10)
    prefix: Optional[str] = Field(default=None, max_length=16)
    suffix: Optional[str] = Field(default=None, max_length=16)

    # No `help_text` here: a metric already has a `description` column, the same
    # one views and forms carry. Two places to write the same sentence is how they
    # end up disagreeing.
    model_config = ConfigDict(extra="forbid")


class MetricCreate(BaseModel):
    name: NameSlug
    display_name: DisplayName
    position: int = 0
    description: Optional[str] = None
    config: MetricConfig


class MetricUpdate(BaseModel):
    name: Optional[NameSlug] = None
    display_name: Optional[DisplayName] = None
    position: Optional[int] = None
    description: Optional[str] = None
    config: Optional[MetricConfig] = None


class MetricRead(BaseModel):
    uuid: UUID
    name: str
    display_name: str
    position: int
    description: Optional[str] = None
    config: MetricConfig

    model_config = ConfigDict(from_attributes=True)


class MetricValue(BaseModel):
    """One metric's answer over the records in scope.

    ``value`` and ``error`` are alternatives: a formula that no longer matches the
    list (a column it reads was renamed or removed) reports the reason rather than
    a number, because a broken ``SUM`` rendering as ``0`` is worse than one that
    says it is broken.
    """

    uuid: UUID
    name: str
    # `int` leads the union so a count stays a count: without it Pydantic coerces
    # COUNT()'s 4 to 4.0, and a card reading "4.0 records" is a wrong answer to a
    # question about how many things there are.
    value: Union[int, float, str, None] = None
    error: Optional[str] = None


class MetricValues(BaseModel):
    # How many records the answers are over — the count after permissions, the
    # view's filters and the search, so it need not match the list's total.
    row_count: int
    # True when the scan stopped at its row limit, so these are the answers over
    # the first `row_count` records rather than over all of them.
    truncated: bool = False
    values: list[MetricValue] = Field(default_factory=list)
