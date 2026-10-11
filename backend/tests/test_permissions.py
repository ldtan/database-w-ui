"""Permission/authorization tests.

Because DISABLE_AUTH resolves the caller to the seeded owner, the core resolver
logic is exercised directly against the service layer with several distinct users,
and HTTP enforcement is checked via a `make_client` helper that overrides
get_current_user to act as a chosen user.
"""
import gc
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.schemas import UserCreate, UserGroupCreate, UserGroupUpdate
from src.auth.users.services import UserGroupService, UserService
from src.infrastructure import database as db_module
from src.tables.forms.models import Form
from src.tables.records.schemas import TableRecordCreate
from src.tables.records.services import TableRecordService
from src.tables.schemas import TableCreate, TableUpdate
from src.tables.services import TableService
from src.permissions.enums import ALL_ACTIONS, Action, ResourceType
from src.permissions.models import PermissionGrant
from src.permissions.schemas import GrantCreate
from src.permissions.services import PermissionService
from src.workspaces.schemas import WorkspaceCreate
from src.workspaces.services import WorkspaceService


async def _make_user(db, name: str):
    return await UserService.create(
        db, UserCreate(username=name, email=f"{name}@example.com", password="pw")
    )


async def _fixture_world(db):
    """Owner + a second user + an empty workspace/table owned by the owner."""
    owner = await _make_user(db, "owner")
    other = await _make_user(db, "other")
    ws = await WorkspaceService.create(
        db, owner.id, WorkspaceCreate(name="ws", display_name="WS")
    )
    table = await TableService.create(
        db, TableCreate(name="table", display_name="Lst", workspace_uuid=ws.uuid)
    )
    return owner, other, ws, table


# --------------------------------------------------------------------------
# Service-level resolver tests
# --------------------------------------------------------------------------


async def test_owner_has_all_actions(session_factory):
    async with session_factory() as db:
        owner, _, _, table = await _fixture_world(db)
        actions = await PermissionService.effective_actions(
            db, owner, ResourceType.table, table.id
        )
        assert actions == set(ALL_ACTIONS)


async def test_non_owner_starts_with_nothing(session_factory):
    async with session_factory() as db:
        _, other, _, table = await _fixture_world(db)
        actions = await PermissionService.effective_actions(
            db, other, ResourceType.table, table.id
        )
        assert actions == set()


async def test_workspace_grant_inherits_to_table_and_items(session_factory):
    async with session_factory() as db:
        owner, other, ws, table = await _fixture_world(db)
        record = await TableRecordService.create(db, table, TableRecordCreate(data={}))

        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.workspace,
                resource_uuid=ws.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )

        assert await PermissionService.has(
            db, other, ResourceType.table, table.id, Action.view
        )
        assert await PermissionService.has(
            db, other, ResourceType.record, record.id, Action.view
        )


async def test_edit_implies_view_and_manage_implies_all(session_factory):
    async with session_factory() as db:
        owner, other, _, table = await _fixture_world(db)

        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.edit],
            ),
        )
        actions = await PermissionService.effective_actions(
            db, other, ResourceType.table, table.id
        )
        assert {Action.edit, Action.view} <= actions
        assert Action.delete not in actions
        assert Action.configure not in actions

        # Overwrite with manage -> everything.
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.manage],
            ),
        )
        assert await PermissionService.effective_actions(
            db, other, ResourceType.table, table.id
        ) == set(ALL_ACTIONS)


async def test_group_grant_applies_to_members(session_factory):
    async with session_factory() as db:
        owner, member, _, table = await _fixture_world(db)
        group = await UserGroupService.create(
            db, UserGroupCreate(name="team", display_name="Team", user_uuids=[member.uuid])
        )

        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                group_uuid=group.uuid,
                actions=[Action.view],
            ),
        )
        assert await PermissionService.has(
            db, member, ResourceType.table, table.id, Action.view
        )


async def test_unique_table_ignores_workspace_grant(session_factory):
    async with session_factory() as db:
        owner, other, ws, table = await _fixture_world(db)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.workspace,
                resource_uuid=ws.uuid,
                user_uuid=other.uuid,
                actions=[Action.edit],
            ),
        )
        assert await PermissionService.has(
            db, other, ResourceType.table, table.id, Action.view
        )

        # Break inheritance: the table now stands alone with no direct grant.
        await TableService.update(db, table.uuid, TableUpdate(permissions_inherit=False))
        assert not await PermissionService.has(
            db, other, ResourceType.table, table.id, Action.view
        )

        # Rejoining restores the inherited access.
        await TableService.update(db, table.uuid, TableUpdate(permissions_inherit=True))
        assert await PermissionService.has(
            db, other, ResourceType.table, table.id, Action.view
        )


async def test_item_level_grant_is_isolated(session_factory):
    async with session_factory() as db:
        owner, other, _, table = await _fixture_world(db)
        shared = await TableRecordService.create(db, table, TableRecordCreate(data={}))
        sibling = await TableRecordService.create(db, table, TableRecordCreate(data={}))

        # The table itself is not shared; only one row is granted directly.
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.record,
                resource_uuid=shared.uuid,
                user_uuid=other.uuid,
                actions=[Action.edit],
            ),
        )
        assert await PermissionService.has(
            db, other, ResourceType.record, shared.id, Action.edit
        )
        assert not await PermissionService.has(
            db, other, ResourceType.record, sibling.id, Action.view
        )


async def test_form_targeting_and_configure_scope(session_factory):
    async with session_factory() as db:
        owner, other, _, table = await _fixture_world(db)
        member = await _make_user(db, "member")
        group = await UserGroupService.create(
            db, UserGroupCreate(name="audience", display_name="Audience", user_uuids=[member.uuid])
        )

        # Both other and member can view the table.
        for principal in (other, member):
            await PermissionService.upsert_grant(
                db,
                owner,
                GrantCreate(
                    resource_type=ResourceType.table,
                    resource_uuid=table.uuid,
                    user_uuid=principal.uuid,
                    actions=[Action.view],
                ),
            )

        forms = (
            await db.execute(select(Form).where(Form.table_id == table.id).order_by(Form.id))
        ).scalars().all()
        form_a, form_b = forms[0], forms[1]

        # Target form_a at the group only.
        form_a.permissions_inherit = False
        await db.commit()
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.form,
                resource_uuid=form_a.uuid,
                group_uuid=group.uuid,
                actions=[Action.view],
            ),
        )

        # form_a: only the group member sees it, not the plain table-viewer.
        assert await PermissionService.has(
            db, member, ResourceType.form, form_a.id, Action.view
        )
        assert not await PermissionService.has(
            db, other, ResourceType.form, form_a.id, Action.view
        )
        # form_b still inherits, so both table-viewers see it.
        assert await PermissionService.has(
            db, other, ResourceType.form, form_b.id, Action.view
        )

        # configure grant confers config rights without record edit or share.
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.configure],
            ),
        )
        actions = await PermissionService.effective_actions(
            db, other, ResourceType.table, table.id
        )
        assert {Action.configure, Action.view} <= actions
        assert Action.edit not in actions
        assert Action.share not in actions


async def test_effective_actions_bulk_does_not_query_per_resource(
    session_factory, engine
):
    """The whole point of the bulk resolver is a query count that does not follow the
    batch size, so that is what gets asserted — at two sizes, since only the shape of
    the growth tells you anything.

    It is worth pinning because the way to lose it is invisible. The rows are resolved
    in one query and the chain walk below reads them back out of the identity map,
    which holds them *weakly*: drop the last strong reference and they are collected,
    the walk misses, and every one is quietly selected again by id. Nothing fails, no
    result changes, and the per-resource query this call exists to remove is back.
    """
    async with session_factory() as db:
        owner, _, _, table = await _fixture_world(db)
        refs = []
        for _ in range(12):
            record = await TableRecordService.create(db, table, TableRecordCreate(data={}))
            refs.append((ResourceType.record, record.uuid))
        # Keep the uuids, not the records: a caller holding the instances would keep
        # them alive on its own and hide exactly the effect under test.
        del record
        gc.collect()

        counted: list[int] = []

        def _count(conn, cursor, statement, params, context, many):
            counted.append(1)

        event.listen(engine.sync_engine, "before_cursor_execute", _count)
        try:
            await PermissionService.effective_actions_bulk(db, owner, refs[:2])
            for_two = len(counted)
            counted.clear()
            gc.collect()
            await PermissionService.effective_actions_bulk(db, owner, refs)
            for_twelve = len(counted)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _count)

        assert for_two == for_twelve, (
            f"{for_two} queries for 2 records but {for_twelve} for {len(refs)}"
        )


@asynccontextmanager
async def _counting(engine):
    """Count statements issued while the block runs."""
    counted: list[str] = []

    def _count(conn, cursor, statement, params, context, many):
        counted.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        yield counted
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)


def _membership_queries(statements) -> int:
    return sum(1 for s in statements if "user_group_association" in s)


async def test_group_membership_is_resolved_once_per_session(
    session_factory, engine
):
    """`_group_ids` is called by `effective_actions`, `effective_actions_bulk` and all
    three collection filters, so one request used to spend four to six identical round
    trips asking which groups the same person is in. It is cached per session now, for
    the reason `_system_group_ids` beside it already was: the answer cannot change
    under a request that is not itself changing it.

    Counted by statement text rather than by total queries, so the assertion says
    what it means and does not move when something unrelated on the path gains or
    loses a query."""
    async with session_factory() as db:
        user = await _make_user(db, "member")

        async with _counting(engine) as statements:
            first = await PermissionService._group_ids(db, user.id)
            after_one = _membership_queries(statements)
            for _ in range(5):
                await PermissionService._group_ids(db, user.id)
            after_six = _membership_queries(statements)

        assert after_one == 1, f"expected one membership query, got {after_one}"
        assert after_six == 1, (
            f"expected the five repeats to be served from cache, got {after_six}"
        )
        assert await PermissionService._group_ids(db, user.id) == first


async def test_the_cache_is_keyed_by_user(session_factory, engine):
    """A session is not always one caller's — the batch importer and the maintenance
    jobs resolve many — so a single cached table would hand one person another's
    groups. That is the failure this key exists to prevent, and it is a wrong
    *authorization* answer rather than a slow one."""
    async with session_factory() as db:
        alice = await _make_user(db, "alice")
        bob = await _make_user(db, "bob")
        group = await UserGroupService.create(
            db,
            UserGroupCreate(
                name="team", display_name="Team", user_uuids=[alice.uuid]
            ),
        )

        alice_groups = await PermissionService._group_ids(db, alice.id)
        bob_groups = await PermissionService._group_ids(db, bob.id)

        assert group.id in alice_groups
        assert group.id not in bob_groups


async def test_editing_a_groups_membership_drops_the_cache(session_factory):
    """The cache sits in front of an authorization answer, so it has to be dropped by
    anything that changes what the answer is. Without this the session would go on
    reporting the membership it read before the edit — and the cost of that being
    wrong is somebody reaching what a grant no longer reaches them."""
    async with session_factory() as db:
        alice = await _make_user(db, "alice")
        group = await UserGroupService.create(
            db, UserGroupCreate(name="team", display_name="Team", user_uuids=[])
        )
        assert group.id not in await PermissionService._group_ids(db, alice.id)

        await UserGroupService.update(
            db, group.uuid, UserGroupUpdate(user_uuids=[alice.uuid])
        )
        assert group.id in await PermissionService._group_ids(db, alice.id)

        await UserGroupService.update(db, group.uuid, UserGroupUpdate(user_uuids=[]))
        assert group.id not in await PermissionService._group_ids(db, alice.id)


async def test_deleting_a_group_drops_the_cache(session_factory):
    """Deleting a group keeps its membership rows — that is what makes the restore a
    real undo — so `_group_ids` is the only thing that stops it conferring, via its
    join to a live `user_groups` row. A cache in front of that check therefore has to
    be dropped here too, or the group goes on conferring for the rest of the session.
    """
    async with session_factory() as db:
        alice = await _make_user(db, "alice")
        group = await UserGroupService.create(
            db,
            UserGroupCreate(
                name="team", display_name="Team", user_uuids=[alice.uuid]
            ),
        )
        assert group.id in await PermissionService._group_ids(db, alice.id)

        await UserGroupService.delete(db, group.uuid)
        assert group.id not in await PermissionService._group_ids(db, alice.id)


# --------------------------------------------------------------------------
# HTTP enforcement
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
def make_client(engine, session_factory):
    """Build an AsyncClient that authenticates as a specific user."""

    @asynccontextmanager
    async def _make(user):
        orig_engine, orig_factory = db_module.engine, db_module.SessionLocal
        db_module.engine = engine
        db_module.SessionLocal = session_factory
        app = create_app()

        async def _current_user():
            return user

        app.dependency_overrides[get_current_user] = _current_user
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client:
                yield client
        finally:
            db_module.engine, db_module.SessionLocal = orig_engine, orig_factory

    return _make


async def test_http_sharing_flow(make_client, session_factory):
    async with session_factory() as db:
        owner, other, ws, table = await _fixture_world(db)
        table_uuid = str(table.uuid)
        other_uuid = str(other.uuid)

    # Before any grant, the second user cannot see the table.
    async with make_client(other) as client:
        assert (await client.get(f"/api/tables/{table_uuid}")).status_code == 403

    # The owner shares 'view'.
    async with make_client(owner) as client:
        resp = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "table",
                "resource_uuid": table_uuid,
                "user_uuid": other_uuid,
                "actions": ["view"],
            },
        )
        assert resp.status_code == 201, resp.text

    # Now the second user can view, but configuring is still forbidden.
    async with make_client(other) as client:
        assert (await client.get(f"/api/tables/{table_uuid}")).status_code == 200
        resp = await client.post(
            f"/api/tables/{table_uuid}/columns/",
            json={"name": "title", "display_name": "Title", "type": "text"},
        )
        assert resp.status_code == 403


async def test_http_non_sharer_cannot_grant(make_client, session_factory):
    async with session_factory() as db:
        owner, other, _, table = await _fixture_world(db)
        table_uuid = str(table.uuid)
        other_uuid = str(other.uuid)
        # other only gets 'view' — not 'share'.
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )

    async with make_client(other) as client:
        resp = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "table",
                "resource_uuid": table_uuid,
                "user_uuid": other_uuid,
                "actions": ["manage"],
            },
        )
        assert resp.status_code == 403


async def test_http_shared_with_me(make_client, session_factory):
    """A directly-granted table sits in a workspace the grantee cannot open, so it
    is only reachable through the shared listing."""
    async with session_factory() as db:
        owner, other, ws, table = await _fixture_world(db)
        workspace_uuid = str(ws.uuid)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )
        table_uuid = str(table.uuid)

    async with make_client(other) as client:
        # Only the table was shared, so the workspace holding it stays invisible;
        # the grantee still sees their own auto-provisioned personal workspace.
        resp = await client.get("/api/workspaces/")
        assert workspace_uuid not in [w["uuid"] for w in resp.json()]

        resp = await client.get("/api/tables/?shared=true")
        assert [record["uuid"] for record in resp.json()] == [table_uuid]

    # The owner owns everything they can see, so nothing is "shared with" them.
    async with make_client(owner) as client:
        resp = await client.get("/api/tables/?shared=true")
        assert resp.json() == []
        # ...but the table is still there unfiltered, under its workspace.
        resp = await client.get(f"/api/tables/?workspace_uuid={workspace_uuid}")
        assert [record["uuid"] for record in resp.json()] == [table_uuid]


async def test_http_workspace_grant_does_not_orphan_its_tables(
    make_client, session_factory
):
    """Sharing a *workspace* shares its tables too, but those tables are reachable by
    browsing the workspace — so they must not also appear in the flat "Shared with
    me" section, which exists only for tables that have nowhere else to hang.

    The two filters answer different questions: `shared` is about ownership (and the
    lookup target picker wants every non-owned table), `workspace_hidden` is about
    reachability. This is the case where they disagree.
    """
    async with session_factory() as db:
        owner, other, ws, table = await _fixture_world(db)
        workspace_uuid = str(ws.uuid)
        table_uuid = str(table.uuid)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.workspace,
                resource_uuid=ws.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )

    async with make_client(other) as client:
        # The whole workspace was shared, so it is browsable...
        resp = await client.get("/api/workspaces/?shared=true")
        assert workspace_uuid in [w["uuid"] for w in resp.json()]
        # ...and stays out of the caller's own workspaces, which is the split the
        # nav tree renders as two sections.
        resp = await client.get("/api/workspaces/?shared=false")
        assert workspace_uuid not in [w["uuid"] for w in resp.json()]

        # The table is shared — it is not owned by the caller.
        resp = await client.get("/api/tables/?shared=true")
        assert [record["uuid"] for record in resp.json()] == [table_uuid]

        # ...but it is *not* homeless: its workspace is right there to browse into,
        # so it is excluded from the section that would show it a second time.
        resp = await client.get("/api/tables/?shared=true&workspace_hidden=true")
        assert resp.json() == []
        assert resp.headers["X-Total-Count"] == "0"

        # The complement returns it, and the paginator's total agrees with the body
        # — the two go through one set of conditions precisely so they cannot drift.
        resp = await client.get("/api/tables/?shared=true&workspace_hidden=false")
        assert [record["uuid"] for record in resp.json()] == [table_uuid]
        assert resp.headers["X-Total-Count"] == "1"


async def test_http_table_grant_survives_the_workspace_hidden_filter(
    make_client, session_factory
):
    """The other half: a table granted *without* its workspace is reachable no other
    way, so it must pass the filter the nav tree's "Shared with me" applies."""
    async with session_factory() as db:
        owner, other, ws, table = await _fixture_world(db)
        workspace_uuid = str(ws.uuid)
        table_uuid = str(table.uuid)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )

    async with make_client(other) as client:
        resp = await client.get("/api/workspaces/?shared=true")
        assert workspace_uuid not in [w["uuid"] for w in resp.json()]

        resp = await client.get("/api/tables/?shared=true&workspace_hidden=true")
        assert [record["uuid"] for record in resp.json()] == [table_uuid]
        assert resp.headers["X-Total-Count"] == "1"
        # Withheld, as ever: naming a workspace the caller was denied would leak it.
        assert resp.json()[0]["workspace_display_name"] is None


@pytest.mark.parametrize("break_inheritance", [False, True])
async def test_http_a_shared_table_is_always_reachable_somewhere(
    break_inheritance, make_client, session_factory
):
    """The invariant the whole split rests on: filtering the flat section down to
    homeless tables must never hide a table that has no home either.

    The dangerous shape is a table held by a *direct* grant whose workspace is also
    shared — it fails `workspace_hidden`, so it is only reachable if browsing that
    workspace finds it. Doubly so when the table has opted out of inheritance and
    stands on the direct grant alone, which is the case where the workspace-level
    and table-level paths disagree about why it is visible.
    """
    async with session_factory() as db:
        owner, other, ws, table = await _fixture_world(db)
        workspace_uuid = str(ws.uuid)
        table_uuid = str(table.uuid)
        for resource_type, resource_uuid in (
            (ResourceType.workspace, ws.uuid),
            (ResourceType.table, table.uuid),
        ):
            await PermissionService.upsert_grant(
                db,
                owner,
                GrantCreate(
                    resource_type=resource_type,
                    resource_uuid=resource_uuid,
                    user_uuid=other.uuid,
                    actions=[Action.view],
                ),
            )
        if break_inheritance:
            await TableService.update(
                db, table.uuid, TableUpdate(permissions_inherit=False)
            )

    async with make_client(other) as client:
        # Not homeless: its workspace is right there, so it stays out of the flat
        # run rather than appearing twice.
        resp = await client.get("/api/tables/?shared=true&workspace_hidden=true")
        assert resp.json() == []
        # ...and the home it was excluded in favour of really does hold it.
        resp = await client.get("/api/workspaces/?shared=true")
        assert workspace_uuid in [w["uuid"] for w in resp.json()]
        resp = await client.get(f"/api/tables/?workspace_uuid={workspace_uuid}")
        assert table_uuid in [record["uuid"] for record in resp.json()]


async def test_http_lookup_target_picker_keeps_the_whole_shared_set(
    make_client, session_factory
):
    """A regression guard for the filter split: the lookup target picker asks for
    `shared=true` alone, and must keep seeing tables that arrived through a workspace
    grant. Narrowing `shared` itself would have silently emptied that picker."""
    async with session_factory() as db:
        owner, other, ws, table = await _fixture_world(db)
        table_uuid = str(table.uuid)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.workspace,
                resource_uuid=ws.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )

    async with make_client(other) as client:
        resp = await client.get("/api/tables/?shared=true&lookup_targets=true")
        assert table_uuid in [record["uuid"] for record in resp.json()]


async def test_http_export_respects_item_permissions(make_client, session_factory):
    """Export is a read of the records, so it must hide the same rows the grid does.
    It previously streamed every row in the table, leaking records whose inheritance
    was broken and were never granted to the caller."""
    async with session_factory() as db:
        owner, other, _, table = await _fixture_world(db)
        await TableRecordService.create(db, table, TableRecordCreate(data={}))
        hidden = await TableRecordService.create(db, table, TableRecordCreate(data={}))

        # other can view the table...
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )
        # ...but the second record goes "unique" with no grant of its own, so the
        # table grant no longer reaches it.
        hidden.permissions_inherit = False
        await db.commit()
        table_uuid = str(table.uuid)

    body = {
        "format": "json",
        "columns": [],
        "filters": {"conjunction": "and", "conditions": []},
        "sorts": [],
        "limit": None,
    }

    async with make_client(other) as client:
        # The grid already hid the unique row; the export must agree with it.
        resp = await client.get(f"/api/tables/{table_uuid}/records/")
        assert len(resp.json()) == 1
        resp = await client.post(f"/api/tables/{table_uuid}/export", json=body)
        assert resp.status_code == 200
        assert len(json.loads(resp.text)) == 1

    # The owner still exports everything.
    async with make_client(owner) as client:
        resp = await client.post(f"/api/tables/{table_uuid}/export", json=body)
        assert len(json.loads(resp.text)) == 2


async def test_http_bulk_delete_skips_records_the_caller_cannot_delete(
    make_client, session_factory
):
    """A bulk delete removes what the caller may remove and silently skips the rest,
    the same way it skips an unknown uuid. Worth pinning at the HTTP level because
    the check is now resolved for the whole batch at once rather than per record: a
    row whose inheritance is broken is out of the table grant's reach, and naming it
    in the batch must not be what gets it deleted."""
    async with session_factory() as db:
        owner, other, _, table = await _fixture_world(db)
        doomed = await TableRecordService.create(db, table, TableRecordCreate(data={}))
        protected = await TableRecordService.create(db, table, TableRecordCreate(data={}))

        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.delete],
            ),
        )
        protected.permissions_inherit = False
        await db.commit()
        table_uuid = str(table.uuid)
        uuids = [str(doomed.uuid), str(protected.uuid)]

    async with make_client(other) as client:
        resp = await client.post(
            f"/api/tables/{table_uuid}/records/bulk-delete", json={"uuids": uuids}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted"] == 1

    # Asked as the owner, who can see both rows: only the granted one went.
    async with make_client(owner) as client:
        remaining = (await client.get(f"/api/tables/{table_uuid}/records/")).json()
        assert [row["uuid"] for row in remaining] == [uuids[1]]


async def test_http_bulk_restore_is_gated_on_the_table_not_per_record(
    make_client, session_factory
):
    """Table-gated for a reason that is not a choice: a soft-deleted record resolves
    to no permission context at all, so there is nothing to check per record even in
    principle. `effective_actions_bulk` could not answer here either — it filters to
    `deleted_at IS NULL`, so everything in the bin is absent from its result.

    Which means a broken-inheritance row comes back along with the rest, where a
    bulk *delete* naming the same two would have skipped it.
    """
    async with session_factory() as db:
        owner, other, _, table = await _fixture_world(db)
        plain = await TableRecordService.create(db, table, TableRecordCreate(data={}))
        detached = await TableRecordService.create(db, table, TableRecordCreate(data={}))

        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view, Action.delete],
            ),
        )
        detached.permissions_inherit = False
        await db.commit()
        table_uuid = str(table.uuid)
        uuids = [str(plain.uuid), str(detached.uuid)]
        for uuid in uuids:
            await TableRecordService.delete(db, table, uuid)

    async with make_client(other) as client:
        resp = await client.post(
            f"/api/tables/{table_uuid}/records/bulk-restore", json={"uuids": uuids}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"restored": 2, "conflicts": []}


async def test_http_bulk_restore_refused_without_delete_on_the_table(
    make_client, session_factory
):
    """Same gate as the bin listing: `view` does not reach it."""
    async with session_factory() as db:
        owner, other, _, table = await _fixture_world(db)
        record = await TableRecordService.create(db, table, TableRecordCreate(data={}))
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )
        await db.commit()
        table_uuid = str(table.uuid)
        uuid = str(record.uuid)
        await TableRecordService.delete(db, table, uuid)

    async with make_client(other) as client:
        resp = await client.post(
            f"/api/tables/{table_uuid}/records/bulk-restore", json={"uuids": [uuid]}
        )
        assert resp.status_code == 403, resp.text


async def test_http_bulk_unarchive_is_gated_on_the_table_not_per_record(
    make_client, session_factory
):
    """The deliberate difference from `bulk-delete`, pinned so it is not "fixed" into
    a per-record check later.

    Taking records *away* resolves per record, because a live record has a context of
    its own and a selection can mix rows the caller may remove with rows they may
    not. Bringing them back matches the surface it is reached from: the archive
    listing and `unarchive` are both table-gated, so a caller with table-level `delete`
    unarchives everything they named — including a row whose inheritance is broken,
    which `bulk-delete` would have skipped.
    """
    async with session_factory() as db:
        owner, other, _, table = await _fixture_world(db)
        plain = await TableRecordService.create(db, table, TableRecordCreate(data={}))
        detached = await TableRecordService.create(db, table, TableRecordCreate(data={}))

        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view, Action.delete],
            ),
        )
        # Out of the table grant's reach for anything resolved per record.
        detached.permissions_inherit = False
        await db.commit()
        table_uuid = str(table.uuid)
        uuids = [str(plain.uuid), str(detached.uuid)]
        for uuid in uuids:
            await TableRecordService.archive(db, table, uuid)

    async with make_client(other) as client:
        resp = await client.post(
            f"/api/tables/{table_uuid}/records/bulk-unarchive", json={"uuids": uuids}
        )
        assert resp.status_code == 200, resp.text
        # Both, where a bulk delete of the same two would have moved one.
        assert resp.json() == {"unarchived": 2}

    async with make_client(owner) as client:
        archived = (
            await client.get(f"/api/tables/{table_uuid}/records/archived")
        ).json()
        assert archived == []


async def test_http_bulk_unarchive_refused_without_delete_on_the_table(
    make_client, session_factory
):
    """`view` is not enough: the authority to see the archive is the authority to
    have put things in it, so the same gate gates taking them out."""
    async with session_factory() as db:
        owner, other, _, table = await _fixture_world(db)
        record = await TableRecordService.create(db, table, TableRecordCreate(data={}))
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )
        await db.commit()
        table_uuid = str(table.uuid)
        uuid = str(record.uuid)
        await TableRecordService.archive(db, table, uuid)

    async with make_client(other) as client:
        resp = await client.post(
            f"/api/tables/{table_uuid}/records/bulk-unarchive", json={"uuids": [uuid]}
        )
        assert resp.status_code == 403, resp.text


@pytest.mark.parametrize(
    "actions, expected",
    [
        ([Action.view], 403),
        # `create` alone is not enough, and that is the upload route's rule rather
        # than a new one: a record can carry a reference that resolves against rows
        # the caller has to be able to write.
        ([Action.view, Action.create], 403),
        ([Action.view, Action.create, Action.edit], 200),
    ],
)
async def test_http_bulk_create_needs_create_and_edit_on_the_table(
    make_client, session_factory, actions, expected
):
    async with session_factory() as db:
        owner, other, _, table = await _fixture_world(db)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=actions,
            ),
        )
        await db.commit()
        table_uuid = str(table.uuid)

    async with make_client(other) as client:
        resp = await client.post(
            f"/api/tables/{table_uuid}/records/bulk-create",
            json={"records": [{}]},
        )
        assert resp.status_code == expected, resp.text


async def test_http_effective_batch(make_client, session_factory):
    """The batch endpoint backs the UI's per-row control gating: one call must
    return the caller's actions per resource, mixing inherited and direct grants."""
    async with session_factory() as db:
        owner, other, ws, table = await _fixture_world(db)
        editable = await TableRecordService.create(db, table, TableRecordCreate(data={}))
        readonly = await TableRecordService.create(db, table, TableRecordCreate(data={}))

        # View on the table (inherited by both rows), edit on just one of them.
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.record,
                resource_uuid=editable.uuid,
                user_uuid=other.uuid,
                actions=[Action.edit],
            ),
        )
        payload = {
            "resources": [
                {"resource_type": "table", "resource_uuid": str(table.uuid)},
                {"resource_type": "record", "resource_uuid": str(editable.uuid)},
                {"resource_type": "record", "resource_uuid": str(readonly.uuid)},
            ]
        }

    async with make_client(other) as client:
        resp = await client.post("/api/permissions/effective", json=payload)
        assert resp.status_code == 200, resp.text
        actions = {row["resource_uuid"]: set(row["actions"]) for row in resp.json()}

    # `view` confers `lookup` — reading a resource includes reading a label out of
    # it — so it rides along on every one of these.
    assert actions[str(table.uuid)] == {"view", "lookup"}
    assert actions[str(editable.uuid)] == {"view", "edit", "lookup"}
    assert actions[str(readonly.uuid)] == {"view", "lookup"}

    # The owner short-circuits to full access on every resource in the batch.
    async with make_client(owner) as client:
        resp = await client.post("/api/permissions/effective", json=payload)
        assert resp.status_code == 200
        for row in resp.json():
            assert set(row["actions"]) == {a.value for a in ALL_ACTIONS}


# --------------------------------------------------------------------------
# "Shared with me" enrichment: owner username always, workspace name only when
# the caller can view that workspace (else it would leak a workspace they were
# specifically not given).
# --------------------------------------------------------------------------


async def test_shared_table_exposes_owner_but_not_a_hidden_workspace(session_factory):
    async with session_factory() as db:
        owner, other, ws, table = await _fixture_world(db)
        # A table-level grant, and crucially *no* workspace grant — so `other` can
        # reach the table but cannot view its workspace.
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )

        shared = await TableService.list(db, other, shared_only=True)
        assert [r.uuid for r in shared] == [table.uuid]
        row = shared[0]
        # Owner username is always exposed — it is the disambiguator.
        assert row.owner_username == "owner"
        # The workspace name is withheld: `other` cannot view "WS".
        assert row.workspace_display_name is None


async def test_owner_listing_includes_its_workspace_name(session_factory):
    async with session_factory() as db:
        owner, _, ws, table = await _fixture_world(db)
        rows = await TableService.list(db, owner, workspace_uuid=ws.uuid)
        assert len(rows) == 1
        # The owner can view the workspace, so its name comes through.
        assert rows[0].workspace_display_name == "WS"
        assert rows[0].owner_username == "owner"


async def test_table_search_and_count_agree(session_factory):
    async with session_factory() as db:
        owner, _, ws, _ = await _fixture_world(db)
        await TableService.create(
            db, TableCreate(name="apples", display_name="Apples", workspace_uuid=ws.uuid)
        )
        await TableService.create(
            db, TableCreate(name="oranges", display_name="Oranges", workspace_uuid=ws.uuid)
        )

        # Baseline: the fixture's "Lst" plus the two just created.
        assert await TableService.count(db, owner, workspace_uuid=ws.uuid) == 3

        matched = await TableService.list(db, owner, workspace_uuid=ws.uuid, q="apple")
        assert [r.name for r in matched] == ["apples"]
        assert await TableService.count(db, owner, workspace_uuid=ws.uuid, q="apple") == 1
        # A miss matches nothing, and the count agrees with the page.
        assert await TableService.list(db, owner, workspace_uuid=ws.uuid, q="zzz") == []
        assert await TableService.count(db, owner, workspace_uuid=ws.uuid, q="zzz") == 0


async def test_bin_authority_is_the_table_not_the_record(make_client, session_factory):
    """The bin (view + restore) is gated on table-level `delete`, because a
    soft-deleted record resolves to no per-record permission context."""
    async with session_factory() as db:
        owner, other, ws, table = await _fixture_world(db)
        record = await TableRecordService.create(db, table, TableRecordCreate(data={}))
        await TableRecordService.delete(db, table, record.uuid)
        table_uuid, record_uuid = str(table.uuid), str(record.uuid)

    # A stranger to the table may neither see nor restore its bin.
    async with make_client(other) as client:
        assert (await client.get(f"/api/tables/{table_uuid}/records/deleted")).status_code == 403
        assert (
            await client.post(f"/api/tables/{table_uuid}/records/{record_uuid}/restore")
        ).status_code == 403

    # A table-level `delete` grant opens the bin.
    async with session_factory() as db:
        owner = await UserService.get_by_uuid(db, owner.uuid)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.delete],
            ),
        )

    async with make_client(other) as client:
        resp = await client.get(f"/api/tables/{table_uuid}/records/deleted")
        assert resp.status_code == 200, resp.text
        assert [r["uuid"] for r in resp.json()] == [record_uuid]

    # And the owner always qualifies, grant or no grant.
    async with make_client(owner) as client:
        assert (await client.get(f"/api/tables/{table_uuid}/records/deleted")).status_code == 200


async def test_http_tables_sends_total_count_header(make_client, session_factory):
    async with session_factory() as db:
        owner, _, ws, _ = await _fixture_world(db)
        await TableService.create(
            db, TableCreate(name="apples", display_name="Apples", workspace_uuid=ws.uuid)
        )

    async with make_client(owner) as client:
        resp = await client.get(f"/api/tables/?workspace_uuid={ws.uuid}&limit=1")
        assert resp.status_code == 200, resp.text
        # The body is capped by limit, but the header reports the full match count.
        assert len(resp.json()) == 1
        assert resp.headers["X-Total-Count"] == "2"


# --------------------------------------------------------------------------
# Grant expiry: an expired grant confers nothing, everywhere at once.
# --------------------------------------------------------------------------


async def test_expired_grant_confers_nothing(session_factory):
    """The predicate lives in `_principal_condition`, so it must bite both the
    point check (`effective_actions`) and the collection filter (`table_query_filter`,
    exercised here through `TableService.table`)."""
    async with session_factory() as db:
        owner, other, ws, table = await _fixture_world(db)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )
        # Live: access granted, and the table shows up in "Shared with me".
        assert Action.view in await PermissionService.effective_actions(
            db, other, ResourceType.table, table.id
        )
        shared = await TableService.list(db, other, shared_only=True)
        assert table.uuid in [r.uuid for r in shared]

        # Force it into the past (the API's future-only guard blocks doing this the
        # normal way — which is the point of doing it directly here).
        grant = (
            await db.execute(
                select(PermissionGrant).where(PermissionGrant.user_id == other.id)
            )
        ).scalars().first()
        grant.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await db.commit()

        # Expired: confers nothing, and drops out of the collection filter too.
        assert await PermissionService.effective_actions(
            db, other, ResourceType.table, table.id
        ) == set()
        shared = await TableService.list(db, other, shared_only=True)
        assert table.uuid not in [r.uuid for r in shared]


async def test_upsert_updates_then_clears_expiry(session_factory):
    async with session_factory() as db:
        owner, other, ws, table = await _fixture_world(db)
        future = datetime.now(timezone.utc) + timedelta(days=3)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
                expires_at=future,
            ),
        )
        grants = await PermissionService.list_grants(
            db, owner, ResourceType.table, table.uuid
        )
        assert grants[0].expires_at is not None

        # Re-sharing without an expiry clears the previous one (verbatim write).
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )
        grants = await PermissionService.list_grants(
            db, owner, ResourceType.table, table.uuid
        )
        assert grants[0].expires_at is None


async def test_http_future_expiry_grants_access_and_past_is_rejected(
    make_client, session_factory
):
    async with session_factory() as db:
        owner, other, ws, table = await _fixture_world(db)
        table_uuid, other_uuid = str(table.uuid), str(other.uuid)

    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    async with make_client(owner) as client:
        # A past expiry is refused up front rather than stored inert.
        bad = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "table",
                "resource_uuid": table_uuid,
                "user_uuid": other_uuid,
                "actions": ["view"],
                "expires_at": past,
            },
        )
        assert bad.status_code == 422, bad.text

        ok = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "table",
                "resource_uuid": table_uuid,
                "user_uuid": other_uuid,
                "actions": ["view"],
                "expires_at": future,
            },
        )
        assert ok.status_code == 201, ok.text
        assert ok.json()["expires_at"] is not None
        # The listing carries the expiry back to the Share panel.
        grants = (
            await client.get(
                f"/api/permissions/grants?resource_type=table&resource_uuid={table_uuid}"
            )
        ).json()
        assert grants[0]["expires_at"] is not None

    # While the grant is live, the grantee can open the table.
    async with make_client(other) as client:
        assert (await client.get(f"/api/tables/{table_uuid}")).status_code == 200
