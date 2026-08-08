"""Writing a lookup cell whose target belongs to somebody else.

A lookup column may point at any list its creator can `lookup` — their own, one in
another of their workspaces, or one shared to them by another *owner*
(`ColumnService._validate_config`). Reading it has always obeyed the same rule from
the other side: `lookup_options`, the export formatter and search-by-linked-label each
re-check the **reader's** permission on the target.

The write path did not. `_lookup_target_id` resolved the target with
`Workspace.owner_id == lst.workspace.owner_id`, so a cross-owner lookup could be built
and would resolve its labels, and then every attempt to put a value in it failed with
"points at a missing target list" — buildable and unwritable. Nothing tested that path,
which is why it survived.

These pin the fix from both sides: the write now works, and the liveness predicate that
remains still refuses a target that is genuinely gone.
"""
from contextlib import asynccontextmanager

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.infrastructure import database as db_module


@pytest_asyncio.fixture
def make_client(engine, session_factory):
    @asynccontextmanager
    async def _make(user):
        orig_engine, orig_factory = db_module.engine, db_module.SessionLocal
        db_module.engine, db_module.SessionLocal = engine, session_factory
        app = create_app()
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client:
                yield client
        finally:
            db_module.engine, db_module.SessionLocal = orig_engine, orig_factory

    return _make


async def _user(session_factory, name):
    async with session_factory() as db:
        created = await UserService.create(
            db, UserCreate(username=name, email=f"{name}@example.com", password="pw")
        )
        return created, str(created.uuid)


async def _grant(client, resource_uuid, user_uuid, actions):
    resp = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "list",
            "resource_uuid": resource_uuid,
            "user_uuid": user_uuid,
            "actions": actions,
        },
    )
    assert resp.status_code == 201, resp.text


async def _labelled_list(client, workspace_uuid, add_column, name, rows):
    """A list labelled by its primary column, with `rows` in it."""
    lst = (
        await client.post(
            "/api/lists/",
            json={
                "name": name,
                "display_name": name.title(),
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    label_col = await add_column(lst, name="name", display_name="Name", type="text")
    await client.patch(f"/api/lists/{lst}", json={"primary_column_uuid": label_col})

    made = {}
    for row in rows:
        created = await client.post(
            f"/api/lists/{lst}/items/", json={"data": {label_col: row}}
        )
        assert created.status_code == 201, created.text
        made[row] = created.json()["uuid"]
    return lst, label_col, made


async def _own_workspace(client):
    resp = await client.get("/api/workspaces/")
    assert resp.status_code == 200, resp.text
    return resp.json()[0]["uuid"]


async def _cross_owner_setup(
    client, make_client, session_factory, workspace_uuid, add_column, name
):
    """Owner A holds a reference list and shares it as a lookup target with `name`,
    who then builds a list of their own pointing at it. Returns everything needed to
    write a cell as the second owner."""
    target, _, rows = await _labelled_list(
        client, workspace_uuid, add_column, "schools", ["Springfield High"]
    )
    other, other_uuid = await _user(session_factory, name)
    await _grant(client, target, other_uuid, ["lookup"])

    async with make_client(other) as second:
        own_ws = await _own_workspace(second)
        own_list = (
            await second.post(
                "/api/lists/",
                json={
                    "name": "pupils",
                    "display_name": "Pupils",
                    "workspace_uuid": own_ws,
                },
            )
        ).json()["uuid"]
        made = await second.post(
            f"/api/lists/{own_list}/columns/",
            json={
                "name": "school",
                "display_name": "School",
                "type": "lookup",
                "config": {"target_list_uuid": target},
            },
        )
        # Building it across owners already worked; it is the write that did not.
        assert made.status_code == 201, made.text
        lookup = made.json()["uuid"]

    return other, target, rows, own_list, lookup


async def test_a_cross_owner_lookup_cell_can_be_written_by_uuid(
    client, make_client, session_factory, workspace_uuid, add_column
):
    """The bug, in one test: this used to 400 with "points at a missing target list"."""
    other, _, rows, own_list, lookup = await _cross_owner_setup(
        client, make_client, session_factory, workspace_uuid, add_column, "linker"
    )

    async with make_client(other) as second:
        created = await second.post(
            f"/api/lists/{own_list}/items/",
            json={"data": {lookup: rows["Springfield High"]}},
        )
        assert created.status_code == 201, created.text
        assert created.json()["data"][lookup] == rows["Springfield High"]


async def test_a_cross_owner_lookup_cell_can_be_written_by_label(
    client, make_client, session_factory, workspace_uuid, add_column
):
    """The label branch resolves through `_lookup_display_key`, which hangs off the
    same cached target id — so it is a genuinely separate path to the same fix, and
    the one an imported CSV takes."""
    other, _, rows, own_list, lookup = await _cross_owner_setup(
        client, make_client, session_factory, workspace_uuid, add_column, "importer"
    )

    async with make_client(other) as second:
        created = await second.post(
            f"/api/lists/{own_list}/items/",
            json={"data": {lookup: "Springfield High"}},
        )
        assert created.status_code == 201, created.text
        # Stored as the uuid, whatever was typed.
        assert created.json()["data"][lookup] == rows["Springfield High"]


async def test_a_cross_owner_lookup_cell_survives_a_second_write(
    client, make_client, session_factory, workspace_uuid, add_column
):
    """Two writes in one request share the session-scoped target-id cache, whose key
    lost its owner component in the fix. If that re-key were wrong, the second write
    would miss the cache or read the wrong entry."""
    other, _, rows, own_list, lookup = await _cross_owner_setup(
        client, make_client, session_factory, workspace_uuid, add_column, "repeater"
    )

    async with make_client(other) as second:
        for _ in range(2):
            created = await second.post(
                f"/api/lists/{own_list}/items/",
                json={"data": {lookup: rows["Springfield High"]}},
            )
            assert created.status_code == 201, created.text

        listed = await second.get(f"/api/lists/{own_list}/items/")
        assert [row["data"][lookup] for row in listed.json()] == [
            rows["Springfield High"],
            rows["Springfield High"],
        ]


async def test_a_lookup_at_a_deleted_target_list_is_still_refused(
    client, make_client, session_factory, workspace_uuid, add_column
):
    """The regression guard for the fix itself: dropping the owner predicate must not
    have dropped the liveness one with it."""
    other, target, rows, own_list, lookup = await _cross_owner_setup(
        client, make_client, session_factory, workspace_uuid, add_column, "orphan"
    )

    deleted = await client.delete(f"/api/lists/{target}")
    assert deleted.status_code == 204, deleted.text

    async with make_client(other) as second:
        refused = await second.post(
            f"/api/lists/{own_list}/items/",
            json={"data": {lookup: rows["Springfield High"]}},
        )
        assert refused.status_code == 400, refused.text
        assert "missing target list" in refused.text


async def test_a_same_owner_lookup_still_works(
    client, workspace_uuid, list_uuid, add_column
):
    """The path that always worked, unchanged."""
    target, _, rows = await _labelled_list(
        client, workspace_uuid, add_column, "departments", ["Physics"]
    )
    lookup = await add_column(
        list_uuid,
        name="department",
        display_name="Department",
        type="lookup",
        config={"target_list_uuid": target},
    )

    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {lookup: rows["Physics"]}}
    )
    assert created.status_code == 201, created.text
    assert created.json()["data"][lookup] == rows["Physics"]
