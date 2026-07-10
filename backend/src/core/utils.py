from typing import Optional, Union
from uuid import UUID


def coerce_uuid(value: Union[str, UUID]) -> Optional[UUID]:
    """Best-effort parse of a value into a UUID, returning None if malformed."""
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None
