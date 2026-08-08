"""Who may write which half of a merged pair.

Permissions follow each list separately — that is the whole rule, and everything here
is a consequence of it. Holding `edit` on a row of this list says nothing about the
record it is paired with, which lives in another list with its own grants and may even
be shared on its own.

The load-bearing test is `test_lookup_on_the_target_is_not_enough_to_write_through_it`.
`Action.lookup` exists so a reference list can resolve other people's row labels while
staying shut; a merged form reads and writes *arbitrary* columns of that list. If
`lookup` were accepted here, the narrowest action in the permission model would become
full read/write access to somebody else's data.
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


async def _grant(client, resource_uuid, user_uuid, actions, resource_type="list"):
    resp = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": resource_type,
            "resource_uuid": resource_uuid,
            "user_uuid": user_uuid,
            "actions": actions,
        },
    )
    assert resp.status_code == 201, resp.text


@pytest_asyncio.fixture
async def setup(client, workspace_uuid, list_uuid, add_column):
    """Owner's two lists, a merged form over them, and one existing linked record."""
    people = (
        await client.post(
            "/api/lists/",
            json={
                "name": "people",
                "display_name": "People",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    name_col = await add_column(people, name="fullname", display_name="Name", type="text")
    await client.patch(f"/api/lists/{people}", json={"primary_column_uuid": name_col})

    status_col = await add_column(
        list_uuid, name="status", display_name="Status", type="text"
    )
    link = await add_column(
        list_uuid,
        name="person",
        display_name="Person",
        type="lookup",
        config={"target_list_uuid": people},
    )
    form = (
        await client.post(
            f"/api/lists/{list_uuid}/forms/",
            json={
                "name": "merged",
                "display_name": "Merged",
                "type": "create",
                "config": {
                    "fields": [],
                    "merge": {
                        "link_column_uuid": link,
                        "fields": [
                            {
                                "column_uuid": name_col,
                                "included": True,
                                "enabled": True,
                                "order": 0,
                            }
                        ],
                        "allow_shared": True,
                    },
                },
            },
        )
    ).json()["uuid"]
    existing = (
        await client.post(
            f"/api/lists/{people}/items/", json={"data": {name_col: "Ana"}}
        )
    ).json()["uuid"]

    return {
        "people": people,
        "name": name_col,
        "status": status_col,
        "link": link,
        "form": form,
        "existing": existing,
    }


async def test_lookup_on_the_target_is_not_enough_to_write_through_it(
    client, make_client, session_factory, list_uuid, setup
):
    """The security rule. `lookup` shares row *labels*, not the list."""
    other, other_uuid = await _user(session_factory, "peeker")
    await _grant(client, list_uuid, other_uuid, ["create", "edit"])
    await _grant(client, setup["people"], other_uuid, ["lookup"])

    async with make_client(other) as peeker:
        resp = await peeker.post(
            f"/api/lists/{list_uuid}/items/merged",
            json={
                "form_uuid": setup["form"],
                "data": {setup["status"]: "active"},
                "linked_data": {setup["name"]: "Sneaky"},
            },
        )
        assert resp.status_code == 400, resp.text
        # Refused as if the target were missing, so this cannot probe for it.
        assert "missing target list" in resp.text

    # And nothing was written into the list they could not read.
    people_rows = (await client.get(f"/api/lists/{setup['people']}/items/")).json()
    assert [r["data"][setup["name"]] for r in people_rows] == ["Ana"]


async def test_view_on_the_target_still_cannot_create_in_it(
    client, make_client, session_factory, list_uuid, setup
):
    """`view` gets the fields onto the screen; minting a record needs `create`."""
    other, other_uuid = await _user(session_factory, "reader")
    await _grant(client, list_uuid, other_uuid, ["create", "edit"])
    await _grant(client, setup["people"], other_uuid, ["view"])

    async with make_client(other) as reader:
        resp = await reader.post(
            f"/api/lists/{list_uuid}/items/merged",
            json={
                "form_uuid": setup["form"],
                "data": {setup["status"]: "active"},
                "linked_data": {setup["name"]: "New person"},
            },
        )
        assert resp.status_code == 403, resp.text


async def test_view_on_the_target_is_enough_to_link_an_existing_record(
    client, make_client, session_factory, list_uuid, setup
):
    """Pointing at a record is not writing to it, so `view` suffices."""
    other, other_uuid = await _user(session_factory, "linker")
    await _grant(client, list_uuid, other_uuid, ["create", "edit"])
    await _grant(client, setup["people"], other_uuid, ["view"])

    async with make_client(other) as linker:
        resp = await linker.post(
            f"/api/lists/{list_uuid}/items/merged",
            json={
                "form_uuid": setup["form"],
                "data": {setup["status"]: "active"},
                "linked_uuid": setup["existing"],
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["linked_item"]["data"][setup["name"]] == "Ana"


async def test_writing_a_picked_record_needs_edit_on_that_record(
    client, make_client, session_factory, list_uuid, setup
):
    """`update_linked` raises the bar from `view` to `edit`, and per **item** — a
    record can be shared on its own, so list-level access does not settle it."""
    other, other_uuid = await _user(session_factory, "writer")
    await _grant(client, list_uuid, other_uuid, ["create", "edit"])
    await _grant(client, setup["people"], other_uuid, ["view"])

    async with make_client(other) as writer:
        resp = await writer.post(
            f"/api/lists/{list_uuid}/items/merged",
            json={
                "form_uuid": setup["form"],
                "data": {setup["status"]: "active"},
                "linked_uuid": setup["existing"],
                "linked_data": {setup["name"]: "Renamed"},
                "update_linked": True,
            },
        )
        assert resp.status_code == 403, resp.text

    # The record they could not write is unchanged.
    row = (
        await client.get(f"/api/lists/{setup['people']}/items/{setup['existing']}")
    ).json()
    assert row["data"][setup["name"]] == "Ana"


async def test_edit_on_the_target_permits_writing_through(
    client, make_client, session_factory, list_uuid, setup
):
    other, other_uuid = await _user(session_factory, "editor")
    await _grant(client, list_uuid, other_uuid, ["create", "edit"])
    await _grant(client, setup["people"], other_uuid, ["edit"])

    async with make_client(other) as editor:
        resp = await editor.post(
            f"/api/lists/{list_uuid}/items/merged",
            json={
                "form_uuid": setup["form"],
                "data": {setup["status"]: "active"},
                "linked_uuid": setup["existing"],
                "linked_data": {setup["name"]: "Renamed"},
                "update_linked": True,
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["linked_item"]["data"][setup["name"]] == "Renamed"


async def test_a_merged_update_needs_edit_on_the_linked_record_too(
    client, make_client, session_factory, list_uuid, setup
):
    other, other_uuid = await _user(session_factory, "halfeditor")
    await _grant(client, list_uuid, other_uuid, ["create", "edit"])
    await _grant(client, setup["people"], other_uuid, ["view"])

    row = (
        await client.post(
            f"/api/lists/{list_uuid}/items/merged",
            json={
                "form_uuid": setup["form"],
                "data": {setup["status"]: "active"},
                "linked_uuid": setup["existing"],
            },
        )
    ).json()["item"]["uuid"]

    async with make_client(other) as halfeditor:
        # This side alone is fine.
        ok = await halfeditor.patch(
            f"/api/lists/{list_uuid}/items/{row}/merged",
            json={"form_uuid": setup["form"], "data": {setup["status"]: "dropped"}},
        )
        assert ok.status_code == 200, ok.text

        # Reaching through to the other one is not.
        refused = await halfeditor.patch(
            f"/api/lists/{list_uuid}/items/{row}/merged",
            json={
                "form_uuid": setup["form"],
                "data": {setup["status"]: "again"},
                "linked_data": {setup["name"]: "Renamed"},
            },
        )
        assert refused.status_code == 403, refused.text

    # Neither side of the refused request was written.
    assert (
        (await client.get(f"/api/lists/{list_uuid}/items/{row}")).json()["data"][
            setup["status"]
        ]
        == "dropped"
    )
    assert (
        (
            await client.get(f"/api/lists/{setup['people']}/items/{setup['existing']}")
        ).json()["data"][setup["name"]]
        == "Ana"
    )


async def test_an_unreadable_linked_record_degrades_rather_than_refusing(
    client, make_client, session_factory, list_uuid, setup
):
    """A row whose pair is individually restricted is still a row this caller may
    read. `linked_item` comes back null — the degradation `LookupCell` already makes
    on a 403 — rather than the whole request failing."""
    row = (
        await client.post(
            f"/api/lists/{list_uuid}/items/merged",
            json={
                "form_uuid": setup["form"],
                "data": {setup["status"]: "active"},
                "linked_uuid": setup["existing"],
            },
        )
    ).json()["item"]["uuid"]

    # Sever the linked record from its list's grants, then share only this list.
    severed = await client.patch(
        f"/api/lists/{setup['people']}/items/{setup['existing']}",
        json={"permissions_inherit": False},
    )
    assert severed.status_code == 200, severed.text

    other, other_uuid = await _user(session_factory, "outsider")
    await _grant(client, list_uuid, other_uuid, ["create", "edit"])
    await _grant(client, setup["people"], other_uuid, ["view"])

    async with make_client(other) as outsider:
        resp = await outsider.patch(
            f"/api/lists/{list_uuid}/items/{row}/merged",
            json={"form_uuid": setup["form"], "data": {setup["status"]: "dropped"}},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"]["data"][setup["status"]] == "dropped"
        assert resp.json()["linked_item"] is None
