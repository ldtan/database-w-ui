"""`GET /lists/{list}/columns/{column}/options` — the lookup picker's backend.

The picker used to fetch a whole target list and filter it in the browser, which
silently stops offering rows once the target has more of them than the fetch's
limit. Matching now happens server-side; the grid resolves the uuids it holds
through the same endpoint.

The label is derived from `display_column_key` — the same function export and
import use — so "the label of a row" means one thing across the app.
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


async def _target_with_rows(client, workspace_uuid, add_column, names):
    """A "Schools" list labelled by its primary column, plus a lookup at it."""
    target = (
        await client.post(
            "/api/lists/",
            json={
                "name": "schools",
                "display_name": "Schools",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    label_col = await add_column(target, name="name", display_name="Name", type="text")
    await client.patch(f"/api/lists/{target}", json={"primary_column_uuid": label_col})

    rows = {}
    for name in names:
        created = await client.post(
            f"/api/lists/{target}/items/", json={"data": {label_col: name}}
        )
        rows[name] = created.json()["uuid"]
    return target, rows


async def test_options_search_by_label(client, workspace_uuid, list_uuid, add_column):
    target, rows = await _target_with_rows(
        client, workspace_uuid, add_column, ["Springfield High", "Shelbyville High", "Ogdenville"]
    )
    lookup = await add_column(
        list_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_list_uuid": target},
    )

    resp = await client.get(f"/api/lists/{list_uuid}/columns/{lookup}/options?q=high")
    assert resp.status_code == 200, resp.text
    assert sorted(o["label"] for o in resp.json()) == [
        "Shelbyville High",
        "Springfield High",
    ]

    # No query: the first page of rows, so an empty picker still has something.
    every = (await client.get(f"/api/lists/{list_uuid}/columns/{lookup}/options")).json()
    assert len(every) == 3


async def test_options_resolve_specific_rows(
    client, workspace_uuid, list_uuid, add_column
):
    """What a grid needs: the labels for the uuids its cells already hold."""
    target, rows = await _target_with_rows(
        client, workspace_uuid, add_column, ["Alpha", "Beta", "Gamma"]
    )
    lookup = await add_column(
        list_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_list_uuid": target},
    )

    wanted = f"uuid={rows['Alpha']}&uuid={rows['Gamma']}"
    resp = await client.get(
        f"/api/lists/{list_uuid}/columns/{lookup}/options?{wanted}"
    )
    assert resp.status_code == 200, resp.text
    assert sorted(o["label"] for o in resp.json()) == ["Alpha", "Gamma"]


async def test_options_on_a_non_lookup_column_is_a_400(
    client, list_uuid, add_column
):
    text = await add_column(list_uuid, name="n", display_name="N", type="text")
    resp = await client.get(f"/api/lists/{list_uuid}/columns/{text}/options")
    assert resp.status_code == 400, resp.text


async def test_options_never_offer_rows_the_caller_cannot_read(
    client, make_client, session_factory, workspace_uuid, list_uuid, add_column
):
    """A picker that offered a hidden row would leak it — and let the caller link
    to it. The endpoint applies the same per-record filter every read path does."""
    target, rows = await _target_with_rows(
        client, workspace_uuid, add_column, ["Public", "Secret"]
    )
    lookup = await add_column(
        list_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_list_uuid": target},
    )

    # Take the secret row out of the list's permission umbrella, then grant the
    # other user view over both lists but not over that row.
    secret = rows["Secret"]
    await client.patch(
        f"/api/lists/{target}/items/{secret}", json={"permissions_inherit": False}
    )

    async with session_factory() as db:
        other = await UserService.create(
            db, UserCreate(username="nosy", email="nosy@example.com", password="pw")
        )
        other_uuid = str(other.uuid)

    for list_ in (list_uuid, target):
        granted = await client.post(  # the owner shares both lists
            "/api/permissions/grants",
            json={
                "resource_type": "list",
                "resource_uuid": list_,
                "user_uuid": other_uuid,
                "actions": ["view"],
            },
        )
        assert granted.status_code == 201, granted.text

    async with make_client(other) as nosy:
        resp = await nosy.get(f"/api/lists/{list_uuid}/columns/{lookup}/options")
        assert resp.status_code == 200, resp.text
        assert [o["label"] for o in resp.json()] == ["Public"]

        # Nor by asking for it directly.
        direct = await nosy.get(
            f"/api/lists/{list_uuid}/columns/{lookup}/options?uuid={secret}"
        )
        assert direct.json() == []


async def test_options_require_view_on_the_target_list(
    client, make_client, session_factory, workspace_uuid, list_uuid, add_column
):
    """Granted the list but not what it points at: no options, rather than a
    window into a list the caller was never given."""
    target, _ = await _target_with_rows(client, workspace_uuid, add_column, ["Alpha"])
    lookup = await add_column(
        list_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_list_uuid": target},
    )

    async with session_factory() as db:
        other = await UserService.create(
            db, UserCreate(username="partial", email="partial@example.com", password="pw")
        )
        other_uuid = str(other.uuid)

    granted = await client.post(  # the list, but deliberately not its lookup target
        "/api/permissions/grants",
        json={
            "resource_type": "list",
            "resource_uuid": list_uuid,
            "user_uuid": other_uuid,
            "actions": ["view"],
        },
    )
    assert granted.status_code == 201, granted.text

    async with make_client(other) as partial:
        resp = await partial.get(f"/api/lists/{list_uuid}/columns/{lookup}/options")
        assert resp.status_code == 403, resp.text
