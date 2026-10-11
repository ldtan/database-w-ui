"""`GET /tables/{table}/columns/{column}/options` — the lookup picker's backend.

The picker used to fetch a whole target table and filter it in the browser, which
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
    """A "Schools" table labelled by its primary column, plus a lookup at it."""
    target = (
        await client.post(
            "/api/tables/",
            json={
                "name": "schools",
                "display_name": "Schools",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    label_col = await add_column(target, name="name", display_name="Name", type="text")
    await client.patch(f"/api/tables/{target}", json={"primary_column_uuid": label_col})

    rows = {}
    for name in names:
        created = await client.post(
            f"/api/tables/{target}/records/", json={"data": {label_col: name}}
        )
        rows[name] = created.json()["uuid"]
    return target, rows


async def test_options_search_by_label(client, workspace_uuid, table_uuid, add_column):
    target, rows = await _target_with_rows(
        client, workspace_uuid, add_column, ["Springfield High", "Shelbyville High", "Ogdenville"]
    )
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target},
    )

    resp = await client.get(f"/api/tables/{table_uuid}/columns/{lookup}/options?q=high")
    assert resp.status_code == 200, resp.text
    assert sorted(o["label"] for o in resp.json()) == [
        "Shelbyville High",
        "Springfield High",
    ]

    # No query: the first page of rows, so an empty picker still has something.
    every = (await client.get(f"/api/tables/{table_uuid}/columns/{lookup}/options")).json()
    assert len(every) == 3


async def test_options_resolve_specific_rows(
    client, workspace_uuid, table_uuid, add_column
):
    """What a grid needs: the labels for the uuids its cells already hold."""
    target, rows = await _target_with_rows(
        client, workspace_uuid, add_column, ["Alpha", "Beta", "Gamma"]
    )
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target},
    )

    wanted = f"uuid={rows['Alpha']}&uuid={rows['Gamma']}"
    resp = await client.get(
        f"/api/tables/{table_uuid}/columns/{lookup}/options?{wanted}"
    )
    assert resp.status_code == 200, resp.text
    assert sorted(o["label"] for o in resp.json()) == ["Alpha", "Gamma"]


async def test_options_on_a_non_lookup_column_is_a_400(
    client, table_uuid, add_column
):
    text = await add_column(table_uuid, name="n", display_name="N", type="text")
    resp = await client.get(f"/api/tables/{table_uuid}/columns/{text}/options")
    assert resp.status_code == 400, resp.text


async def test_options_never_offer_rows_the_caller_cannot_read(
    client, make_client, session_factory, workspace_uuid, table_uuid, add_column
):
    """A picker that offered a hidden row would leak it — and let the caller link
    to it. The endpoint applies the same per-record filter every read path does."""
    target, rows = await _target_with_rows(
        client, workspace_uuid, add_column, ["Public", "Secret"]
    )
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target},
    )

    # Take the secret row out of the table's permission umbrella, then grant the
    # other user view over both tables but not over that row.
    secret = rows["Secret"]
    await client.patch(
        f"/api/tables/{target}/records/{secret}", json={"permissions_inherit": False}
    )

    async with session_factory() as db:
        other = await UserService.create(
            db, UserCreate(username="nosy", email="nosy@example.com", password="pw")
        )
        other_uuid = str(other.uuid)

    for table_ in (table_uuid, target):
        granted = await client.post(  # the owner shares both tables
            "/api/permissions/grants",
            json={
                "resource_type": "table",
                "resource_uuid": table_,
                "user_uuid": other_uuid,
                "actions": ["view"],
            },
        )
        assert granted.status_code == 201, granted.text

    async with make_client(other) as nosy:
        resp = await nosy.get(f"/api/tables/{table_uuid}/columns/{lookup}/options")
        assert resp.status_code == 200, resp.text
        assert [o["label"] for o in resp.json()] == ["Public"]

        # Nor by asking for it directly.
        direct = await nosy.get(
            f"/api/tables/{table_uuid}/columns/{lookup}/options?uuid={secret}"
        )
        assert direct.json() == []


async def test_options_require_view_on_the_target_table(
    client, make_client, session_factory, workspace_uuid, table_uuid, add_column
):
    """Granted the table but not what it points at: no options, rather than a
    window into a table the caller was never given."""
    target, _ = await _target_with_rows(client, workspace_uuid, add_column, ["Alpha"])
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target},
    )

    async with session_factory() as db:
        other = await UserService.create(
            db, UserCreate(username="partial", email="partial@example.com", password="pw")
        )
        other_uuid = str(other.uuid)

    granted = await client.post(  # the table, but deliberately not its lookup target
        "/api/permissions/grants",
        json={
            "resource_type": "table",
            "resource_uuid": table_uuid,
            "user_uuid": other_uuid,
            "actions": ["view"],
        },
    )
    assert granted.status_code == 201, granted.text

    async with make_client(other) as partial:
        resp = await partial.get(f"/api/tables/{table_uuid}/columns/{lookup}/options")
        assert resp.status_code == 403, resp.text


async def test_labels_fall_back_to_the_first_column(
    client, workspace_uuid, table_uuid, add_column
):
    """Most tables have no primary column — it is only ever set by an explicit
    PATCH, and cleared when that column is deleted. Without a fallback a lookup at
    such a table has no label at all: stub ids in the grid and a search box that
    matches nothing."""
    target = (
        await client.post(
            "/api/tables/",
            json={
                "name": "schools",
                "display_name": "Schools",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    # Deliberately no primary_column_uuid PATCH: the default state of a new table.
    name_col = await add_column(target, name="name", display_name="Name", type="text")
    await client.post(
        f"/api/tables/{target}/records/", json={"data": {name_col: "Springfield High"}}
    )

    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target},
    )

    resp = await client.get(f"/api/tables/{table_uuid}/columns/{lookup}/options?q=spring")
    assert resp.status_code == 200, resp.text
    assert [o["label"] for o in resp.json()] == ["Springfield High"]


async def test_a_deleted_display_column_falls_back_rather_than_blanking(
    client, workspace_uuid, table_uuid, add_column
):
    """Deleting the column a lookup labels its rows by must not blank the labels.

    `display_column_uuid` is the third pointer at a column, alongside the table's
    `primary_column_id` and a nesting pairing — and the only one nothing cleared,
    because it lives in a *different table's* config. It also short-circuited the
    fallbacks, so a dead one did not degrade to the primary or first column, it
    stopped there: every option came back labelled `null`, the export wrote uuids,
    and no search matched anything.

    Deliberately checked at read time rather than scrubbed at delete time, which is
    what the second half of this test is about.
    """
    target, _ = await _target_with_rows(
        client, workspace_uuid, add_column, ["Springfield High"]
    )
    # A second column, explicitly named as the label — not the primary one, so the
    # fallback below is demonstrably doing the work.
    alias = await add_column(target, name="alias", display_name="Alias", type="text")
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target, "display_column_uuid": alias},
    )

    await client.delete(f"/api/tables/{target}/columns/{alias}")

    # Falls through to the primary column rather than answering with nothing.
    resp = await client.get(f"/api/tables/{table_uuid}/columns/{lookup}/options?q=spring")
    assert resp.status_code == 200, resp.text
    assert [o["label"] for o in resp.json()] == ["Springfield High"]

    # And the config still names it, so restoring the column restores the label —
    # which is why this is not scrubbed on delete. A scrub would demote the lookup
    # permanently and a restore could not tell anyone it had.
    restored = await client.post(
        f"/api/tables/{target}/columns/{alias}/restore"
    )
    assert restored.status_code == 200, restored.text
    await client.patch(
        f"/api/tables/{target}/records/{_row_uuid(await client.get(f'/api/tables/{target}/records/'))}",
        json={"data": {alias: "SHS"}},
    )
    again = await client.get(f"/api/tables/{table_uuid}/columns/{lookup}/options?q=shs")
    assert [o["label"] for o in again.json()] == ["SHS"]


def _row_uuid(resp):
    return resp.json()[0]["uuid"]


async def test_export_does_not_leak_labels_from_an_unreadable_target(
    client, make_client, session_factory, workspace_uuid, table_uuid, add_column
):
    """Resolving a label means reading the target table, so it obeys the *target's*
    permissions. Granting view on a table must not hand over the contents of a
    different table it happens to point at — the caller gets uuids instead."""
    target, rows = await _target_with_rows(
        client, workspace_uuid, add_column, ["Springfield High"]
    )
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target},
    )
    await client.post(
        f"/api/tables/{table_uuid}/records/",
        json={"data": {lookup: rows["Springfield High"]}},
    )

    async with session_factory() as db:
        other = await UserService.create(
            db, UserCreate(username="peeker", email="peeker@example.com", password="pw")
        )
        other_uuid = str(other.uuid)

    granted = await client.post(  # the table, but deliberately not its lookup target
        "/api/permissions/grants",
        json={
            "resource_type": "table",
            "resource_uuid": table_uuid,
            "user_uuid": other_uuid,
            "actions": ["view"],
        },
    )
    assert granted.status_code == 201, granted.text

    async with make_client(other) as peeker:
        export = await peeker.post(
            f"/api/tables/{table_uuid}/export", json={"format": "csv"}
        )
        assert export.status_code == 200, export.text
        assert "Springfield High" not in export.text  # the name is not theirs to see
        assert rows["Springfield High"] in export.text  # an opaque uuid, as before

    # The owner, who can read the target, still gets the readable label.
    mine = await client.post(f"/api/tables/{table_uuid}/export", json={"format": "csv"})
    assert "Springfield High" in mine.text


async def test_resolving_more_rows_than_a_search_page_is_not_truncated(
    client, workspace_uuid, table_uuid, add_column
):
    """Asking for specific rows means wanting all of them. A search's page size
    would silently drop the labels past it, and the caller would render row stubs
    with nothing to say anything had been cut."""
    names = [f"School {i:02d}" for i in range(30)]  # > the 25-row suggestion page
    target, rows = await _target_with_rows(client, workspace_uuid, add_column, names)
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target},
    )

    wanted = "&".join(f"uuid={uuid}" for uuid in rows.values())
    resp = await client.get(f"/api/tables/{table_uuid}/columns/{lookup}/options?{wanted}")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 30

    # A search, by contrast, still returns one page of suggestions.
    page = await client.get(f"/api/tables/{table_uuid}/columns/{lookup}/options")
    assert len(page.json()) == 25


async def _own_table(subject_client):
    """A workspace and empty table belonging to whoever `subject_client` is, to
    hang a lookup column on."""
    ws = (
        await subject_client.post(
            "/api/workspaces/", json={"name": "ow", "display_name": "Other WS"}
        )
    ).json()["uuid"]
    table = (
        await subject_client.post(
            "/api/tables/",
            json={"name": "mine", "display_name": "Mine", "workspace_uuid": ws},
        )
    ).json()["uuid"]
    return table


async def test_lookup_can_target_another_of_your_own_workspaces(
    client, workspace_uuid, table_uuid, add_column
):
    """A lookup's target need not share the referencing table's workspace: any
    table you own qualifies, across all of your workspaces."""
    other_ws = (
        await client.post(
            "/api/workspaces/", json={"name": "w2", "display_name": "Workspace 2"}
        )
    ).json()["uuid"]
    target, _ = await _target_with_rows(client, other_ws, add_column, ["Alpha"])

    resp = await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={
            "name": "school",
            "display_name": "School",
            "type": "lookup",
            "config": {"target_table_uuid": target},
        },
    )
    assert resp.status_code == 201, resp.text


async def test_lookup_can_target_a_table_shared_with_you(
    client, make_client, session_factory, workspace_uuid, add_column
):
    """The point of cross-workspace lookups: a table shared to you (view) from
    another user's workspace is a valid target."""
    target, _ = await _target_with_rows(client, workspace_uuid, add_column, ["Alpha"])

    async with session_factory() as db:
        other = await UserService.create(
            db, UserCreate(username="linker", email="linker@example.com", password="pw")
        )
        other_uuid = str(other.uuid)

    granted = await client.post(  # the owner shares the target with `other`
        "/api/permissions/grants",
        json={
            "resource_type": "table",
            "resource_uuid": target,
            "user_uuid": other_uuid,
            "actions": ["view"],
        },
    )
    assert granted.status_code == 201, granted.text

    async with make_client(other) as linker:
        table = await _own_table(linker)
        resp = await linker.post(
            f"/api/tables/{table}/columns/",
            json={
                "name": "school",
                "display_name": "School",
                "type": "lookup",
                "config": {"target_table_uuid": target},
            },
        )
        assert resp.status_code == 201, resp.text


async def test_lookup_target_you_cannot_view_is_refused_like_it_is_missing(
    client, make_client, session_factory, workspace_uuid, add_column
):
    """You cannot point a lookup at a table you were never given. The refusal is
    indistinguishable from the table not existing, so the field can't be used to
    probe for tables a stranger holds."""
    target, _ = await _target_with_rows(client, workspace_uuid, add_column, ["Alpha"])
    # Deliberately not shared with `other`.

    async with session_factory() as db:
        other = await UserService.create(
            db,
            UserCreate(username="outsider", email="outsider@example.com", password="pw"),
        )

    async with make_client(other) as outsider:
        table = await _own_table(outsider)
        resp = await outsider.post(
            f"/api/tables/{table}/columns/",
            json={
                "name": "school",
                "display_name": "School",
                "type": "lookup",
                "config": {"target_table_uuid": target},
            },
        )
        assert resp.status_code == 400, resp.text
        assert "look up" in resp.json()["detail"].lower()


async def test_search_by_lookup_label_obeys_the_target_tables_permissions(
    client, make_client, session_factory, workspace_uuid, table_uuid, add_column
):
    """Searching a linked row's label means reading the target table. A caller who
    cannot read it gets no match — otherwise search becomes a way to probe the
    contents of a table you were never given."""
    target, rows = await _target_with_rows(
        client, workspace_uuid, add_column, ["Springfield High"]
    )
    name = await add_column(table_uuid, name="name", display_name="Name", type="text")
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target},
    )
    await client.post(
        f"/api/tables/{table_uuid}/records/",
        json={"data": {name: "Ann", lookup: rows["Springfield High"]}},
    )

    async with session_factory() as db:
        other = await UserService.create(
            db, UserCreate(username="prober", email="prober@example.com", password="pw")
        )
        other_uuid = str(other.uuid)

    # Granted the table, but deliberately not the table its lookup points at.
    granted = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "table",
            "resource_uuid": table_uuid,
            "user_uuid": other_uuid,
            "actions": ["view"],
        },
    )
    assert granted.status_code == 201, granted.text

    async with make_client(other) as prober:
        # The record is visible...
        assert len((await prober.get(f"/api/tables/{table_uuid}/records/")).json()) == 1
        # ...but its school's name is not theirs to search by.
        hidden = await prober.get(f"/api/tables/{table_uuid}/records/?q=springfield")
        assert hidden.json() == []
        # The owner, who can read the target, finds it.
        mine = await client.get(f"/api/tables/{table_uuid}/records/?q=springfield")
        assert len(mine.json()) == 1
