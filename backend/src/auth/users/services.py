from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.utils import LIKE_ESCAPE, like_pattern
from src.core.exceptions import (
    ConflictError,
    PermissionDeniedError,
    UnknownReferenceError,
)
from src.workspaces.services import WorkspaceService

from ..helpers import get_password_hash_async, verify_password_async
from .models import (
    AUTHENTICATED_USERS_DISPLAY_NAME,
    AUTHENTICATED_USERS_NAME,
    User,
    UserGroup,
)
from .schemas import (
    UserCreate,
    UserGroupCreate,
    UserGroupUpdate,
    UserSettingsUpdate,
    UserUpdate,
)


def _coerce_uuid(value: Union[str, UUID]) -> Optional[UUID]:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


class UserService:
    @staticmethod
    def _select():
        return (
            select(User)
            .where(User.deleted_at.is_(None))
            .options(selectinload(User.groups))
        )

    @staticmethod
    def _select_any():
        """Deleted accounts included — for resolving a uuid a record already holds."""
        return select(User).options(selectinload(User.groups))

    @classmethod
    async def get_by_uuid(cls, db: AsyncSession, uuid: Union[str, UUID]) -> Optional[User]:
        parsed = _coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select().where(User.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def get_by_username(cls, db: AsyncSession, username: str) -> Optional[User]:
        result = await db.execute(cls._select().where(User.username == username))
        return result.scalars().first()

    @classmethod
    async def get_by_email(cls, db: AsyncSession, email: str) -> Optional[User]:
        result = await db.execute(cls._select().where(User.email == email))
        return result.scalars().first()

    @staticmethod
    def _apply_search(stmt, q: Optional[str], search_person_names: bool):
        """Shared by list() and count() so the page and its total can never disagree.

        ``search_person_names`` widens the match to the user's connected person.
        Only pass it for a superuser: /auth/users is readable by any authenticated
        user, and person records are private — otherwise anyone could probe for
        someone's real name by watching which searches return a hit.
        """
        from src.people.models import Person

        if not (q and q.strip()):
            return stmt
        pattern = like_pattern(q.strip())
        conditions = [
            User.username.ilike(pattern, escape=LIKE_ESCAPE),
            User.email.ilike(pattern, escape=LIKE_ESCAPE),
        ]
        if search_person_names:
            stmt = stmt.outerjoin(
                Person,
                (Person.user_id == User.id) & (Person.deleted_at.is_(None)),
            )
            conditions += [
                Person.first_name.ilike(pattern, escape=LIKE_ESCAPE),
                Person.middle_name.ilike(pattern, escape=LIKE_ESCAPE),
                Person.last_name.ilike(pattern, escape=LIKE_ESCAPE),
            ]
        return stmt.where(or_(*conditions))

    @classmethod
    async def list(
        cls,
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        q: Optional[str] = None,
        search_person_names: bool = False,
        uuids: Optional[Sequence[UUID]] = None,
    ) -> Sequence[User]:
        """Matched server-side so a search can find users beyond the first page.

        ``uuids`` restricts the result to specific accounts — what a page showing
        `user` cells needs to turn the uuids it holds into names, without pulling
        the whole users table down to do it.
        """
        # Resolving specific accounts includes deleted ones: a `user` cell may hold
        # someone who has since been removed, and it still has to render their name
        # (as "(deleted)") rather than an unidentifiable uuid. A *search* only ever
        # offers live accounts — nothing new may be assigned to a deleted user.
        base = cls._select_any() if uuids is not None else cls._select()
        stmt = cls._apply_search(base, q, search_person_names)
        if uuids is not None:
            stmt = stmt.where(User.uuid.in_(uuids))
        result = await db.execute(stmt.order_by(User.id).offset(skip).limit(limit))
        return result.scalars().all()

    @classmethod
    async def count(
        cls,
        db: AsyncSession,
        q: Optional[str] = None,
        search_person_names: bool = False,
        uuids: Optional[Sequence[UUID]] = None,
    ) -> int:
        """Total matching rows, ignoring skip/limit — what the paginator needs.

        Takes the same filters as ``list``: a total that ignored one of them would
        describe a different query than the page it is sent with.
        """
        stmt = select(func.count(func.distinct(User.id))).select_from(User).where(
            User.deleted_at.is_(None)
        )
        if uuids is not None:
            stmt = stmt.where(User.uuid.in_(uuids))
        return await db.scalar(cls._apply_search(stmt, q, search_person_names)) or 0

    @classmethod
    async def create(cls, db: AsyncSession, payload: UserCreate) -> User:
        user = User(
            username=payload.username,
            email=payload.email,
            password=await get_password_hash_async(payload.password),
            is_active=payload.is_active,
        )
        db.add(user)
        try:
            await db.flush()
            # Read the generated identifiers before commit expires the instance.
            new_uuid = user.uuid
            new_id = user.id
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("Username or email is already in use") from exc

        # Bootstrap a personal workspace so the account can create lists right away.
        await WorkspaceService.create_default(db, new_id)

        return await cls.get_by_uuid(db, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        uuid: Union[str, UUID],
        payload: UserUpdate,
        verify_current_password: bool = True,
    ) -> Optional[User]:
        """``verify_current_password`` is False only when a superuser is resetting
        someone else's password — they cannot be expected to know it."""
        user = await cls.get_by_uuid(db, uuid)
        if user is None:
            return None

        data = payload.model_dump(exclude_unset=True, exclude_none=True)
        password = data.pop("password", None)
        current_password = data.pop("current_password", None)
        if password is not None:
            # Knowing the current password is what proves this is the account holder
            # and not someone who merely got hold of a token.
            if verify_current_password and (
                current_password is None
                or not await verify_password_async(current_password, user.password)
            ):
                raise PermissionDeniedError("current_password is incorrect")
            user.password = await get_password_hash_async(password)

        if data.get("is_superuser") is False and user.is_superuser:
            await cls._guard_last_superuser(db, user)
        if data.get("is_active") is False and user.is_superuser:
            await cls._guard_last_superuser(db, user)

        for field, value in data.items():
            setattr(user, field, value)

        try:
            await db.flush()
            target_uuid = user.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("Username or email is already in use") from exc

        return await cls.get_by_uuid(db, target_uuid)

    @classmethod
    async def update_settings(
        cls,
        db: AsyncSession,
        uuid: Union[str, UUID],
        payload: UserSettingsUpdate,
    ) -> Optional[User]:
        """Merge UI preferences onto the stored blob.

        Deliberately not routed through `update`: that method carries the password
        and superuser guards, and its `exclude_none=True` cannot express "this key
        was sent" separately from "this key is null", which is exactly the
        distinction a merge needs.
        """
        user = await cls.get_by_uuid(db, uuid)
        if user is None:
            return None

        # exclude_unset, not exclude_none: a key the caller omitted keeps its stored
        # value, so a tab that only knows about `mode` cannot wipe a `theme` set
        # elsewhere. Assigning a *new* dict is required — a plain JSONB-mapped dict
        # is not mutation-tracked, so an in-place update would never flush.
        incoming = payload.model_dump(exclude_unset=True, mode="json")
        user.settings = {**(user.settings or {}), **incoming}

        await db.flush()
        target_uuid = user.uuid
        await db.commit()

        return await cls.get_by_uuid(db, target_uuid)

    @classmethod
    async def delete(cls, db: AsyncSession, uuid: Union[str, UUID]) -> bool:
        user = await cls.get_by_uuid(db, uuid)
        if user is None:
            return False

        if user.is_superuser:
            await cls._guard_last_superuser(db, user)

        user.deleted_at = datetime.now(timezone.utc)
        # Memberships are kept. They used to be cleared here so the account stopped
        # appearing in UserGroupRead.users, but that made the delete lossy in a way
        # nothing recorded: a restore brought the account back with no groups and no
        # way to know it ever had any. The relationship filters deleted rows out of
        # the join instead (see models.py), so it reads the same and undoes cleanly.
        # `user` cells still holding this uuid are left alone for the same reason, and
        # render as "(deleted)" until the account is purged. See
        # src/maintenance/purge_deleted_principals.py.
        await db.commit()
        return True

    @staticmethod
    async def _guard_last_superuser(db: AsyncSession, user: User) -> None:
        """Refuse to remove the system's only administrator.

        Superusers are only ever minted two ways: seeded into an empty users table,
        or promoted by an existing superuser. Losing the last one is therefore
        unrecoverable through the API — and with no migration tool, awkward to undo
        by hand.
        """
        remaining = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.is_superuser.is_(True),
                User.is_active.is_(True),
                User.deleted_at.is_(None),
                User.id != user.id,
            )
        )
        if not remaining:
            raise PermissionDeniedError(
                "This is the only superuser; promote another before removing this one"
            )


class UserGroupService:
    @staticmethod
    def _select():
        return (
            select(UserGroup)
            .where(UserGroup.deleted_at.is_(None))
            .options(selectinload(UserGroup.users))
        )

    @staticmethod
    def _guard_system_group(group: UserGroup) -> None:
        if group.is_system:
            raise PermissionDeniedError(
                f"System group {AUTHENTICATED_USERS_DISPLAY_NAME!r} is immutable"
            )

    @staticmethod
    def _select_any():
        """Deleted groups included — for resolving a uuid a record already holds."""
        return select(UserGroup).options(selectinload(UserGroup.users))

    @staticmethod
    def _apply_search(stmt, q: Optional[str]):
        """Shared by list() and count() so the page and its total cannot drift.

        Both names, because they diverged: someone typing "Registrar" is reading the
        display name off the People page, and matching only the slug would answer
        "no such group" about a group they are looking at.
        """
        if not (q and q.strip()):
            return stmt
        pattern = like_pattern(q.strip())
        return stmt.where(
            or_(
                UserGroup.name.ilike(pattern, escape=LIKE_ESCAPE),
                UserGroup.display_name.ilike(pattern, escape=LIKE_ESCAPE),
                UserGroup.description.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )

    @staticmethod
    async def _resolve_users(
        db: AsyncSession, user_uuids: Optional[Sequence[Union[str, UUID]]]
    ) -> list[User]:
        if not user_uuids:
            return []

        parsed: list[UUID] = []
        for raw in user_uuids:
            value = _coerce_uuid(raw)
            if value is None:
                raise UnknownReferenceError(f"Malformed user uuid: {raw}")
            parsed.append(value)

        result = await db.execute(
            select(User).where(User.uuid.in_(parsed), User.deleted_at.is_(None))
        )
        users = list(result.scalars().all())

        found = {user.uuid for user in users}
        missing = [str(value) for value in parsed if value not in found]
        if missing:
            raise UnknownReferenceError(f"Unknown user uuid(s): {', '.join(missing)}")
        return users

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, uuid: Union[str, UUID]
    ) -> Optional[UserGroup]:
        parsed = _coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select().where(UserGroup.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def get_by_name(cls, db: AsyncSession, name: str) -> Optional[UserGroup]:
        result = await db.execute(cls._select().where(UserGroup.name == name))
        return result.scalars().first()

    @classmethod
    async def list(
        cls,
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        q: Optional[str] = None,
        uuids: Optional[Sequence[UUID]] = None,
    ) -> Sequence[UserGroup]:
        base = cls._select_any() if uuids is not None else cls._select()
        stmt = cls._apply_search(base, q)
        if uuids is not None:
            stmt = stmt.where(UserGroup.uuid.in_(uuids))
        result = await db.execute(
            stmt.order_by(UserGroup.id).offset(skip).limit(limit)
        )
        return result.scalars().all()

    @classmethod
    async def count(
        cls,
        db: AsyncSession,
        q: Optional[str] = None,
        uuids: Optional[Sequence[UUID]] = None,
    ) -> int:
        stmt = select(func.count(UserGroup.id)).select_from(UserGroup).where(
            UserGroup.deleted_at.is_(None)
        )
        if uuids is not None:
            stmt = stmt.where(UserGroup.uuid.in_(uuids))
        return await db.scalar(cls._apply_search(stmt, q)) or 0

    @classmethod
    async def create(cls, db: AsyncSession, payload: UserGroupCreate) -> UserGroup:
        if payload.name == AUTHENTICATED_USERS_NAME:
            raise PermissionDeniedError(
                f"Group name {AUTHENTICATED_USERS_NAME!r} is reserved"
            )

        users = await cls._resolve_users(db, payload.user_uuids)
        group = UserGroup(
            name=payload.name,
            display_name=payload.display_name,
            description=payload.description,
            users=users,
        )
        db.add(group)
        try:
            await db.flush()
            new_uuid = group.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("Group name is already in use") from exc

        return await cls.get_by_uuid(db, new_uuid)

    @classmethod
    async def update(
        cls, db: AsyncSession, uuid: Union[str, UUID], payload: UserGroupUpdate
    ) -> Optional[UserGroup]:
        group = await cls.get_by_uuid(db, uuid)
        if group is None:
            return None

        cls._guard_system_group(group)

        data = payload.model_dump(exclude_unset=True)
        if "user_uuids" in data:
            group.users = await cls._resolve_users(db, data.pop("user_uuids"))
            # `_group_ids` caches membership for the life of the session, so the
            # answer it holds is now the one from before this edit. Deferred import:
            # permissions.services imports this module, so a module-level one cycles.
            from src.permissions.services import PermissionService

            PermissionService.forget_group_ids(db)
        # Both are NOT NULL, and `exclude_unset` cannot tell an omitted key from one
        # explicitly set to null — so an explicit null would reach the database as a
        # constraint violation rather than as the no-op the caller plainly meant.
        for required in ("name", "display_name"):
            if required in data and data[required] is None:
                data.pop(required)
        for field, value in data.items():
            setattr(group, field, value)

        try:
            await db.flush()
            target_uuid = group.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("Group name is already in use") from exc

        return await cls.get_by_uuid(db, target_uuid)

    @classmethod
    async def delete(cls, db: AsyncSession, uuid: Union[str, UUID]) -> bool:
        group = await cls.get_by_uuid(db, uuid)
        if group is None:
            return False

        cls._guard_system_group(group)

        group.deleted_at = datetime.now(timezone.utc)
        # Kept, for the reason UserService.delete gives. The group stops appearing in
        # UserRead.groups because the relationship filters it, and stops conferring
        # anything because PermissionService._group_ids checks the same thing — that
        # second one is why this is a security change and not only a cosmetic one.
        # Which is exactly why the cache in front of that check has to be dropped
        # here: it holds ids resolved while the group was still live.
        from src.permissions.services import PermissionService

        PermissionService.forget_group_ids(db)
        await db.commit()
        return True
