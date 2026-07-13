"""Addressing a list's children by uuid *or* by name.

`/lists/{list}/columns/019f52df-…` and `/lists/{list}/columns/first_name` both work.
The two can never be confused: a name is `^[a-z][a-z0-9_]*$` — no dashes, must start
with a letter — and a uuid always has dashes and hex, so a string either parses as a
uuid or it does not. That is the same uuid-or-label trick reference *values* already
use, which is why `coerce_uuid` returning None is the whole of the disambiguation.

**Only children.** Lists themselves are addressed by uuid alone, deliberately:

- A list's name is unique per *workspace*, but `/lists/{x}` carries no workspace, so
  `/lists/students` has no correct answer when the caller can see two of them.
- Guessable names turn a permission check into an enumeration oracle. `get_list_or_404`
  answers 404 for "no such list" and 403 for "exists, not yours" — harmless when the
  identifier is an unguessable uuid, an information leak the moment it is a word
  someone can guess. Children sit behind that same gate: reaching one of these routes
  at all means you already hold `view` on the list, so naming its columns tells you
  nothing you could not read anyway. A *view* or *form* can be restricted further than
  its list, and those two routes therefore answer **404, not 403**, to a caller who
  cannot view them — otherwise a guessed name would confirm that the name exists. See
  `_visible_or_404` in each of their routers.

The uuid stays canonical — it is what every response returns, and what the frontend
uses. A name is an *input* convenience for people writing API calls by hand. Names are
editable, so a name in a URL is a link that a rename can break; that is the usual slug
bargain, and the reason the UI never rewrites `name` when you rename a column.
"""
from typing import Optional, TypeVar, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.utils import coerce_uuid

T = TypeVar("T")


async def get_by_ref(
    db: AsyncSession, model: type[T], list_id: int, ref: Union[str, UUID]
) -> Optional[T]:
    """A list's child, addressed by uuid or by name. None if there is no such child.

    Live children only. The unique index on `name` is partial (`WHERE deleted_at IS
    NULL`), so a deleted `notes` column and a live one can coexist — a name must never
    resolve to something in the bin.
    """
    stmt = select(model).where(
        model.list_id == list_id, model.deleted_at.is_(None)
    )
    parsed = coerce_uuid(ref)
    stmt = stmt.where(model.uuid == parsed) if parsed else stmt.where(model.name == ref)
    return (await db.execute(stmt)).scalars().first()
