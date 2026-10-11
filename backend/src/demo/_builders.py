"""Idempotent builders behind the demo scenarios.

Every helper here asks the same question first — "is this already there?" — keyed
on the resource's natural handle (a username, a group name, a `name` slug within
its scope). It creates only what is missing. That is what makes a scenario script
safe to re-run: a second run fills the gaps left by a first one that died halfway,
and leaves everything else alone, including edits made by hand while exploring.

Records are the one thing without a natural key — a record is pure data (D2), so
there is nothing to match on. `seed_records` therefore takes the table as its unit:
a table that already holds live records is left untouched rather than doubled.

Writes go through the **real services**, not raw INSERTs, so seeded data is
validated exactly as the API validates it: a lookup pointing nowhere, a formula
reading a column that does not exist, a choice value outside the option set — all
fail here, loudly, instead of arriving as a broken cell someone finds later. What
the services do *not* do is enforce permissions (the routers do that), so every
builder that needs an actor takes one explicitly. Two things depend on it:
`created_by`/`updated_by` are stamped from it, and a lookup column's target is
checked against *its* access.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.users.models import User, UserGroup
from src.auth.users.schemas import (
    UserCreate,
    UserGroupCreate,
    UserGroupUpdate,
    UserSettingsUpdate,
)
from src.auth.users.services import UserGroupService, UserService
from src.tables.columns.models import Column
from src.tables.columns.schemas import ColumnConfig, ColumnCreate
from src.tables.columns.services import ColumnService
from src.tables.forms.enums import FormType
from src.tables.forms.models import Form
from src.tables.forms.schemas import FormConfig, FormCreate
from src.tables.forms.services import FormService
from src.tables.records.models import TableRecord
from src.tables.records.schemas import TableRecordCreate
from src.tables.records.services import TableRecordService
from src.tables.metrics.models import Metric
from src.tables.metrics.schemas import MetricConfig, MetricCreate
from src.tables.metrics.services import MetricService
from src.tables.models import Table
from src.tables.schemas import TableCreate, TableUpdate
from src.tables.services import TableService
from src.tables.views.models import View
from src.tables.views.schemas import ViewConfig, ViewCreate
from src.tables.views.services import ViewService
from src.people.models import Person
from src.people.schemas import PersonCreate
from src.people.services import PersonService
from src.permissions.enums import Action, ResourceType
from src.permissions.schemas import GrantCreate
from src.permissions.services import PermissionService
from src.workspaces.models import Workspace
from src.workspaces.schemas import WorkspaceCreate
from src.workspaces.services import WorkspaceService


class Report(Counter):
    """What the run created, versus what it found already in place.

    A counter rather than a log: on a re-run the interesting number is how much was
    *new*, and "created 0 tables" is exactly the reassurance an idempotent script
    owes its operator.
    """

    def made(self, kind: str) -> None:
        self[f"{kind} created"] += 1

    def found(self, kind: str) -> None:
        self[f"{kind} reused"] += 1

    def render(self) -> str:
        width = max((len(key) for key in self), default=0)
        return "\n".join(
            f"  {key.ljust(width)}  {count:>5,}" for key, count in sorted(self.items())
        )


# -- principals ------------------------------------------------------------


async def ensure_user(
    db: AsyncSession,
    report: Report,
    *,
    username: str,
    email: str,
    password: str,
    is_superuser: bool = False,
) -> User:
    """An account, plus the personal workspace `UserService.create` seeds with it."""
    existing = await UserService.get_by_username(db, username)
    if existing is not None:
        report.found("users")
        return existing

    user = await UserService.create(
        db, UserCreate(username=username, email=email, password=password)
    )
    if is_superuser:
        user.is_superuser = True
        await db.commit()
    report.made("users")
    return user


async def ensure_person(
    db: AsyncSession, report: Report, user: User, **fields: Any
) -> Person:
    """The user's personal details. One per account (`uq_people_user`)."""
    existing = await PersonService.get_by_user_id(db, user.id)
    if existing is not None:
        report.found("people")
        return existing

    person = await PersonService.create(db, user.id, PersonCreate(**fields))
    report.made("people")
    return person


async def ensure_group(
    db: AsyncSession,
    report: Report,
    *,
    name: str,
    display_name: str,
    description: str,
    members: Sequence[User],
) -> UserGroup:
    """A group and its membership.

    An existing group keeps whatever membership it has — except an empty one, which
    is what a half-finished run leaves behind and is never what anyone wants.

    Identified by the slug, like every other `ensure_` here: re-running after someone
    has renamed a demo group in the UI should find that group, not build a second one
    beside it.
    """
    existing = await UserGroupService.get_by_name(db, name)
    if existing is not None:
        if not existing.users and members:
            existing = await UserGroupService.update(
                db,
                existing.uuid,
                UserGroupUpdate(user_uuids=[member.uuid for member in members]),
            )
        report.found("groups")
        return existing

    group = await UserGroupService.create(
        db,
        UserGroupCreate(
            name=name,
            display_name=display_name,
            description=description,
            user_uuids=[member.uuid for member in members],
        ),
    )
    report.made("groups")
    return group


async def set_settings(db: AsyncSession, user: User, **prefs: Any) -> None:
    """Theme/mode/timezone, merged onto whatever the account already holds."""
    await UserService.update_settings(db, user.uuid, UserSettingsUpdate(**prefs))


# -- containers ------------------------------------------------------------


async def ensure_workspace(
    db: AsyncSession,
    report: Report,
    owner: User,
    *,
    name: str,
    display_name: str,
    description: Optional[str] = None,
    settings: Optional[dict] = None,
) -> Workspace:
    """A workspace. `name` is unique per owner, so that is what identifies it."""
    existing = (
        await db.execute(
            select(Workspace).where(
                Workspace.owner_id == owner.id,
                Workspace.name == name,
                Workspace.deleted_at.is_(None),
            )
        )
    ).scalars().first()
    if existing is not None:
        report.found("workspaces")
        return existing

    workspace = await WorkspaceService.create(
        db,
        owner.id,
        WorkspaceCreate(
            name=name,
            display_name=display_name,
            description=description,
            settings=settings or {},
        ),
    )
    report.made("workspaces")
    return workspace


async def ensure_table(
    db: AsyncSession,
    report: Report,
    workspace: Workspace,
    *,
    name: str,
    display_name: str,
    description: Optional[str] = None,
    settings: Optional[dict] = None,
) -> Table:
    """A table. Creating one auto-provisions the audit columns, a grid view and the
    three default forms — this returns after that has happened.

    Always resolved through `TableService.get_any_by_uuid`, whatever branch it takes,
    because that is the one loader that eager-loads `Table.workspace`. Several
    services reach for it (`_lookup_target_id` reads its owner), and under asyncpg a
    lazy load outside the greenlet raises rather than quietly fetching.
    """
    existing = (
        await db.execute(
            select(Table.uuid).where(
                Table.workspace_id == workspace.id,
                Table.name == name,
                Table.deleted_at.is_(None),
            )
        )
    ).scalars().first()
    if existing is not None:
        report.found("tables")
        return await TableService.get_any_by_uuid(db, existing)

    table = await TableService.create(
        db,
        TableCreate(
            name=name,
            display_name=display_name,
            description=description,
            settings=settings or {},
            workspace_uuid=workspace.uuid,
        ),
    )
    report.made("tables")
    return table


async def set_primary_column(
    db: AsyncSession, table: Table, column: Column
) -> Table:
    """Point the table's display field at one of its columns.

    Worth doing on every table that anything links to: a row's label falls back from
    the lookup's `display_column_uuid` to this, and only then to the table's *first*
    column — which is rarely the one a human would have picked.
    """
    if table.primary_column_id == column.id:
        return table
    return await TableService.update(
        db, table.uuid, TableUpdate(primary_column_uuid=column.uuid)
    )


async def set_unique_permissions(db: AsyncSession, table: Table) -> Table:
    """Stop the table inheriting its workspace's grants, so its own stand alone."""
    if not table.permissions_inherit:
        return table
    return await TableService.update(db, table.uuid, TableUpdate(permissions_inherit=False))


# -- table configuration ----------------------------------------------------


async def ensure_column(
    db: AsyncSession,
    report: Report,
    table: Table,
    actor: User,
    *,
    name: str,
    display_name: str,
    type: str,
    position: int,
    config: Optional[dict] = None,
) -> Column:
    """One field. `actor` is whose access a lookup's target is checked against.

    **A binned column counts as already there.** A column `name` is unique only
    among live columns, so restricting this to live rows would let a scenario that
    deliberately bins one recreate it on the next run — and bin the new one too,
    growing a pile of identical binned columns. The same rule holds for views, and
    for tables via `table_exists`.
    """
    existing = (
        await db.execute(
            select(Column).where(Column.table_id == table.id, Column.name == name)
        )
    ).scalars().first()
    if existing is not None:
        report.found("columns")
        return existing

    column = await ColumnService.create(
        db,
        table,
        ColumnCreate(
            name=name,
            display_name=display_name,
            type=type,
            position=position,
            config=ColumnConfig(**(config or {})),
        ),
        actor,
    )
    report.made("columns")
    return column


async def ensure_view(
    db: AsyncSession,
    report: Report,
    table: Table,
    *,
    name: str,
    display_name: str,
    position: int = 0,
    description: Optional[str] = None,
    config: Optional[dict] = None,
) -> View:
    """A saved view. A binned one counts as already there — see `ensure_column`."""
    existing = (
        await db.execute(
            select(View).where(View.table_id == table.id, View.name == name)
        )
    ).scalars().first()
    if existing is not None:
        report.found("views")
        return existing

    view = await ViewService.create(
        db,
        table,
        ViewCreate(
            name=name,
            display_name=display_name,
            position=position,
            description=description,
            config=ViewConfig(**(config or {})),
        ),
    )
    report.made("views")
    return view


async def ensure_form(
    db: AsyncSession,
    report: Report,
    table: Table,
    *,
    name: str,
    display_name: str,
    type: FormType = FormType.custom,
    is_default: bool = False,
    config: Optional[dict] = None,
) -> Form:
    existing = (
        await db.execute(
            select(Form).where(
                Form.table_id == table.id, Form.name == name, Form.deleted_at.is_(None)
            )
        )
    ).scalars().first()
    if existing is not None:
        report.found("forms")
        return existing

    form = await FormService.create(
        db,
        table,
        FormCreate(
            name=name,
            display_name=display_name,
            type=type,
            is_default=is_default,
            config=FormConfig(**(config or {"fields": []})),
        ),
    )
    report.made("forms")
    return form


async def ensure_metric(
    db: AsyncSession,
    report: Report,
    table: Table,
    *,
    name: str,
    display_name: str,
    formula: str,
    position: int = 0,
    description: Optional[str] = None,
    precision: Optional[int] = None,
    prefix: Optional[str] = None,
    suffix: Optional[str] = None,
) -> Metric:
    existing = (
        await db.execute(
            select(Metric).where(
                Metric.table_id == table.id,
                Metric.name == name,
                Metric.deleted_at.is_(None),
            )
        )
    ).scalars().first()
    if existing is not None:
        report.found("metrics")
        return existing

    metric = await MetricService.create(
        db,
        table,
        MetricCreate(
            name=name,
            display_name=display_name,
            position=position,
            description=description,
            config=MetricConfig(
                formula=formula, precision=precision, prefix=prefix, suffix=suffix
            ),
        ),
    )
    report.made("metrics")
    return metric


# -- records ---------------------------------------------------------------


async def seed_records(
    db: AsyncSession,
    report: Report,
    table: Table,
    actor: User,
    columns: dict[str, Column],
    rows: Sequence[dict],
) -> list[str]:
    """Write `rows` (keyed by column *name*) as records, unless the table has some.

    The table is the unit of idempotency because a record has no natural key: `data`
    is keyed by column uuid and holds nothing that identifies the row across runs.
    Anything already here therefore wins, and nothing is duplicated.

    **Returns the table's row uuids either way** — the ones just written, or the ones
    already there, in insertion order. Callers link one table to another by position
    (`students[4]` is the fifth student), so a skip has to hand back something a
    later phase can still point at, or a re-run would build tables full of empty
    lookups.

    Values are passed through as written and validated per column — a choice label
    must be one of the options, a lookup must name a live target row (by uuid or by
    its label), a `user`/`group` cell may name the account or resolve its uuid.
    """
    existing = (
        await db.execute(
            select(TableRecord.uuid)
            .where(TableRecord.table_id == table.id, TableRecord.deleted_at.is_(None))
            .order_by(TableRecord.id)
        )
    ).scalars().all()
    if existing:
        report["records reused"] += len(existing)
        return [str(uuid) for uuid in existing]

    created: list[str] = []
    for row in rows:
        data = {}
        for name, value in row.items():
            column = columns.get(name)
            if column is None:
                raise KeyError(f"{table.name!r} has no column named {name!r}")
            data[str(column.uuid)] = value
        record = await TableRecordService.create(db, table, TableRecordCreate(data=data), actor)
        created.append(str(record.uuid))
    report["records created"] += len(created)
    return created


async def bin_records(
    db: AsyncSession, report: Report, table: Table, uuids: Sequence[str]
) -> None:
    """Soft-delete a few records so the table's bin has something in it.

    A table that already has something binned is left alone. Callers pick the rows
    to bin off the *live* ones, so without this each run would eat another couple
    of records — a seeder that quietly shrinks its own data every time it runs.
    """
    already = await db.scalar(
        select(func.count())
        .select_from(TableRecord)
        .where(TableRecord.table_id == table.id, TableRecord.deleted_at.is_not(None))
    )
    if already:
        return
    for uuid in uuids:
        if await TableRecordService.delete(db, table, uuid):
            report["records binned"] += 1


async def table_exists(db: AsyncSession, workspace_id: int, name: str) -> bool:
    """Whether a table of this name is in the workspace, **bin included**.

    `ensure_table` deliberately ignores binned rows — a name is only unique among
    live tables, so a deleted one must not block re-creating it. That is wrong for
    a table the scenario deletes on purpose: without this check every run would
    create another and bin it too.
    """
    found = await db.scalar(
        select(Table.id).where(
            Table.workspace_id == workspace_id, Table.name == name
        )
    )
    return found is not None


async def table_owned_by(db: AsyncSession, owner: User, name: str) -> bool:
    """Whether this user owns a live table of this name in any of their workspaces.

    How a transfer is recognised on a re-run: the table is no longer where it was
    created, so the only way to tell it has already been handed over is to ask
    whether the recipient has it.
    """
    found = await db.scalar(
        select(Table.id)
        .join(Workspace, Table.workspace_id == Workspace.id)
        .where(
            Workspace.owner_id == owner.id,
            Table.name == name,
            Table.deleted_at.is_(None),
            Workspace.deleted_at.is_(None),
        )
    )
    return found is not None


# -- sharing ---------------------------------------------------------------


async def grant(
    db: AsyncSession,
    report: Report,
    actor: User,
    *,
    resource_type: ResourceType,
    resource_uuid: Union[str, UUID],
    actions: Sequence[Action],
    user: Optional[User] = None,
    group: Optional[UserGroup] = None,
    expires_at: Optional[datetime] = None,
) -> None:
    """Share a resource with one principal.

    Already idempotent at the service: there is one grant row per
    (resource, principal), and a second call overwrites its actions. `actor` needs
    `share` on the resource — a workspace's owner has every action on everything
    beneath it, which is why scenarios grant as the owner.
    """
    await PermissionService.upsert_grant(
        db,
        actor,
        GrantCreate(
            resource_type=resource_type,
            resource_uuid=resource_uuid,
            user_uuid=user.uuid if user is not None else None,
            group_uuid=group.uuid if group is not None else None,
            actions=list(actions),
            expires_at=expires_at,
        ),
    )
    report["grants written"] += 1
