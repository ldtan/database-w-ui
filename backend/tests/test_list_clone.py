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
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.infrastructure import database as db_module
from src.lists import services as list_services
from src.lists.items.models import ListItem
from src.lists.models import List


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


async def _rows(session_factory, list_uuid):
    """A list's records straight from the table, in id order.

    The API is the wrong instrument for the copy tests below: `_shape_for_read`
    recomputes and normalises what it hands back, so a stray key or a null `data`
    would be tidied away before the assertion could see it.
    """
    async with session_factory() as db:
        lst = (
            await db.execute(select(List).where(List.uuid == UUID(list_uuid)))
        ).scalar_one()
        return (
            await db.execute(
                select(ListItem)
                .where(ListItem.list_id == lst.id, ListItem.deleted_at.is_(None))
                .order_by(ListItem.id)
            )
        ).scalars().all()


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


async def test_clone_remaps_a_fields_condition(make_client, session_factory):
    """A field's `condition` names columns too, and they are remapped like any other.

    Left out, the clone's conditions go on testing the *source* list's columns: they
    resolve to nothing there, so every conditional field shows unconditionally — a
    quiet failure, which is why both uuids below are ones the map actually mentions.
    """
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        status_uuid = await _add_column(client, list_uuid, "status")
        reason_uuid = await _add_column(client, list_uuid, "reason")
        f = await client.post(
            f"/api/lists/{list_uuid}/forms/",
            json={
                "name": "myform",
                "display_name": "My Form",
                "config": {
                    "fields": [
                        {"column_uuid": status_uuid, "order": 0},
                        {
                            "column_uuid": reason_uuid,
                            "order": 1,
                            "condition": {
                                "when": {
                                    "conjunction": "and",
                                    "conditions": [
                                        {
                                            "column_uuid": status_uuid,
                                            "operator": "is",
                                            "value": "Rejected",
                                        }
                                    ],
                                },
                                "effect": "hide",
                            },
                        },
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
        clone_status = _column_named(clone_cols, "status")["uuid"]
        clone_reason = _column_named(clone_cols, "reason")["uuid"]
        assert clone_status != status_uuid  # the map really does move it

        forms = (await client.get(f"/api/lists/{clone_uuid}/forms/")).json()
        myform = next(f for f in forms if f["name"] == "myform")
        field = next(
            f for f in myform["config"]["fields"] if f["column_uuid"] == clone_reason
        )
        assert field["condition"]["when"]["conditions"][0]["column_uuid"] == clone_status


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


async def test_clone_respects_item_permissions(make_client, session_factory):
    """A clone is a *read* of the records, so it honours the per-record filter.

    The sibling of `test_http_export_respects_item_permissions`. A record whose
    inheritance is broken and which was never granted to the caller is out of
    reach of their list-level access — and a clone must not be the way around
    that, because the copy lands in a list where it inherits and the caller can
    then read it at leisure. Bob holds `configure` on the workspace, which is
    what the clone route requires and which confers `view`; that is precisely the
    access this used to convert into a copy of a record he could never open.
    """
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        bob = await _make_user(db, "bob")
        bob_uuid = str(bob.uuid)

    async with make_client(owner) as client:
        ws_uuid, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        col_uuid = await _add_column(client, list_uuid, "title")
        for title in ("open", "secret"):
            resp = await client.post(
                f"/api/lists/{list_uuid}/items/", json={"data": {col_uuid: title}}
            )
            assert resp.status_code == 201, resp.text

        grant = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "workspace",
                "resource_uuid": ws_uuid,
                "user_uuid": bob_uuid,
                "actions": ["configure"],
            },
        )
        assert grant.status_code == 201, grant.text

    # The second record goes "unique" with no grant of its own, so neither the
    # workspace grant nor the list access it confers reaches it any more.
    secret_id = next(
        r.id for r in await _rows(session_factory, list_uuid)
        if r.data.get(col_uuid) == "secret"
    )
    async with session_factory() as db:
        row = (
            await db.execute(select(ListItem).where(ListItem.id == secret_id))
        ).scalar_one()
        row.permissions_inherit = False
        await db.commit()

    async with make_client(bob) as client:
        # The grid already hides it; the clone must agree.
        assert len((await client.get(f"/api/lists/{list_uuid}/items/")).json()) == 1
        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": True}
        )
        assert resp.status_code == 201, resp.text
        clone_uuid = resp.json()["uuid"]

    # Read the table directly: the copy is what matters, not what bob can see of it.
    copied = await _rows(session_factory, clone_uuid)
    assert [next(iter(r.data.values())) for r in copied] == ["open"]

    # The owner, who can see both, still clones both.
    async with make_client(owner) as client:
        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": True}
        )
        assert resp.status_code == 201, resp.text
        full = await _rows(session_factory, resp.json()["uuid"])
    assert sorted(next(iter(r.data.values())) for r in full) == ["open", "secret"]


# --------------------------------------------------------------------------
# Copying the records.
#
# The copy is keyset-paged and performed by one INSERT ... SELECT per batch, so that
# a large list is never held in memory. These shrink CLONE_CHUNK to two, the way
# test_maintenance_batching does, so the loop runs several times over a handful of
# rows: an unterminated loop hangs, and a single-pass one leaves records behind.


@pytest.fixture
def tiny_chunks(monkeypatch):
    monkeypatch.setattr(list_services, "CLONE_CHUNK", 2)


async def test_clone_copies_every_record_across_batches(
    make_client, session_factory, tiny_chunks
):
    """Five records over batches of two — the last batch is the one a broken loop
    drops, and the partial batch at the end is where an off-by-one shows."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        src_col_uuid = await _add_column(client, list_uuid, "title")
        for n in range(5):
            resp = await client.post(
                f"/api/lists/{list_uuid}/items/",
                json={"data": {src_col_uuid: f"row-{n}"}},
            )
            assert resp.status_code == 201, resp.text

        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": True}
        )
        assert resp.status_code == 201, resp.text
        clone_uuid = resp.json()["uuid"]

        clone_cols = (await client.get(f"/api/lists/{clone_uuid}/columns/")).json()
        clone_col_uuid = _column_named(clone_cols, "title")["uuid"]

    rows = await _rows(session_factory, clone_uuid)
    # Every record, exactly once, still in the source's order.
    assert [row.data[clone_col_uuid] for row in rows] == [f"row-{n}" for n in range(5)]
    # New identities, not the source's, and one apiece.
    assert len({row.uuid for row in rows}) == 5


async def test_clone_keeps_data_keys_the_map_does_not_mention(
    make_client, session_factory, tiny_chunks
):
    """A key with no column behind it — left by a deleted column, or by a conversion's
    hidden scratch column, which `in_shape` keeps out of the clone's schema — is copied
    across untouched. That is what `col_map.get(key, key)` did, and what the SQL
    `COALESCE(map ->> key, key)` has to keep doing."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        src_col_uuid = await _add_column(client, list_uuid, "title")
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {src_col_uuid: "hello"}}
        )

        # Put the orphan there directly: no endpoint leaves one behind on purpose.
        async with session_factory() as db:
            item = (await _rows(session_factory, list_uuid))[0]
            merged = (await db.get(ListItem, item.id))
            merged.data = {**merged.data, "no-such-column": "kept"}
            await db.commit()

        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": True}
        )
        assert resp.status_code == 201, resp.text
        clone_uuid = resp.json()["uuid"]

        clone_cols = (await client.get(f"/api/lists/{clone_uuid}/columns/")).json()
        clone_col_uuid = _column_named(clone_cols, "title")["uuid"]

    (row,) = await _rows(session_factory, clone_uuid)
    assert row.data[clone_col_uuid] == "hello"  # the mapped key moved
    assert row.data["no-such-column"] == "kept"  # the unmapped one stayed
    assert src_col_uuid not in row.data


async def test_clone_copies_a_record_with_no_cells(
    make_client, session_factory, tiny_chunks
):
    """An empty record clones as an empty record, not as NULL. `jsonb_object_agg` over
    no rows answers NULL, and `data` does not accept it — so this is the row that
    proves the COALESCE around it is doing something."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        await _add_column(client, list_uuid, "title")
        resp = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
        assert resp.status_code == 201, resp.text

        async with session_factory() as db:
            item = (await _rows(session_factory, list_uuid))[0]
            blank = await db.get(ListItem, item.id)
            blank.data = {}
            await db.commit()

        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": True}
        )
        assert resp.status_code == 201, resp.text
        clone_uuid = resp.json()["uuid"]

    (row,) = await _rows(session_factory, clone_uuid)
    assert row.data == {}


async def test_clone_leaves_binned_records_behind(
    make_client, session_factory, tiny_chunks
):
    """A record in the source's bin is not part of the list, so it is not part of a
    copy of the list."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        src_col_uuid = await _add_column(client, list_uuid, "title")
        kept = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {src_col_uuid: "kept"}}
        )
        binned = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {src_col_uuid: "binned"}}
        )
        assert kept.status_code == 201 and binned.status_code == 201
        gone = await client.delete(
            f"/api/lists/{list_uuid}/items/{binned.json()['uuid']}"
        )
        assert gone.status_code in (200, 204), gone.text

        resp = await client.post(
            f"/api/lists/{list_uuid}/clone", json={"with_records": True}
        )
        assert resp.status_code == 201, resp.text
        clone_uuid = resp.json()["uuid"]

        clone_cols = (await client.get(f"/api/lists/{clone_uuid}/columns/")).json()
        clone_col_uuid = _column_named(clone_cols, "title")["uuid"]

    rows = await _rows(session_factory, clone_uuid)
    assert [row.data[clone_col_uuid] for row in rows] == ["kept"]


async def test_a_failed_clone_leaves_no_records_behind(
    make_client, session_factory, monkeypatch, tiny_chunks
):
    """A clone that dies partway takes its records with it.

    This is the property the batching is arranged around: the batches deliberately do
    *not* commit as they go, unlike the maintenance loops that share their shape, so
    the copy is all-or-nothing however far it got. Forced here by making every copied
    record ask for the same uuid, which the unique index refuses partway through — a
    stand-in for any database error mid-copy.

    The failure propagates rather than becoming the ConflictError below it: that
    handler answers for a name collision, and reporting a uuid clash as "a list with
    this name already exists" would be a lie. What the copy relies on is the
    transaction, not the handler — nothing was committed, so nothing survives.
    """
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        _, lst = await _seed_list(client, "tasks")
        list_uuid = lst["uuid"]
        src_col_uuid = await _add_column(client, list_uuid, "title")
        for n in range(5):
            await client.post(
                f"/api/lists/{list_uuid}/items/",
                json={"data": {src_col_uuid: f"row-{n}"}},
            )
        before = len(await _rows(session_factory, list_uuid))

        # Unique for the first batch, then a repeat — so the second batch is what
        # fails, and the first batch's rows are the ones that have to disappear.
        real_uuid7 = list_services.uuid7
        minted = []

        def _colliding():
            minted.append(real_uuid7() if len(minted) < 2 else minted[0])
            return minted[-1]

        monkeypatch.setattr(list_services, "uuid7", _colliding)

        with pytest.raises(IntegrityError):
            await client.post(
                f"/api/lists/{list_uuid}/clone", json={"with_records": True}
            )

    # The source is untouched and the half-built clone left nothing standing.
    async with session_factory() as db:
        total = len((await db.execute(select(ListItem))).scalars().all())
        names = sorted(
            (await db.execute(select(List.name))).scalars().all()
        )
    assert total == before == 5
    assert names == ["tasks"]
