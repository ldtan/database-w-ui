"""Cloning a list.

A clone's columns are new rows with new uuids, so the load-bearing rule is that
every uuid reference is rewritten through an old→new column-uuid map: the record
``data`` keys, the primary column, and the column-uuid references inside view and
form configs. Get it wrong and the clone's records render blank. Structure is
always copied; records only when asked. A self-referencing lookup deliberately
keeps pointing at the *original* list. Grants are never copied — the clone starts
private, like any freshly created list.

DISABLE_AUTH resolves the caller to the first user, so — like test_permissions —
these drive HTTP as a chosen user via a get_current_user override.
"""
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.infrastructure import database as db_module


async def _make_user(db, name: str):
    return await UserService.create(
        db, UserCreate(username=name, email=f"{name}@example.com", password="pw")
    )


@pytest_asyncio.fixture
def make_client(engine, session_factory):
    """An AsyncClient that authenticates as a specific user (see test_permissions)."""

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


async def _seed_list(client, name: str = "tasks"):
    """A fresh workspace + list owned by the client's user; returns (ws_uuid, list)."""
    ws = await client.post(
        "/api/workspaces/", json={"name": f"{name}_ws", "display_name": "WS"}
    )
    assert ws.status_code == 201, ws.text
    ws_uuid = ws.json()["uuid"]
    lst = await client.post(
        "/api/lists/",
        json={"name": name, "display_name": name.title(), "workspace_uuid": ws_uuid},
    )
    assert lst.status_code == 201, lst.text
    return ws_uuid, lst.json()


async def _add_column(client, list_uuid, name, type="text", config=None):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": name,
            "display_name": name.title(),
            "type": type,
            "config": config or {},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


def _column_named(columns, name):
    return next(c for c in columns if c["name"] == name)


# --------------------------------------------------------------------------


async def test_clone_structure_only_omits_records(make_client, session_factory):
    """Default clone copies columns/views/forms but no records, and does not
    re-provision defaults on top of the copied ones."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        col_uuid = await _add_column(client, list_uuid, "title")
        rec = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {col_uuid: "hello"}}
        )
        assert rec.status_code == 201, rec.text

        src_cols = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
        src_views = (await client.get(f"/api/lists/{list_uuid}/views/")).json()
        src_forms = (await client.get(f"/api/lists/{list_uuid}/forms/")).json()

        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": False}
        )
        assert resp.status_code == 201, resp.text
        clone = resp.json()
        assert clone["uuid"] != list_uuid
        assert clone["name"] == "tasks_copy"
        assert clone["display_name"] == "Tasks (copy)"
        clone_uuid = clone["uuid"]

        clone_cols = (await client.get(f"/api/lists/{clone_uuid}/columns/")).json()
        # Same columns by name, and no duplicates from a stray _provision_defaults.
        assert len(clone_cols) == len(src_cols)
        assert {c["name"] for c in clone_cols} == {c["name"] for c in src_cols}

        clone_items = (await client.get(f"/api/lists/{clone_uuid}/items/")).json()
        assert clone_items == []

        clone_views = (await client.get(f"/api/lists/{clone_uuid}/views/")).json()
        clone_forms = (await client.get(f"/api/lists/{clone_uuid}/forms/")).json()
        assert len(clone_views) == len(src_views)
        assert len(clone_forms) == len(src_forms)


async def test_clone_with_records_remaps_data_keys(make_client, session_factory):
    """The crux: a copied record's value must land under the *clone's* column uuid,
    not the source's — otherwise it keys against a column that doesn't exist and the
    cell renders blank."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        src_col_uuid = await _add_column(client, list_uuid, "title")
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {src_col_uuid: "hello"}}
        )

        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": True}
        )
        assert resp.status_code == 201, resp.text
        clone_uuid = resp.json()["uuid"]

        clone_cols = (await client.get(f"/api/lists/{clone_uuid}/columns/")).json()
        clone_col_uuid = _column_named(clone_cols, "title")["uuid"]
        assert clone_col_uuid != src_col_uuid  # a genuinely new column

        items = (await client.get(f"/api/lists/{clone_uuid}/items/")).json()
        assert len(items) == 1
        data = items[0]["data"]
        assert data.get(clone_col_uuid) == "hello"  # remapped, renders
        assert src_col_uuid not in data  # the stale key is gone


async def test_clone_remaps_view_config(make_client, session_factory):
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        src_col_uuid = await _add_column(client, list_uuid, "title")
        v = await client.post(
            f"/api/lists/{list_uuid}/views/",
            json={
                "name": "myview",
                "display_name": "My View",
                "config": {
                    "sorts": [{"column_uuid": src_col_uuid, "direction": "asc"}],
                    "hidden_columns": [src_col_uuid],
                    "column_order": [src_col_uuid],
                },
            },
        )
        assert v.status_code == 201, v.text

        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": False}
        )
        clone_uuid = resp.json()["uuid"]
        clone_cols = (await client.get(f"/api/lists/{clone_uuid}/columns/")).json()
        clone_col_uuid = _column_named(clone_cols, "title")["uuid"]

        views = (await client.get(f"/api/lists/{clone_uuid}/views/")).json()
        myview = next(v for v in views if v["name"] == "myview")
        cfg = myview["config"]
        assert cfg["sorts"][0]["column_uuid"] == clone_col_uuid
        assert cfg["hidden_columns"] == [clone_col_uuid]
        assert cfg["column_order"] == [clone_col_uuid]


async def test_clone_remaps_form_config(make_client, session_factory):
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        src_col_uuid = await _add_column(client, list_uuid, "title")
        f = await client.post(
            f"/api/lists/{list_uuid}/forms/",
            json={
                "name": "myform",
                "display_name": "My Form",
                "config": {
                    "fields": [
                        {
                            "column_uuid": src_col_uuid,
                            "included": True,
                            "enabled": True,
                            "order": 0,
                        }
                    ]
                },
            },
        )
        assert f.status_code == 201, f.text

        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": False}
        )
        clone_uuid = resp.json()["uuid"]
        clone_cols = (await client.get(f"/api/lists/{clone_uuid}/columns/")).json()
        clone_col_uuid = _column_named(clone_cols, "title")["uuid"]

        forms = (await client.get(f"/api/lists/{clone_uuid}/forms/")).json()
        myform = next(f for f in forms if f["name"] == "myform")
        assert myform["config"]["fields"][0]["column_uuid"] == clone_col_uuid


async def test_clone_remaps_primary_column(make_client, session_factory):
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        src_col_uuid = await _add_column(client, list_uuid, "title")
        patched = await client.patch(
            f"/api/lists/{list_uuid}", json={"primary_column_uuid": src_col_uuid}
        )
        assert patched.status_code == 200, patched.text

        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": False}
        )
        clone = resp.json()
        clone_uuid = clone["uuid"]
        clone_cols = (await client.get(f"/api/lists/{clone_uuid}/columns/")).json()
        clone_col_uuid = _column_named(clone_cols, "title")["uuid"]
        # The clone's primary field points at the clone's column, not the source's.
        assert clone["primary_column"] is not None
        assert clone["primary_column"]["uuid"] == clone_col_uuid


async def test_self_lookup_keeps_pointing_at_original(make_client, session_factory):
    """A lookup targeting the list being cloned is left pointing at the *original*
    list (config and cells), per the design decision — no row-uuid remap."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        await _add_column(
            client, list_uuid, "rel", type="lookup",
            config={"target_list_uuid": list_uuid},
        )

        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": False}
        )
        clone_uuid = resp.json()["uuid"]
        clone_cols = (await client.get(f"/api/lists/{clone_uuid}/columns/")).json()
        rel = _column_named(clone_cols, "rel")
        # Still the source list's uuid — the clone does not self-reference.
        assert rel["config"]["target_list_uuid"] == list_uuid


async def test_clone_name_auto_suffixes(make_client, session_factory):
    """Cloning twice yields tasks_copy then tasks_copy_2 — name is unique per
    workspace and a clone lands in the same workspace."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]

        first = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": False}
        )
        assert first.status_code == 201, first.text
        assert first.json()["name"] == "tasks_copy"

        second = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": False}
        )
        assert second.status_code == 201, second.text
        assert second.json()["name"] == "tasks_copy_2"


async def test_clone_does_not_copy_grants(make_client, session_factory):
    """The clone is a new resource the cloner owns; it starts private even when the
    source was shared."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        bob = await _make_user(db, "bob")
        bob_uuid = str(bob.uuid)

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        grant = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "list",
                "resource_uuid": list_uuid,
                "user_uuid": bob_uuid,
                "actions": ["view"],
            },
        )
        assert grant.status_code == 201, grant.text
        clone_uuid = (
            await client.post(
                f"/api/lists/{list_uuid}/clone", json={"with_records": False}
            )
        ).json()["uuid"]

    # Bob could view the source, but the clone carried none of that.
    async with make_client(bob) as client:
        assert (await client.get(f"/api/lists/{clone_uuid}")).status_code == 403


async def test_clone_requires_configure_on_workspace(make_client, session_factory):
    """Reading the source needs only 'view', but writing the copy into the workspace
    needs 'configure' there — a viewer who does not own the workspace cannot clone."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        bob = await _make_user(db, "bob")
        bob_uuid = str(bob.uuid)

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        grant = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "list",
                "resource_uuid": list_uuid,
                "user_uuid": bob_uuid,
                "actions": ["view"],
            },
        )
        assert grant.status_code == 201, grant.text

    async with make_client(bob) as client:
        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": False}
        )
        assert resp.status_code == 403, resp.text


# -- copying the records without holding them all ---------------------------
#
# These shrink `_CLONE_CHUNK` rather than creating thousands of records, the same
# way test_purge_record_draining.py does: what is being pinned is that the walk
# runs more than once and still finishes with everything, not the number it runs
# at. A single-chunk clone would pass every one of these by doing nothing.


async def test_clone_copies_every_record_across_several_chunks(
    make_client, session_factory, monkeypatch
):
    from src.lists import services as list_services

    monkeypatch.setattr(list_services, "_CLONE_CHUNK", 2)
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        col = await _add_column(client, list_uuid, "title")
        for i in range(7):  # three full chunks and a short one
            await client.post(
                f"/api/lists/{list_uuid}/items/", json={"data": {col: f"row {i}"}}
            )

        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": True}
        )
        assert resp.status_code == 201, resp.text
        clone_uuid = resp.json()["uuid"]

        clone_cols = (await client.get(f"/api/lists/{clone_uuid}/columns/")).json()
        clone_col = _column_named(clone_cols, "title")["uuid"]
        items = (
            await client.get(f"/api/lists/{clone_uuid}/items/?limit=100")
        ).json()

        assert sorted(i["data"][clone_col] for i in items) == [
            f"row {i}" for i in range(7)
        ]


async def test_the_chunked_walk_does_not_copy_records_from_the_bin(
    make_client, session_factory, monkeypatch
):
    """The keyset walk skips deleted rows, which means it also skips their ids —
    so `after` has to come from the last row *returned*, not from a count."""
    from src.lists import services as list_services

    monkeypatch.setattr(list_services, "_CLONE_CHUNK", 2)
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        col = await _add_column(client, list_uuid, "title")
        uuids = []
        for i in range(6):
            created = await client.post(
                f"/api/lists/{list_uuid}/items/", json={"data": {col: f"row {i}"}}
            )
            uuids.append(created.json()["uuid"])
        # Delete two, one of them the last row, so the final chunk comes back empty
        # of live rows rather than short.
        for i in (1, 5):
            assert (
                await client.delete(f"/api/lists/{list_uuid}/items/{uuids[i]}")
            ).status_code in (200, 204)

        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": True}
        )
        assert resp.status_code == 201, resp.text
        clone_uuid = resp.json()["uuid"]

        clone_cols = (await client.get(f"/api/lists/{clone_uuid}/columns/")).json()
        clone_col = _column_named(clone_cols, "title")["uuid"]
        items = (
            await client.get(f"/api/lists/{clone_uuid}/items/?limit=100")
        ).json()

        assert sorted(i["data"][clone_col] for i in items) == [
            "row 0", "row 2", "row 3", "row 4",
        ]
        # And the clone's bin is empty: a deleted record is not copied as deleted.
        binned = (
            await client.get(f"/api/lists/{clone_uuid}/items/deleted")
        ).json()
        assert binned == []


async def test_a_clone_that_dies_before_the_commit_leaves_nothing_behind(
    make_client, session_factory, monkeypatch
):
    """Records are flushed chunk by chunk but committed once, and this is why.

    Every chunk has reached the database by the time this blows up — three of
    them, at a chunk size of two. None of it survives, because a clone is one
    thing appearing: a run that died would otherwise leave a half-copied list
    sitting under a name its owner cannot reuse.
    """
    from src.lists import services as list_services

    monkeypatch.setattr(list_services, "_CLONE_CHUNK", 2)
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        col = await _add_column(client, list_uuid, "title")
        for i in range(6):
            await client.post(
                f"/api/lists/{list_uuid}/items/", json={"data": {col: f"row {i}"}}
            )

        real = list_services.ListService._clone_records

        async def _die_after_a_chunk(db, source_id, clone_id, col_map):
            await real(db, source_id, clone_id, col_map)
            raise RuntimeError("boom")

        monkeypatch.setattr(
            list_services.ListService,
            "_clone_records",
            staticmethod(_die_after_a_chunk),
        )

        with pytest.raises(RuntimeError):
            await client.post(
                f"/api/lists/{list_uuid}/clone", json={"with_records": True}
            )

    monkeypatch.undo()
    async with make_client(owner) as client:
        lists = (await client.get("/api/lists/")).json()
        # The copy's own row went with its records: the session is closed without
        # a commit, and everything flushed inside it goes back.
        assert [entry["uuid"] for entry in lists] == [list_uuid]
