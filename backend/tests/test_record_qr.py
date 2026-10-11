"""`POST /tables/{uuid}/records/qr` — the JSON a record's QR code carries.

The feature's whole promise is that a code this app generates can be scanned *back* into a
record. That is not a property of the QR encoder — it is a property of the payload being
the same bytes a JSON export writes, which is why `qr_payloads` and `export_rows` share
`json_row`. So the load-bearing test here is not any single field: it is
`test_the_payload_matches_what_the_json_export_writes` plus the round trip through import.
Those two are what would fail if somebody reimplemented the formatter from the screen.
"""
import json
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.core.config import settings
from src.infrastructure import database as db_module
from src.tables.transfer.services import QR_UUID_KEY


@pytest_asyncio.fixture
async def grid(client, table_uuid, add_column):
    """A table with a text column and a choice column, and two records."""
    name = await add_column(
        table_uuid, name="full_name", display_name="Full name", type="text"
    )
    status = await add_column(
        table_uuid,
        name="status",
        display_name="Status",
        type="choice",
        config={"options": ["Active", "Lapsed"]},
    )
    records = []
    for who, state in (("Ana Cruz", "Active"), ("Ben Dizon", "Lapsed")):
        resp = await client.post(
            f"/api/tables/{table_uuid}/records/",
            json={"data": {name: who, status: state}},
        )
        assert resp.status_code == 201, resp.text
        records.append(resp.json()["uuid"])
    return {"name": name, "status": status, "records": records}


async def _qr(client, table_uuid, **body):
    return await client.post(f"/api/tables/{table_uuid}/records/qr", json=body)


# -- the payload -------------------------------------------------------------


async def test_the_payload_is_keyed_by_column_name(client, table_uuid, grid):
    """The same key a CSV/JSON import uses, so one convention covers every way a record
    crosses the app's boundary — and so a code stays writable by a person."""
    resp = await _qr(
        client,
        table_uuid,
        record_uuids=[grid["records"][0]],
        columns=["full_name", "status"],
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()[0]["payload"]
    assert payload["full_name"] == "Ana Cruz"
    assert payload["status"] == "Active"


async def test_the_record_uuid_is_included_under_a_reserved_key(
    client, table_uuid, grid
):
    """`_uuid`, not `uuid`: a column may legally be named `uuid`, and the record's id
    landing on top of that column's value would be undetectable."""
    resp = await _qr(client, table_uuid, record_uuids=[grid["records"][0]], columns=[])
    payload = resp.json()[0]["payload"]
    assert payload == {QR_UUID_KEY: grid["records"][0]}


async def test_the_uuid_can_be_left_out(client, table_uuid, grid):
    resp = await _qr(
        client,
        table_uuid,
        record_uuids=[grid["records"][0]],
        columns=["full_name"],
        include_uuid=False,
    )
    payload = resp.json()[0]["payload"]
    assert QR_UUID_KEY not in payload
    assert payload == {"full_name": "Ana Cruz"}
    # Still matchable to its row, which is why record_uuid is echoed outside the payload.
    assert resp.json()[0]["record_uuid"] == grid["records"][0]


async def test_a_column_named_uuid_is_not_shadowed(client, table_uuid, add_column):
    """The collision the reserved key exists to prevent, pinned."""
    col = await add_column(table_uuid, name="uuid", display_name="Badge no.", type="text")
    created = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: "A-1234"}}
    )
    record = created.json()["uuid"]
    resp = await _qr(client, table_uuid, record_uuids=[record], columns=["uuid"])
    payload = resp.json()[0]["payload"]
    assert payload["uuid"] == "A-1234"  # the column's value, untouched
    assert payload[QR_UUID_KEY] == record  # the record's id, beside it


async def test_an_unknown_column_name_is_ignored(client, table_uuid, grid):
    """A saved selection outlives the columns it names. Renaming or deleting one should
    quietly stop it appearing on the badge, not make every badge un-printable."""
    resp = await _qr(
        client,
        table_uuid,
        record_uuids=[grid["records"][0]],
        columns=["full_name", "was_renamed_away"],
    )
    assert resp.status_code == 200, resp.text
    assert set(resp.json()[0]["payload"]) == {QR_UUID_KEY, "full_name"}


async def test_payloads_come_back_in_the_order_asked_for(client, table_uuid, grid):
    """A printed sheet has to match the selection on screen, and `IN (...)` promises no
    ordering."""
    reversed_uuids = list(reversed(grid["records"]))
    resp = await _qr(
        client, table_uuid, record_uuids=reversed_uuids, columns=["full_name"]
    )
    assert [row["record_uuid"] for row in resp.json()] == reversed_uuids


async def test_an_empty_request_is_an_empty_answer(client, table_uuid, grid):
    resp = await _qr(client, table_uuid, record_uuids=[], columns=["full_name"])
    assert resp.status_code == 200
    assert resp.json() == []


async def test_over_the_cap_is_refused_by_the_schema(client, table_uuid, grid):
    """Published as a `max_length`, so an over-long body is a 422 before any work."""
    import uuid as uuid_module

    too_many = [str(uuid_module.uuid7()) for _ in range(settings.BULK_DELETE_MAX + 1)]
    resp = await _qr(client, table_uuid, record_uuids=too_many, columns=[])
    assert resp.status_code == 422


# -- the property the shared formatter exists for ---------------------------


async def test_the_payload_matches_what_the_json_export_writes(
    client, table_uuid, grid
):
    """**The test this feature rests on.**

    A generated code is meant to be readable by the importer, and it is only *guaranteed*
    to be if the code and the export are the same bytes. `qr_payloads` and `export_rows`
    share `json_row` to make that so; this is what would fail if the payload were ever
    rebuilt from the screen instead — a group's label, for one, is its slug in an export
    and its display name on screen.
    """
    export = await client.post(
        f"/api/tables/{table_uuid}/export",
        json={"format": "json", "columns": ["full_name", "status"]},
    )
    assert export.status_code == 200, export.text
    exported = json.loads(export.text)

    qr = await _qr(
        client,
        table_uuid,
        record_uuids=grid["records"],
        columns=["full_name", "status"],
        include_uuid=False,
    )
    assert [row["payload"] for row in qr.json()] == exported


async def test_a_lookup_cell_carries_the_label_not_the_uuid(
    client, table_uuid, add_column, workspace_uuid
):
    """A code full of uuids is useless to a person and unreadable by the importer."""
    target = await client.post(
        "/api/tables/",
        json={
            "name": "schools",
            "display_name": "Schools",
            "workspace_uuid": workspace_uuid,
        },
    )
    target_uuid = target.json()["uuid"]
    school_name = await add_column(
        target_uuid, name="school_name", display_name="School", type="text"
    )
    await client.patch(
        f"/api/tables/{target_uuid}",
        json={"primary_column_uuid": school_name},
    )
    row = await client.post(
        f"/api/tables/{target_uuid}/records/",
        json={"data": {school_name: "Westbrook Academy"}},
    )
    school = row.json()["uuid"]

    link = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target_uuid},
    )
    created = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {link: school}}
    )
    record = created.json()["uuid"]

    resp = await _qr(client, table_uuid, record_uuids=[record], columns=["school"])
    assert resp.json()[0]["payload"]["school"] == "Westbrook Academy"


async def test_a_calculated_cell_is_computed_rather_than_read(
    client, table_uuid, add_column
):
    """A printed code is as uncorrectable afterwards as a file is, so it must not trust
    the stored copy — which is a query index that can lag a formula edit."""
    qty = await add_column(table_uuid, name="qty", display_name="Qty", type="number")
    await add_column(
        table_uuid,
        name="doubled",
        display_name="Doubled",
        type="calculated",
        config={"formula": "qty * 2"},
    )
    created = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {qty: 21}}
    )
    record = created.json()["uuid"]
    resp = await _qr(client, table_uuid, record_uuids=[record], columns=["doubled"])
    assert resp.json()[0]["payload"]["doubled"] == 42


async def test_a_generated_payload_imports_back_into_an_equal_record(
    client, table_uuid, grid
):
    """The round trip, which is the point of the whole feature: print a badge, scan it,
    get the record back."""
    qr = await _qr(
        client,
        table_uuid,
        record_uuids=[grid["records"][0]],
        columns=["full_name", "status"],
    )
    payload = qr.json()[0]["payload"]

    # `_uuid` goes along deliberately: a real scan carries it, and the importer has to
    # tolerate it the way `qrFill` does rather than choke on a key that is not a column.
    resp = await client.post(
        f"/api/tables/{table_uuid}/import",
        files={"file": ("badge.json", json.dumps([payload]), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 1, resp.json()

    rows = (await client.get(f"/api/tables/{table_uuid}/records/")).json()
    made = [r for r in rows if r["uuid"] not in grid["records"]]
    assert len(made) == 1
    source = next(r for r in rows if r["uuid"] == grid["records"][0])
    assert made[0]["data"][grid["name"]] == source["data"][grid["name"]]
    assert made[0]["data"][grid["status"]] == source["data"][grid["status"]]


# -- archived and binned records --------------------------------------------


async def test_an_archived_record_still_gets_a_code(client, table_uuid, grid):
    """The selection comes from what is on screen, and an archived record is viewable."""
    record = grid["records"][0]
    assert (
        await client.post(f"/api/tables/{table_uuid}/records/{record}/archive")
    ).status_code in (200, 204)
    resp = await _qr(client, table_uuid, record_uuids=[record], columns=["full_name"])
    assert len(resp.json()) == 1


async def test_a_binned_record_does_not(client, table_uuid, grid):
    """A badge for a record somebody deleted is a badge for a person who is not coming."""
    record = grid["records"][0]
    await client.delete(f"/api/tables/{table_uuid}/records/{record}")
    resp = await _qr(client, table_uuid, record_uuids=[record], columns=["full_name"])
    assert resp.json() == []


# -- permissions -------------------------------------------------------------


@pytest_asyncio.fixture
def make_client(engine, session_factory):
    """An AsyncClient authenticated as a chosen user (see test_table_transfer)."""

    @asynccontextmanager
    async def _make(user):
        orig_engine, orig_factory = db_module.engine, db_module.SessionLocal
        db_module.engine = engine
        db_module.SessionLocal = session_factory
        app = create_app()

        async def _current_user():
            return user

        app.dependency_overrides[get_current_user] = _current_user
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as c:
                yield c
        finally:
            db_module.engine, db_module.SessionLocal = orig_engine, orig_factory

    return _make


async def test_a_record_the_caller_cannot_see_is_omitted_not_refused(
    client, table_uuid, grid, session_factory, make_client
):
    """A sheet of badges for a selection must not fail wholesale because one row among
    forty carries its own grants — the same shrug a batch of file ids gets."""
    async with session_factory() as db:
        stranger = await UserService.create(
            db,
            UserCreate(
                username="stranger", email="stranger@example.com", password="pw"
            ),
        )
        stranger_uuid = str(stranger.uuid)

    # `view` on the table as a whole...
    granted = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "table",
            "resource_uuid": table_uuid,
            "user_uuid": stranger_uuid,
            "actions": ["view"],
        },
    )
    assert granted.status_code == 201, granted.text

    # ...then break one record out of that grant's reach.
    hidden, visible = grid["records"]
    broke = await client.patch(
        f"/api/tables/{table_uuid}/records/{hidden}",
        json={"permissions_inherit": False},
    )
    assert broke.status_code == 200, broke.text

    async with session_factory() as db:
        theirs_user = await UserService.get_by_username(db, "stranger")

    async with make_client(theirs_user) as theirs:
        resp = await theirs.post(
            f"/api/tables/{table_uuid}/records/qr",
            json={"record_uuids": [hidden, visible], "columns": ["full_name"]},
        )
    assert resp.status_code == 200, resp.text
    assert [row["record_uuid"] for row in resp.json()] == [visible]


# -- the saved selection ----------------------------------------------------


async def test_the_selection_is_saved_on_the_table(client, table_uuid, grid):
    resp = await client.patch(
        f"/api/tables/{table_uuid}",
        json={"settings": {"qr": {"columns": ["full_name"], "include_uuid": False}}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["settings"]["qr"] == {
        "columns": ["full_name"],
        "include_uuid": False,
    }


async def test_writing_one_settings_key_does_not_drop_the_others(client, table_uuid):
    """`settings` is a shared bag, so a write merges. Before this, the first writer of one
    key silently wiped every other — which is a data-loss bug nobody would have seen
    until a second feature used the blob."""
    first = await client.patch(
        f"/api/tables/{table_uuid}", json={"settings": {"colour": "red"}}
    )
    assert first.json()["settings"] == {"colour": "red"}

    second = await client.patch(
        f"/api/tables/{table_uuid}", json={"settings": {"qr": {"columns": ["a"]}}}
    )
    assert second.json()["settings"] == {
        "colour": "red",
        "qr": {"columns": ["a"]},
    }


async def test_a_mistyped_qr_key_is_refused(client, table_uuid):
    """A 422 naming the key, rather than a setting that silently never takes effect."""
    resp = await client.patch(
        f"/api/tables/{table_uuid}", json={"settings": {"qr": {"colums": ["a"]}}}
    )
    assert resp.status_code == 422, resp.text
    assert "qr" in resp.text


async def test_an_unknown_settings_key_is_still_free_form(client, table_uuid):
    """Only the keys this build reads are validated; the blob stays a blob."""
    resp = await client.patch(
        f"/api/tables/{table_uuid}", json={"settings": {"whatever": {"deep": [1]}}}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["settings"]["whatever"] == {"deep": [1]}


async def test_a_clone_keeps_the_selection(client, table_uuid, grid):
    """Column *names* are stored precisely so this works: a clone keeps every name, where
    uuids would need remapping."""
    await client.patch(
        f"/api/tables/{table_uuid}",
        json={"settings": {"qr": {"columns": ["full_name"]}}},
    )
    clone = await client.post(
        f"/api/tables/{table_uuid}/clone", json={"with_records": False}
    )
    assert clone.status_code == 201, clone.text
    assert clone.json()["settings"]["qr"] == {"columns": ["full_name"]}

    # And the clone's own columns answer to that name, so the setting is live rather than
    # merely copied.
    resp = await client.post(
        f"/api/tables/{clone.json()['uuid']}/records/",
        json={"data": {}},
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.parametrize("include_uuid", [True, False])
async def test_include_uuid_round_trips_through_settings(
    client, table_uuid, include_uuid
):
    resp = await client.patch(
        f"/api/tables/{table_uuid}",
        json={"settings": {"qr": {"columns": [], "include_uuid": include_uuid}}},
    )
    assert resp.json()["settings"]["qr"]["include_uuid"] is include_uuid
