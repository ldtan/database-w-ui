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
from src.auth.users.schemas import UserCreate, UserGroupCreate
from src.auth.users.services import UserGroupService, UserService
from src.infrastructure import database as db_module
from src.lists.forms.models import Form
from src.lists.items.schemas import ListItemCreate
from src.lists.items.services import ListItemService
from src.lists.schemas import ListCreate, ListUpdate
from src.lists.services import ListService
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
    """Owner + a second user + an empty workspace/list owned by the owner."""
    owner = await _make_user(db, "owner")
    other = await _make_user(db, "other")
    ws = await WorkspaceService.create(
        db, owner.id, WorkspaceCreate(name="ws", display_name="WS")
    )
    lst = await ListService.create(
        db, ListCreate(name="lst", display_name="Lst", workspace_uuid=ws.uuid)
    )
    return owner, other, ws, lst


# --------------------------------------------------------------------------
# Service-level resolver tests
# --------------------------------------------------------------------------


async def test_owner_has_all_actions(session_factory):
    async with session_factory() as db:
        owner, _, _, lst = await _fixture_world(db)
        actions = await PermissionService.effective_actions(
            db, owner, ResourceType.list, lst.id
        )
        assert actions == set(ALL_ACTIONS)


async def test_non_owner_starts_with_nothing(session_factory):
    async with session_factory() as db:
        _, other, _, lst = await _fixture_world(db)
        actions = await PermissionService.effective_actions(
            db, other, ResourceType.list, lst.id
        )
        assert actions == set()


async def test_workspace_grant_inherits_to_list_and_items(session_factory):
    async with session_factory() as db:
        owner, other, ws, lst = await _fixture_world(db)
        item = await ListItemService.create(db, lst, ListItemCreate(data={}))

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
            db, other, ResourceType.list, lst.id, Action.view
        )
        assert await PermissionService.has(
            db, other, ResourceType.item, item.id, Action.view
        )


async def test_edit_implies_view_and_manage_implies_all(session_factory):
    async with session_factory() as db:
        owner, other, _, lst = await _fixture_world(db)

        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                user_uuid=other.uuid,
                actions=[Action.edit],
            ),
        )
        actions = await PermissionService.effective_actions(
            db, other, ResourceType.list, lst.id
        )
        assert {Action.edit, Action.view} <= actions
        assert Action.delete not in actions
        assert Action.configure not in actions

        # Overwrite with manage -> everything.
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                user_uuid=other.uuid,
                actions=[Action.manage],
            ),
        )
        assert await PermissionService.effective_actions(
            db, other, ResourceType.list, lst.id
        ) == set(ALL_ACTIONS)


async def test_group_grant_applies_to_members(session_factory):
    async with session_factory() as db:
        owner, member, _, lst = await _fixture_world(db)
        group = await UserGroupService.create(
            db, UserGroupCreate(name="team", display_name="Team", user_uuids=[member.uuid])
        )

        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                group_uuid=group.uuid,
                actions=[Action.view],
            ),
        )
        assert await PermissionService.has(
            db, member, ResourceType.list, lst.id, Action.view
        )


async def test_unique_list_ignores_workspace_grant(session_factory):
    async with session_factory() as db:
        owner, other, ws, lst = await _fixture_world(db)
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
            db, other, ResourceType.list, lst.id, Action.view
        )

        # Break inheritance: the list now stands alone with no direct grant.
        await ListService.update(db, lst.uuid, ListUpdate(permissions_inherit=False))
        assert not await PermissionService.has(
            db, other, ResourceType.list, lst.id, Action.view
        )

        # Rejoining restores the inherited access.
        await ListService.update(db, lst.uuid, ListUpdate(permissions_inherit=True))
        assert await PermissionService.has(
            db, other, ResourceType.list, lst.id, Action.view
        )


async def test_item_level_grant_is_isolated(session_factory):
    async with session_factory() as db:
        owner, other, _, lst = await _fixture_world(db)
        shared = await ListItemService.create(db, lst, ListItemCreate(data={}))
        sibling = await ListItemService.create(db, lst, ListItemCreate(data={}))

        # The list itself is not shared; only one row is granted directly.
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.item,
                resource_uuid=shared.uuid,
                user_uuid=other.uuid,
                actions=[Action.edit],
            ),
        )
        assert await PermissionService.has(
            db, other, ResourceType.item, shared.id, Action.edit
        )
        assert not await PermissionService.has(
            db, other, ResourceType.item, sibling.id, Action.view
        )


async def test_form_targeting_and_configure_scope(session_factory):
    async with session_factory() as db:
        owner, other, _, lst = await _fixture_world(db)
        member = await _make_user(db, "member")
        group = await UserGroupService.create(
            db, UserGroupCreate(name="audience", display_name="Audience", user_uuids=[member.uuid])
        )

        # Both other and member can view the list.
        for principal in (other, member):
            await PermissionService.upsert_grant(
                db,
                owner,
                GrantCreate(
                    resource_type=ResourceType.list,
                    resource_uuid=lst.uuid,
                    user_uuid=principal.uuid,
                    actions=[Action.view],
                ),
            )

        forms = (
            await db.execute(select(Form).where(Form.list_id == lst.id).order_by(Form.id))
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

        # form_a: only the group member sees it, not the plain list-viewer.
        assert await PermissionService.has(
            db, member, ResourceType.form, form_a.id, Action.view
        )
        assert not await PermissionService.has(
            db, other, ResourceType.form, form_a.id, Action.view
        )
        # form_b still inherits, so both list-viewers see it.
        assert await PermissionService.has(
            db, other, ResourceType.form, form_b.id, Action.view
        )

        # configure grant confers config rights without record edit or share.
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                user_uuid=other.uuid,
                actions=[Action.configure],
            ),
        )
        actions = await PermissionService.effective_actions(
            db, other, ResourceType.list, lst.id
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
        owner, _, _, lst = await _fixture_world(db)
        refs = []
        for _ in range(12):
            item = await ListItemService.create(db, lst, ListItemCreate(data={}))
            refs.append((ResourceType.item, item.uuid))
        # Keep the uuids, not the records: a caller holding the instances would keep
        # them alive on its own and hide exactly the effect under test.
        del item
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
        owner, other, ws, lst = await _fixture_world(db)
        list_uuid = str(lst.uuid)
        other_uuid = str(other.uuid)

    # Before any grant, the second user cannot see the list.
    async with make_client(other) as client:
        assert (await client.get(f"/api/lists/{list_uuid}")).status_code == 403

    # The owner shares 'view'.
    async with make_client(owner) as client:
        resp = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "list",
                "resource_uuid": list_uuid,
                "user_uuid": other_uuid,
                "actions": ["view"],
            },
        )
        assert resp.status_code == 201, resp.text

    # Now the second user can view, but configuring is still forbidden.
    async with make_client(other) as client:
        assert (await client.get(f"/api/lists/{list_uuid}")).status_code == 200
        resp = await client.post(
            f"/api/lists/{list_uuid}/columns/",
            json={"name": "title", "display_name": "Title", "type": "text"},
        )
        assert resp.status_code == 403


async def test_http_non_sharer_cannot_grant(make_client, session_factory):
    async with session_factory() as db:
        owner, other, _, lst = await _fixture_world(db)
        list_uuid = str(lst.uuid)
        other_uuid = str(other.uuid)
        # other only gets 'view' — not 'share'.
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )

    async with make_client(other) as client:
        resp = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "list",
                "resource_uuid": list_uuid,
                "user_uuid": other_uuid,
                "actions": ["manage"],
            },
        )
        assert resp.status_code == 403


async def test_http_shared_with_me(make_client, session_factory):
    """A directly-granted list sits in a workspace the grantee cannot open, so it
    is only reachable through the shared listing."""
    async with session_factory() as db:
        owner, other, ws, lst = await _fixture_world(db)
        workspace_uuid = str(ws.uuid)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )
        list_uuid = str(lst.uuid)

    async with make_client(other) as client:
        # Only the list was shared, so the workspace holding it stays invisible;
        # the grantee still sees their own auto-provisioned personal workspace.
        resp = await client.get("/api/workspaces/")
        assert workspace_uuid not in [w["uuid"] for w in resp.json()]

        resp = await client.get("/api/lists/?shared=true")
        assert [item["uuid"] for item in resp.json()] == [list_uuid]

    # The owner owns everything they can see, so nothing is "shared with" them.
    async with make_client(owner) as client:
        resp = await client.get("/api/lists/?shared=true")
        assert resp.json() == []
        # ...but the list is still there unfiltered, under its workspace.
        resp = await client.get(f"/api/lists/?workspace_uuid={workspace_uuid}")
        assert [item["uuid"] for item in resp.json()] == [list_uuid]


async def test_http_workspace_grant_does_not_orphan_its_lists(
    make_client, session_factory
):
    """Sharing a *workspace* shares its lists too, but those lists are reachable by
    browsing the workspace — so they must not also appear in the flat "Shared with
    me" section, which exists only for lists that have nowhere else to hang.

    The two filters answer different questions: `shared` is about ownership (and the
    lookup target picker wants every non-owned list), `workspace_hidden` is about
    reachability. This is the case where they disagree.
    """
    async with session_factory() as db:
        owner, other, ws, lst = await _fixture_world(db)
        workspace_uuid = str(ws.uuid)
        list_uuid = str(lst.uuid)
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

        # The list is shared — it is not owned by the caller.
        resp = await client.get("/api/lists/?shared=true")
        assert [item["uuid"] for item in resp.json()] == [list_uuid]

        # ...but it is *not* homeless: its workspace is right there to browse into,
        # so it is excluded from the section that would show it a second time.
        resp = await client.get("/api/lists/?shared=true&workspace_hidden=true")
        assert resp.json() == []
        assert resp.headers["X-Total-Count"] == "0"

        # The complement returns it, and the paginator's total agrees with the body
        # — the two go through one set of conditions precisely so they cannot drift.
        resp = await client.get("/api/lists/?shared=true&workspace_hidden=false")
        assert [item["uuid"] for item in resp.json()] == [list_uuid]
        assert resp.headers["X-Total-Count"] == "1"


async def test_http_list_grant_survives_the_workspace_hidden_filter(
    make_client, session_factory
):
    """The other half: a list granted *without* its workspace is reachable no other
    way, so it must pass the filter the nav tree's "Shared with me" applies."""
    async with session_factory() as db:
        owner, other, ws, lst = await _fixture_world(db)
        workspace_uuid = str(ws.uuid)
        list_uuid = str(lst.uuid)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )

    async with make_client(other) as client:
        resp = await client.get("/api/workspaces/?shared=true")
        assert workspace_uuid not in [w["uuid"] for w in resp.json()]

        resp = await client.get("/api/lists/?shared=true&workspace_hidden=true")
        assert [item["uuid"] for item in resp.json()] == [list_uuid]
        assert resp.headers["X-Total-Count"] == "1"
        # Withheld, as ever: naming a workspace the caller was denied would leak it.
        assert resp.json()[0]["workspace_display_name"] is None


@pytest.mark.parametrize("break_inheritance", [False, True])
async def test_http_a_shared_list_is_always_reachable_somewhere(
    break_inheritance, make_client, session_factory
):
    """The invariant the whole split rests on: filtering the flat section down to
    homeless lists must never hide a list that has no home either.

    The dangerous shape is a list held by a *direct* grant whose workspace is also
    shared — it fails `workspace_hidden`, so it is only reachable if browsing that
    workspace finds it. Doubly so when the list has opted out of inheritance and
    stands on the direct grant alone, which is the case where the workspace-level
    and list-level paths disagree about why it is visible.
    """
    async with session_factory() as db:
        owner, other, ws, lst = await _fixture_world(db)
        workspace_uuid = str(ws.uuid)
        list_uuid = str(lst.uuid)
        for resource_type, resource_uuid in (
            (ResourceType.workspace, ws.uuid),
            (ResourceType.list, lst.uuid),
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
            await ListService.update(
                db, lst.uuid, ListUpdate(permissions_inherit=False)
            )

    async with make_client(other) as client:
        # Not homeless: its workspace is right there, so it stays out of the flat
        # run rather than appearing twice.
        resp = await client.get("/api/lists/?shared=true&workspace_hidden=true")
        assert resp.json() == []
        # ...and the home it was excluded in favour of really does hold it.
        resp = await client.get("/api/workspaces/?shared=true")
        assert workspace_uuid in [w["uuid"] for w in resp.json()]
        resp = await client.get(f"/api/lists/?workspace_uuid={workspace_uuid}")
        assert list_uuid in [item["uuid"] for item in resp.json()]


async def test_http_lookup_target_picker_keeps_the_whole_shared_set(
    make_client, session_factory
):
    """A regression guard for the filter split: the lookup target picker asks for
    `shared=true` alone, and must keep seeing lists that arrived through a workspace
    grant. Narrowing `shared` itself would have silently emptied that picker."""
    async with session_factory() as db:
        owner, other, ws, lst = await _fixture_world(db)
        list_uuid = str(lst.uuid)
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
        resp = await client.get("/api/lists/?shared=true&lookup_targets=true")
        assert list_uuid in [item["uuid"] for item in resp.json()]


async def test_http_export_respects_item_permissions(make_client, session_factory):
    """Export is a read of the records, so it must hide the same rows the grid does.
    It previously streamed every row in the list, leaking records whose inheritance
    was broken and were never granted to the caller."""
    async with session_factory() as db:
        owner, other, _, lst = await _fixture_world(db)
        await ListItemService.create(db, lst, ListItemCreate(data={}))
        hidden = await ListItemService.create(db, lst, ListItemCreate(data={}))

        # other can view the list...
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )
        # ...but the second record goes "unique" with no grant of its own, so the
        # list grant no longer reaches it.
        hidden.permissions_inherit = False
        await db.commit()
        list_uuid = str(lst.uuid)

    body = {
        "format": "json",
        "columns": [],
        "filters": {"conjunction": "and", "conditions": []},
        "sorts": [],
        "limit": None,
    }

    async with make_client(other) as client:
        # The grid already hid the unique row; the export must agree with it.
        resp = await client.get(f"/api/lists/{list_uuid}/items/")
        assert len(resp.json()) == 1
        resp = await client.post(f"/api/lists/{list_uuid}/export", json=body)
        assert resp.status_code == 200
        assert len(json.loads(resp.text)) == 1

    # The owner still exports everything.
    async with make_client(owner) as client:
        resp = await client.post(f"/api/lists/{list_uuid}/export", json=body)
        assert len(json.loads(resp.text)) == 2


async def test_http_bulk_delete_skips_records_the_caller_cannot_delete(
    make_client, session_factory
):
    """A bulk delete removes what the caller may remove and silently skips the rest,
    the same way it skips an unknown uuid. Worth pinning at the HTTP level because
    the check is now resolved for the whole batch at once rather than per record: a
    row whose inheritance is broken is out of the list grant's reach, and naming it
    in the batch must not be what gets it deleted."""
    async with session_factory() as db:
        owner, other, _, lst = await _fixture_world(db)
        doomed = await ListItemService.create(db, lst, ListItemCreate(data={}))
        protected = await ListItemService.create(db, lst, ListItemCreate(data={}))

        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                user_uuid=other.uuid,
                actions=[Action.delete],
            ),
        )
        protected.permissions_inherit = False
        await db.commit()
        list_uuid = str(lst.uuid)
        uuids = [str(doomed.uuid), str(protected.uuid)]

    async with make_client(other) as client:
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/bulk-delete", json={"uuids": uuids}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted"] == 1

    # Asked as the owner, who can see both rows: only the granted one went.
    async with make_client(owner) as client:
        remaining = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
        assert [row["uuid"] for row in remaining] == [uuids[1]]


async def test_http_effective_batch(make_client, session_factory):
    """The batch endpoint backs the UI's per-row control gating: one call must
    return the caller's actions per resource, mixing inherited and direct grants."""
    async with session_factory() as db:
        owner, other, ws, lst = await _fixture_world(db)
        editable = await ListItemService.create(db, lst, ListItemCreate(data={}))
        readonly = await ListItemService.create(db, lst, ListItemCreate(data={}))

        # View on the list (inherited by both rows), edit on just one of them.
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.item,
                resource_uuid=editable.uuid,
                user_uuid=other.uuid,
                actions=[Action.edit],
            ),
        )
        payload = {
            "resources": [
                {"resource_type": "list", "resource_uuid": str(lst.uuid)},
                {"resource_type": "item", "resource_uuid": str(editable.uuid)},
                {"resource_type": "item", "resource_uuid": str(readonly.uuid)},
            ]
        }

    async with make_client(other) as client:
        resp = await client.post("/api/permissions/effective", json=payload)
        assert resp.status_code == 200, resp.text
        actions = {row["resource_uuid"]: set(row["actions"]) for row in resp.json()}

    # `view` confers `lookup` — reading a resource includes reading a label out of
    # it — so it rides along on every one of these.
    assert actions[str(lst.uuid)] == {"view", "lookup"}
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


async def test_shared_list_exposes_owner_but_not_a_hidden_workspace(session_factory):
    async with session_factory() as db:
        owner, other, ws, lst = await _fixture_world(db)
        # A list-level grant, and crucially *no* workspace grant — so `other` can
        # reach the list but cannot view its workspace.
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )

        shared = await ListService.list(db, other, shared_only=True)
        assert [r.uuid for r in shared] == [lst.uuid]
        row = shared[0]
        # Owner username is always exposed — it is the disambiguator.
        assert row.owner_username == "owner"
        # The workspace name is withheld: `other` cannot view "WS".
        assert row.workspace_display_name is None


async def test_owner_listing_includes_its_workspace_name(session_factory):
    async with session_factory() as db:
        owner, _, ws, lst = await _fixture_world(db)
        rows = await ListService.list(db, owner, workspace_uuid=ws.uuid)
        assert len(rows) == 1
        # The owner can view the workspace, so its name comes through.
        assert rows[0].workspace_display_name == "WS"
        assert rows[0].owner_username == "owner"


async def test_list_search_and_count_agree(session_factory):
    async with session_factory() as db:
        owner, _, ws, _ = await _fixture_world(db)
        await ListService.create(
            db, ListCreate(name="apples", display_name="Apples", workspace_uuid=ws.uuid)
        )
        await ListService.create(
            db, ListCreate(name="oranges", display_name="Oranges", workspace_uuid=ws.uuid)
        )

        # Baseline: the fixture's "Lst" plus the two just created.
        assert await ListService.count(db, owner, workspace_uuid=ws.uuid) == 3

        matched = await ListService.list(db, owner, workspace_uuid=ws.uuid, q="apple")
        assert [r.name for r in matched] == ["apples"]
        assert await ListService.count(db, owner, workspace_uuid=ws.uuid, q="apple") == 1
        # A miss matches nothing, and the count agrees with the page.
        assert await ListService.list(db, owner, workspace_uuid=ws.uuid, q="zzz") == []
        assert await ListService.count(db, owner, workspace_uuid=ws.uuid, q="zzz") == 0


async def test_bin_authority_is_the_list_not_the_record(make_client, session_factory):
    """The bin (view + restore) is gated on list-level `delete`, because a
    soft-deleted record resolves to no per-item permission context."""
    async with session_factory() as db:
        owner, other, ws, lst = await _fixture_world(db)
        item = await ListItemService.create(db, lst, ListItemCreate(data={}))
        await ListItemService.delete(db, lst, item.uuid)
        list_uuid, item_uuid = str(lst.uuid), str(item.uuid)

    # A stranger to the list may neither see nor restore its bin.
    async with make_client(other) as client:
        assert (await client.get(f"/api/lists/{list_uuid}/items/deleted")).status_code == 403
        assert (
            await client.post(f"/api/lists/{list_uuid}/items/{item_uuid}/restore")
        ).status_code == 403

    # A list-level `delete` grant opens the bin.
    async with session_factory() as db:
        owner = await UserService.get_by_uuid(db, owner.uuid)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                user_uuid=other.uuid,
                actions=[Action.delete],
            ),
        )

    async with make_client(other) as client:
        resp = await client.get(f"/api/lists/{list_uuid}/items/deleted")
        assert resp.status_code == 200, resp.text
        assert [r["uuid"] for r in resp.json()] == [item_uuid]

    # And the owner always qualifies, grant or no grant.
    async with make_client(owner) as client:
        assert (await client.get(f"/api/lists/{list_uuid}/items/deleted")).status_code == 200


async def test_http_lists_sends_total_count_header(make_client, session_factory):
    async with session_factory() as db:
        owner, _, ws, _ = await _fixture_world(db)
        await ListService.create(
            db, ListCreate(name="apples", display_name="Apples", workspace_uuid=ws.uuid)
        )

    async with make_client(owner) as client:
        resp = await client.get(f"/api/lists/?workspace_uuid={ws.uuid}&limit=1")
        assert resp.status_code == 200, resp.text
        # The body is capped by limit, but the header reports the full match count.
        assert len(resp.json()) == 1
        assert resp.headers["X-Total-Count"] == "2"


# --------------------------------------------------------------------------
# Grant expiry: an expired grant confers nothing, everywhere at once.
# --------------------------------------------------------------------------


async def test_expired_grant_confers_nothing(session_factory):
    """The predicate lives in `_principal_condition`, so it must bite both the
    point check (`effective_actions`) and the collection filter (`list_query_filter`,
    exercised here through `ListService.list`)."""
    async with session_factory() as db:
        owner, other, ws, lst = await _fixture_world(db)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )
        # Live: access granted, and the list shows up in "Shared with me".
        assert Action.view in await PermissionService.effective_actions(
            db, other, ResourceType.list, lst.id
        )
        shared = await ListService.list(db, other, shared_only=True)
        assert lst.uuid in [r.uuid for r in shared]

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
            db, other, ResourceType.list, lst.id
        ) == set()
        shared = await ListService.list(db, other, shared_only=True)
        assert lst.uuid not in [r.uuid for r in shared]


async def test_upsert_updates_then_clears_expiry(session_factory):
    async with session_factory() as db:
        owner, other, ws, lst = await _fixture_world(db)
        future = datetime.now(timezone.utc) + timedelta(days=3)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
                expires_at=future,
            ),
        )
        grants = await PermissionService.list_grants(
            db, owner, ResourceType.list, lst.uuid
        )
        assert grants[0].expires_at is not None

        # Re-sharing without an expiry clears the previous one (verbatim write).
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=lst.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )
        grants = await PermissionService.list_grants(
            db, owner, ResourceType.list, lst.uuid
        )
        assert grants[0].expires_at is None


async def test_http_future_expiry_grants_access_and_past_is_rejected(
    make_client, session_factory
):
    async with session_factory() as db:
        owner, other, ws, lst = await _fixture_world(db)
        list_uuid, other_uuid = str(lst.uuid), str(other.uuid)

    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    async with make_client(owner) as client:
        # A past expiry is refused up front rather than stored inert.
        bad = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "list",
                "resource_uuid": list_uuid,
                "user_uuid": other_uuid,
                "actions": ["view"],
                "expires_at": past,
            },
        )
        assert bad.status_code == 422, bad.text

        ok = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "list",
                "resource_uuid": list_uuid,
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
                f"/api/permissions/grants?resource_type=list&resource_uuid={list_uuid}"
            )
        ).json()
        assert grants[0]["expires_at"] is not None

    # While the grant is live, the grantee can open the list.
    async with make_client(other) as client:
        assert (await client.get(f"/api/lists/{list_uuid}")).status_code == 200
