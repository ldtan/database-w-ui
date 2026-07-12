"""Permission/authorization tests.

Because DISABLE_AUTH resolves the caller to the seeded owner, the core resolver
logic is exercised directly against the service layer with several distinct users,
and HTTP enforcement is checked via a `make_client` helper that overrides
get_current_user to act as a chosen user.
"""
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
