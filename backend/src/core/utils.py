from typing import Optional, Union
from uuid import UUID

# Escape character for LIKE/ILIKE patterns built from user input.
LIKE_ESCAPE = "\\"


def coerce_uuid(value: Union[str, UUID]) -> Optional[UUID]:
    """Best-effort parse of a value into a UUID, returning None if malformed."""
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def like_pattern(term: str) -> str:
    """Build a `%term%` ILIKE pattern from a search box.

    `%` and `_` are wildcards in LIKE, so a user typing them would otherwise get
    surprising matches (a lone `%` matching everything). Escape them, and the
    escape character itself, so the term is matched literally. Pair with
    `.ilike(pattern, escape=LIKE_ESCAPE)`.
    """
    escaped = (
        term.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", f"{LIKE_ESCAPE}%")
        .replace("_", f"{LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"
