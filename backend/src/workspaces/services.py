from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.exceptions import ConflictError, PermissionDeniedError
from src.core.utils import LIKE_ESCAPE, coerce_uuid, like_pattern

from .models import Workspace
from .schemas import WorkspaceCreate, WorkspaceUpdate

# The reserved name/label of every user's "Incoming" workspace — the immutable drop
# point transferred lists land in (see get_or_create_inbox). The name is reserved so
# an ordinary workspace can never occupy the slot (see create).
INBOX_NAME = "incoming"
INBOX_DISPLAY = "Incoming"


class WorkspaceService:
    @staticmethod
    def _select(owner_id: int):
        return select(Workspace).where(
            Workspace.deleted_at.is_(None), Workspace.owner_id == owner_id
        )

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, owner_id: int, uuid: Union[str, UUID]
    ) -> Optional[Workspace]:
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select(owner_id).where(Workspace.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def get_any_by_uuid(
        cls, db: AsyncSession, uuid: Union[str, UUID]
    ) -> Optional[Workspace]:
        """Resolve a live workspace by uuid regardless of owner. Callers enforce
        permissions separately (see src/permissions)."""
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(
            select(Workspace).where(
                Workspace.uuid == parsed, Workspace.deleted_at.is_(None)
            )
        )
        return result.scalars().first()

    @classmethod
    async def list(
        cls, db: AsyncSession, owner_id: int, skip: int = 0, limit: int = 100
    ) -> Sequence[Workspace]:
        result = await db.execute(
            cls._select(owner_id).order_by(Workspace.id).offset(skip).limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    def _apply_search(stmt, q: Optional[str]):
        """Shared by list_visible() and count_visible() so the page and its total
        cannot drift."""
        if not (q and q.strip()):
            return stmt
        pattern = like_pattern(q.strip())
        return stmt.where(
            or_(
                Workspace.display_name.ilike(pattern, escape=LIKE_ESCAPE),
                Workspace.name.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )

    @classmethod
    async def list_visible(
        cls,
        db: AsyncSession,
        user,
        skip: int = 0,
        limit: int = 100,
        q: Optional[str] = None,
    ) -> Sequence[Workspace]:
        """Workspaces the user owns or has a workspace-level grant on, matched
        server-side by ``q`` so a search finds one past the first page."""
        from src.permissions.services import PermissionService

        condition = await PermissionService.workspace_view_filter(db, user)
        stmt = cls._apply_search(
            select(Workspace).where(Workspace.deleted_at.is_(None), condition), q
        )
        result = await db.execute(
            stmt.order_by(Workspace.id).offset(skip).limit(limit)
        )
        return result.scalars().all()

    @classmethod
    async def count_visible(
        cls, db: AsyncSession, user, q: Optional[str] = None
    ) -> int:
        """Total visible workspaces, ignoring skip/limit — what the paginator needs."""
        from src.permissions.services import PermissionService

        condition = await PermissionService.workspace_view_filter(db, user)
        stmt = cls._apply_search(
            select(func.count(Workspace.id))
            .select_from(Workspace)
            .where(Workspace.deleted_at.is_(None), condition),
            q,
        )
        return await db.scalar(stmt) or 0

    @staticmethod
    def _guard_system_workspace(workspace: Workspace) -> None:
        """A system workspace (the transfers inbox) is immutable: it cannot be
        renamed, re-described, moved into, or deleted. Mirrors the system-group
        guard. Its *lists* stay ordinary, so the recipient can still move a
        transferred list out or delete it — only the workspace itself is frozen."""
        if workspace.is_system:
            raise PermissionDeniedError(
                f"The {INBOX_DISPLAY!r} workspace is immutable"
            )

    @classmethod
    async def create(
        cls, db: AsyncSession, owner_id: int, payload: WorkspaceCreate
    ) -> Workspace:
        # Keep the inbox slot free: an ordinary workspace may not take the reserved
        # name, so get_or_create_inbox can always mint it without colliding on the
        # per-owner unique index.
        if payload.name == INBOX_NAME:
            raise ConflictError(f"{INBOX_NAME!r} is a reserved workspace name")
        workspace = Workspace(
            owner_id=owner_id,
            name=payload.name,
            display_name=payload.display_name,
            description=payload.description,
            settings=payload.settings or {},
        )
        db.add(workspace)
        try:
            await db.flush()
            new_uuid = workspace.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("A workspace with this name already exists") from exc

        return await cls.get_by_uuid(db, owner_id, new_uuid)

    @classmethod
    async def create_default(cls, db: AsyncSession, owner_id: int) -> Workspace:
        """Ensure a user has a workspace so they can create lists immediately.

        Called when a user is created. Idempotent: if the user already has a
        workspace, the first one is returned instead of creating another.
        """
        existing = await cls.list(db, owner_id, limit=1)
        if existing:
            return existing[0]
        return await cls.create(
            db,
            owner_id,
            WorkspaceCreate(
                name="personal",
                display_name="Personal",
                description="Your personal workspace",
            ),
        )

    @classmethod
    async def get_or_create_inbox(cls, db: AsyncSession, owner_id: int) -> Workspace:
        """The user's immutable "Incoming" workspace, minted on first use.

        Transferred lists land here (see ``ListService.transfer_to_user``). Looked up
        by ``is_system`` rather than by name, so a user who happens to have named an
        ordinary workspace before the reservation existed can't be mistaken for it —
        though ``create`` now reserves the name, so the slot is free to mint.

        Flushes but does not commit: the caller (a transfer) owns the transaction, so
        the inbox and the list move land atomically. Idempotent — returns the existing
        inbox if there is one.
        """
        existing = (
            await db.execute(
                select(Workspace).where(
                    Workspace.owner_id == owner_id,
                    Workspace.is_system.is_(True),
                    Workspace.deleted_at.is_(None),
                )
            )
        ).scalars().first()
        if existing is not None:
            return existing

        inbox = Workspace(
            owner_id=owner_id,
            name=INBOX_NAME,
            display_name=INBOX_DISPLAY,
            description="Lists other people have transferred to you.",
            is_system=True,
        )
        db.add(inbox)
        await db.flush()
        return inbox

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        uuid: Union[str, UUID],
        payload: WorkspaceUpdate,
    ) -> Optional[Workspace]:
        # Owner-agnostic: the router enforces the required permission first.
        workspace = await cls.get_any_by_uuid(db, uuid)
        if workspace is None:
            return None
        # The inbox is immutable — refuse before touching any field.
        cls._guard_system_workspace(workspace)

        data = payload.model_dump(exclude_unset=True)
        if data.get("name") is None:
            data.pop("name", None)
        if data.get("display_name") is None:
            data.pop("display_name", None)
        # An ordinary workspace may not be renamed onto the reserved inbox slot.
        if data.get("name") == INBOX_NAME:
            raise ConflictError(f"{INBOX_NAME!r} is a reserved workspace name")

        for field, value in data.items():
            setattr(workspace, field, value)

        try:
            await db.flush()
            target_uuid = workspace.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("A workspace with this name already exists") from exc

        return await cls.get_any_by_uuid(db, target_uuid)

    @classmethod
    async def delete(
        cls, db: AsyncSession, uuid: Union[str, UUID]
    ) -> bool:
        # Owner-agnostic: the router enforces the required permission first.
        workspace = await cls.get_any_by_uuid(db, uuid)
        if workspace is None:
            return False
        # The inbox cannot be deleted, only emptied — its lists are the recipient's
        # to move out or delete individually.
        cls._guard_system_workspace(workspace)

        # Deferred import avoids a module cycle (lists.services imports this one).
        from src.lists.services import ListService

        now = datetime.now(timezone.utc)
        # Cascade the soft delete to the workspace's lists and their descendants,
        # all in this one transaction.
        await ListService.soft_delete_for_workspace(db, workspace.id, now)
        workspace.deleted_at = now
        await db.commit()
        return True

    # -- the bin: view, restore, and purge soft-deleted workspaces ----------

    @staticmethod
    def _select_deleted(owner_id: int):
        """Workspaces in an owner's bin: soft-deleted and still inside the retention
        window. Older ones are past ``PURGE_DELETED_AFTER_DAYS`` and on their way out
        — the purge job may destroy them at any time — so they are no longer offered
        as restorable, exactly as the records bin does."""
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=settings.PURGE_DELETED_AFTER_DAYS
        )
        return select(Workspace).where(
            Workspace.owner_id == owner_id,
            Workspace.deleted_at.is_not(None),
            Workspace.deleted_at >= cutoff,
        )

    @classmethod
    async def list_deleted_for_owner(
        cls, db: AsyncSession, owner_id: int, skip: int = 0, limit: int = 100
    ) -> Sequence[Workspace]:
        """An owner's bin, most recently deleted first. The bin is owner-scoped: a
        deleted workspace resolves to no permission context (see
        ``PermissionService._security_context``), so there is nothing to grant a
        non-owner against — restoring a whole workspace is the owner's call."""
        stmt = (
            cls._select_deleted(owner_id)
            .order_by(Workspace.deleted_at.desc(), Workspace.id.desc())
            .offset(skip)
            .limit(limit)
        )
        return (await db.execute(stmt)).scalars().all()

    @classmethod
    async def count_deleted_for_owner(cls, db: AsyncSession, owner_id: int) -> int:
        """How many workspaces are in the owner's bin — the paginator's total, built
        off the same predicate as the listing so the two cannot drift."""
        stmt = select(func.count()).select_from(
            cls._select_deleted(owner_id).subquery()
        )
        return await db.scalar(stmt) or 0

    @classmethod
    async def get_deleted_by_uuid(
        cls, db: AsyncSession, uuid: Union[str, UUID]
    ) -> Optional[Workspace]:
        """A single workspace from the bin, regardless of owner — the router enforces
        ownership. ``get_any_by_uuid`` filters deleted rows out, so restore/purge need
        their own fetch."""
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=settings.PURGE_DELETED_AFTER_DAYS
        )
        result = await db.execute(
            select(Workspace).where(
                Workspace.uuid == parsed,
                Workspace.deleted_at.is_not(None),
                Workspace.deleted_at >= cutoff,
            )
        )
        return result.scalars().first()

    @classmethod
    async def restore(
        cls, db: AsyncSession, uuid: Union[str, UUID]
    ) -> Optional[Workspace]:
        """Bring a soft-deleted workspace back, along with the lists (and their
        descendants) that went down *with it*. Returns None if it is not in the bin.

        The cascade delete stamped the workspace and everything it took down with one
        identical ``deleted_at``; restore reverses exactly that set by matching it, so
        a list the owner had binned on its own — a different timestamp — stays binned.

        A workspace name is unique per owner among live rows, so a name freed while
        this one sat deleted can be taken; restoring then collides and raises
        ``ConflictError`` (HTTP 409), just as a record restore can.

        Note: references *into* this workspace's lists were scrubbed at delete time
        (see ``ListService.delete``), so cross-list lookups pointing at them are not
        brought back — the lists themselves and their records are.
        """
        workspace = await cls.get_deleted_by_uuid(db, uuid)
        if workspace is None:
            return None

        # Deferred import avoids a module cycle (lists.services imports this one).
        from src.lists.services import ListService

        await ListService.restore_for_workspace(
            db, workspace.id, workspace.deleted_at
        )
        workspace.deleted_at = None
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("A workspace with this name already exists") from exc
        return await cls.get_any_by_uuid(db, uuid)

    @classmethod
    async def purge(cls, db: AsyncSession, uuid: Union[str, UUID]) -> bool:
        """Permanently destroy a single soft-deleted workspace and everything under
        it. Returns False if it is not in the bin.

        Mirrors the workspace purge job's cascade, but for one workspace in a single
        transaction: run the list cascade for its lists (break the circular foreign
        key, delete descendant grants, then records/views/forms/metrics/columns/lists), then
        the workspace's own grants, then the workspace. Inbound references were already
        scrubbed at soft-delete time. Irreversible.
        """
        workspace = await cls.get_deleted_by_uuid(db, uuid)
        if workspace is None:
            return False

        workspace_ids = [workspace.id]
        list_ids = list(
            (
                await db.execute(
                    text(
                        "SELECT id FROM lists WHERE workspace_id = ANY(:workspace_ids)"
                    ),
                    {"workspace_ids": workspace_ids},
                )
            ).scalars().all()
        )
        if list_ids:
            # Break the circular dependency (lists.primary_column_id -> columns.id).
            await db.execute(
                text(
                    "UPDATE lists SET primary_column_id = NULL WHERE id = ANY(:list_ids)"
                ),
                {"list_ids": list_ids},
            )
            await db.execute(
                text(
                    """
                    DELETE FROM permission_grants
                     WHERE (resource_type = 'list' AND resource_id = ANY(:list_ids))
                        OR (resource_type = 'item' AND resource_id IN (SELECT id FROM list_items WHERE list_id = ANY(:list_ids)))
                        OR (resource_type = 'view' AND resource_id IN (SELECT id FROM views WHERE list_id = ANY(:list_ids)))
                        OR (resource_type = 'form' AND resource_id IN (SELECT id FROM forms WHERE list_id = ANY(:list_ids)))
                    """
                ),
                {"list_ids": list_ids},
            )
            await db.execute(text("DELETE FROM list_items WHERE list_id = ANY(:list_ids)"), {"list_ids": list_ids})
            await db.execute(text("DELETE FROM views WHERE list_id = ANY(:list_ids)"), {"list_ids": list_ids})
            await db.execute(text("DELETE FROM forms WHERE list_id = ANY(:list_ids)"), {"list_ids": list_ids})
            # Metrics are never a grant target, so they appear here but not above.
            await db.execute(text("DELETE FROM metrics WHERE list_id = ANY(:list_ids)"), {"list_ids": list_ids})
            await db.execute(text("DELETE FROM columns WHERE list_id = ANY(:list_ids)"), {"list_ids": list_ids})
            await db.execute(text("DELETE FROM lists WHERE id = ANY(:list_ids)"), {"list_ids": list_ids})

        await db.execute(
            text(
                "DELETE FROM permission_grants "
                "WHERE resource_type = 'workspace' AND resource_id = ANY(:workspace_ids)"
            ),
            {"workspace_ids": workspace_ids},
        )
        await db.execute(
            text("DELETE FROM workspaces WHERE id = ANY(:workspace_ids)"),
            {"workspace_ids": workspace_ids},
        )
        await db.commit()
        return True
