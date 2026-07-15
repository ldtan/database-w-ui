"""Permission/authorization tests.

Because DISABLE_AUTH resolves the caller to the seeded owner, the core resolver
logic is exercised directly against the service layer with several distinct users,
and HTTP enforcement is checked via a `make_client` helper that overrides
get_current_user to act as a chosen user.
"""
import json
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

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
            db, UserGroupCreate(name="team", user_uuids=[member.uuid])
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
            db, UserGroupCreate(name="audience", user_uuids=[member.uuid])
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

    assert actions[str(lst.uuid)] == {"view"}
    assert actions[str(editable.uuid)] == {"view", "edit"}
    assert actions[str(readonly.uuid)] == {"view"}

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
