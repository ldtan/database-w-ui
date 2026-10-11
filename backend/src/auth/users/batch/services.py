"""Create many accounts from one uploaded file.

Every row goes through the same services a single Add user does -- UserService,
PersonService -- so a batch-created account is indistinguishable from a form-created
one: same argon2 hashing, same conflict message, same personal workspace. Inlining
account creation here would be faster and is the wrong trade: it is how batch quietly
stops bootstrapping a workspace months after anyone remembers there are two paths.

``UserService.create`` and ``WorkspaceService.create`` each commit internally, so
every row is already its own transaction. That is exactly the isolation
skip-and-report wants, which is why there is no savepoint machinery here -- unlike
table import, which inserts into one session and commits in batches for speed.
"""
import secrets
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ServiceError, UnknownReferenceError
from src.people.services import PersonService

from ..models import UserGroup
from ..schemas import UserCreate, UserUpdate
from ..services import UserService
from .parsing import ParsedFile
from .schemas import BatchUserError, BatchUserSummary, CreatedUser

# 16 URL-safe characters, 96 bits of entropy. Long enough that it never needs a
# strength policy, short enough to read off a page and type.
_PASSWORD_BYTES = 12


async def resolve_groups(db: AsyncSession, names: set[str]) -> dict[str, UserGroup]:
    """Every group named anywhere in the file, in one query rather than one per row.

    This is what parsing the whole upload up front buys: the names are all known
    before the first account is created.

    Keyed on the slug `name`, never the display name. The whole point of the split is
    that a file names a group by something a cosmetic rename cannot invalidate, so
    accepting the display name here would hand that back.
    """
    if not names:
        return {}
    result = await db.execute(
        select(UserGroup).where(
            UserGroup.name.in_(names), UserGroup.deleted_at.is_(None)
        )
    )
    return {group.name: group for group in result.scalars().all()}


async def suggest_slugs(db: AsyncSession, misses: set[str]) -> dict[str, str]:
    """Display name -> slug, for the values in the file that matched no group.

    Groups were addressed by their display name until the name/display_name split, so
    the likeliest reason a value misses is that the file predates it. Rather than
    quietly accepting the old spelling -- which would restore the ambiguity the split
    removed -- the error names the slug to put in its place.

    Case-insensitive, because a display name is free text and nobody transcribing one
    into a spreadsheet is thinking about case. One query for the whole file, matching
    `resolve_groups` above; an empty result just means no suggestion is offered.
    """
    if not misses:
        return {}
    lowered = {name.lower() for name in misses}
    result = await db.execute(
        select(UserGroup).where(
            sa.func.lower(UserGroup.display_name).in_(lowered),
            UserGroup.deleted_at.is_(None),
        )
    )
    return {group.display_name.lower(): group.name for group in result.scalars().all()}


def _unknown_group_message(unknown: list[str], suggestions: dict[str, str]) -> str:
    """The per-row error, carrying the right slug when one can be worked out."""
    named = ", ".join(unknown)
    hints = [suggestions[name.lower()] for name in unknown if name.lower() in suggestions]
    if not hints:
        return f"Unknown group(s): {named}"
    return f"Unknown group(s): {named}. Did you mean: {', '.join(hints)}?"


async def _undo_row(db: AsyncSession, user_uuid: UUID) -> None:
    """Take back an account whose row failed after it had been created.

    Keeps the promise the summary makes -- a reported row created nothing. Validating
    the whole row in memory first makes this nearly unreachable; what is left is
    infrastructure failure and a group deleted mid-import. Rolling back first is what
    makes the delete possible at all, since the session may still be holding the
    failure that got us here.
    """
    try:
        await db.rollback()
        await UserService.delete(db, user_uuid)
    except Exception:
        # Nothing better is available, and the caller still has to hear about the
        # error that got us here -- which is the more useful of the two.
        pass


async def import_users(db: AsyncSession, parsed: ParsedFile) -> BatchUserSummary:
    """Create an account per parsed row, reporting the ones that could not be."""
    wanted = {name for _, row in parsed.rows for name in row.groups}
    groups_by_name = await resolve_groups(db, wanted)
    # Resolved once for the file, not once per bad row: a roster written against the
    # old free-text names misses on *every* row, and that is exactly the file whose
    # error messages have to be worth reading.
    suggestions = await suggest_slugs(db, wanted - set(groups_by_name))

    created: list[CreatedUser] = []
    errors: list[BatchUserError] = list(parsed.errors)
    superusers_created = 0

    for number, row in parsed.rows:
        password = row.password or secrets.token_urlsafe(_PASSWORD_BYTES)
        try:
            unknown = [name for name in row.groups if name not in groups_by_name]
            if unknown:
                raise UnknownReferenceError(
                    _unknown_group_message(unknown, suggestions)
                )
            user = await UserService.create(
                db,
                UserCreate(
                    username=row.username,
                    email=row.email,
                    password=password,
                    is_active=row.is_active,
                ),
            )
        except ServiceError as exc:
            errors.append(BatchUserError(row=number, message=str(exc)))
            continue

        # Read the identifiers now and use them from here on: the row still has
        # commits ahead of it, and what gets reported has to survive them.
        user_uuid, user_id, username = user.uuid, user.id, user.username
        try:
            if row.person is not None:
                await PersonService.create(db, user_id, row.person)
            if row.groups:
                # Assigned on the *user*, not through UserGroupService.update, which
                # sets a group's entire membership from a uuid list -- calling that
                # per row would erase everyone already in the group.
                user.groups = [groups_by_name[name] for name in row.groups]
                await db.commit()
            if row.is_superuser:
                # UserCreate has no is_superuser field on purpose ("an account cannot
                # be *born* a superuser"), so this mirrors the follow-up PATCH the Add
                # user dialog already does. Last in the sequence, which is what keeps
                # _undo_row clear of _guard_last_superuser.
                await UserService.update(db, user_uuid, UserUpdate(is_superuser=True))
                superusers_created += 1
        except Exception as exc:
            await _undo_row(db, user_uuid)
            errors.append(BatchUserError(row=number, message=str(exc) or repr(exc)))
            continue

        created.append(
            CreatedUser(
                row=number,
                uuid=user_uuid,
                username=username,
                generated_password=None if row.password else password,
            )
        )

    # Parse errors were collected before any of these, so sorting is what puts the
    # two kinds back into the order the file reads in.
    errors.sort(key=lambda error: error.row)
    return BatchUserSummary(
        created=created, errors=errors, superusers_created=superusers_created
    )
