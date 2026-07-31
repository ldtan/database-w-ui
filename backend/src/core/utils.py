import re
from typing import Optional, Union
from uuid import UUID

# Escape character for LIKE/ILIKE patterns built from user input.
LIKE_ESCAPE = "\\"

_NON_SLUG = re.compile(r"[^a-z0-9]+")


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


def slugify_name(value: str, max_length: int = 63) -> str:
    """Turn free text into a `NameSlug` (`^[a-z][a-z0-9_]*$`, see core/schemas.py).

    The server-side twin of `slugify` in frontend/src/lib/slug.ts, and deliberately
    rule-for-rule identical to it — including the `x_` prefix and the `item`
    fallback, which are that file's choices rather than this one's. A group created
    through the browser and a group renamed by a backfill must land on the same slug,
    and they only do if there is one transform spelled twice rather than two.

    Truncation happens last so the result always fits `NameSlug.max_length`; callers
    that must also be unique (the group backfill) dedupe on top of this, which is why
    the caller passes a shortened `max_length` to leave room for its suffix.
    """
    slug = _NON_SLUG.sub("_", value.lower()).strip("_")
    if not slug:
        return "item"
    if not slug[0].isalpha():
        slug = f"x_{slug}"
    return slug[:max_length].rstrip("_") or "item"
