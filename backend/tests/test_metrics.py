"""Table metrics, over the HTTP API.

A metric is a named formula stored beside its table's views and forms, and its
*value* is computed per request rather than stored. So the load-bearing things to
pin are what the value is computed over — the records this caller may see,
through this view, matching this search — and that a formula which no longer
matches the table says so instead of quietly reading zero.
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
from src.tables.records.schemas import TableRecordCreate
from src.tables.records.services import TableRecordService
from src.tables.schemas import TableCreate
from src.tables.services import TableService
from src.permissions.enums import Action, ResourceType
from src.permissions.schemas import GrantCreate
from src.permissions.services import PermissionService
from src.workspaces.schemas import WorkspaceCreate
from src.workspaces.services import WorkspaceService


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


async def add_metric(client, table_uuid, name, formula, **config):
    return await client.post(
        f"/api/tables/{table_uuid}/metrics/",
        json={
            "name": name,
            "display_name": name.replace("_", " ").title(),
            "config": {"formula": formula, **config},
        },
    )


def value_of(payload, name):
    """One card's value out of a /values response."""
    return next(v["value"] for v in payload["values"] if v["name"] == name)


def error_of(payload, name):
    return next(v["error"] for v in payload["values"] if v["name"] == name)


@pytest_asyncio.fixture
async def timesheet(client, table_uuid, add_column):
    """A table with `hours` and `rate` numbers, an `owner` text, and three records."""
    hours = await add_column(table_uuid, name="hours", display_name="Hours", type="number")
    rate = await add_column(table_uuid, name="rate", display_name="Rate", type="number")
    owner = await add_column(table_uuid, name="owner", display_name="Owner", type="text")
    for values in (
        {hours: 6, rate: 70, owner: "ana"},
        {hours: 12, rate: 70, owner: "raj"},
        {hours: 4, rate: 50, owner: "ana"},
    ):
        resp = await client.post(f"/api/tables/{table_uuid}/records/", json={"data": values})
        assert resp.status_code == 201, resp.text
    return {"table": table_uuid, "hours": hours, "rate": rate, "owner": owner}


# --------------------------------------------------------------------------
# Definitions: CRUD and validation
# --------------------------------------------------------------------------


async def test_create_table_get_patch_and_delete(client, timesheet):
    table = timesheet["table"]
    created = await add_metric(client, table, "total_hours", "SUM(hours)")
    assert created.status_code == 201, created.text
    assert created.json()["config"]["formula"] == "SUM(hours)"
    uuid = created.json()["uuid"]

    listing = await client.get(f"/api/tables/{table}/metrics/")
    assert [s["name"] for s in listing.json()] == ["total_hours"]

    # Addressed by uuid alone, like every other child of a table. A name is not a
    # uuid, so it is rejected rather than resolved.
    by_uuid = await client.get(f"/api/tables/{table}/metrics/{uuid}")
    assert by_uuid.json()["uuid"] == uuid
    assert (
        await client.get(f"/api/tables/{table}/metrics/total_hours")
    ).status_code == 422

    patched = await client.patch(
        f"/api/tables/{table}/metrics/{uuid}",
        json={"display_name": "Hours logged", "config": {"formula": "SUM(hours * rate)"}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["display_name"] == "Hours logged"
    assert patched.json()["config"]["formula"] == "SUM(hours * rate)"

    assert (await client.delete(f"/api/tables/{table}/metrics/{uuid}")).status_code == 204
    assert (await client.get(f"/api/tables/{table}/metrics/")).json() == []


async def test_names_are_unique_per_table(client, timesheet):
    table = timesheet["table"]
    assert (await add_metric(client, table, "total", "SUM(hours)")).status_code == 201
    clash = await add_metric(client, table, "total", "SUM(rate)")
    assert clash.status_code == 409, clash.text


@pytest.mark.parametrize(
    "formula,reason",
    [
        ("SUM(nope)", "unknown column"),
        ("hours", "aggregate"),
        ("1 + 1", "aggregate"),
        ("SUM(hours) +", "not a valid expression"),
    ],
)
async def test_a_formula_that_cannot_work_is_refused_at_config_time(
    client, timesheet, formula, reason
):
    resp = await add_metric(client, timesheet["table"], "bad", formula)
    assert resp.status_code == 400, resp.text
    assert reason.lower() in resp.json()["detail"].lower()


async def test_only_number_text_and_calculated_columns_can_be_aggregated(
    client, timesheet, add_column
):
    """The other types store uuids or ISO strings whose text form would surprise —
    the same restriction a calculated column's formula lives under."""
    await add_column(timesheet["table"], name="due", display_name="Due", type="date")
    resp = await add_metric(client, timesheet["table"], "latest", "MAX(due)")
    assert resp.status_code == 400, resp.text
    assert "'due' is date" in resp.json()["detail"]


# --------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------


async def test_values_answer_over_every_record(client, timesheet):
    table = timesheet["table"]
    await add_metric(client, table, "total_hours", "SUM(hours)")
    await add_metric(client, table, "records", "COUNT()")
    await add_metric(client, table, "revenue", "SUM(hours * rate)")
    await add_metric(client, table, "people", "UNIQUE(owner)")

    resp = await client.get(f"/api/tables/{table}/metrics/values")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["row_count"] == 3
    assert body["truncated"] is False
    assert value_of(body, "total_hours") == 22
    assert value_of(body, "records") == 3
    assert value_of(body, "revenue") == 6 * 70 + 12 * 70 + 4 * 50
    assert value_of(body, "people") == 2


async def test_a_count_stays_a_count(client, timesheet):
    """4.0 is a wrong answer to "how many records", so the response keeps ints."""
    await add_metric(client, timesheet["table"], "records", "COUNT()")
    body = (await client.get(f"/api/tables/{timesheet['table']}/metrics/values")).json()
    assert value_of(body, "records") == 3
    assert isinstance(value_of(body, "records"), int)


async def test_values_follow_the_views_filters(client, timesheet):
    """A metric sitting above a filtered grid must describe what the grid shows."""
    table, hours = timesheet["table"], timesheet["hours"]
    await add_metric(client, table, "total_hours", "SUM(hours)")
    view = await client.post(
        f"/api/tables/{table}/views/",
        json={
            "name": "long_days",
            "display_name": "Long days",
            "config": {
                "filters": {
                    "conjunction": "and",
                    "conditions": [{"column_uuid": hours, "operator": ">", "value": 5}],
                }
            },
        },
    )
    assert view.status_code == 201, view.text
    view_uuid = view.json()["uuid"]

    body = (
        await client.get(f"/api/tables/{table}/metrics/values?view_uuid={view_uuid}")
    ).json()
    assert body["row_count"] == 2
    assert value_of(body, "total_hours") == 18


async def test_values_narrow_with_the_search(client, timesheet):
    table = timesheet["table"]
    await add_metric(client, table, "total_hours", "SUM(hours)")
    body = (await client.get(f"/api/tables/{table}/metrics/values?q=ana")).json()
    assert body["row_count"] == 2
    assert value_of(body, "total_hours") == 10


async def test_a_calculated_column_can_be_aggregated(client, timesheet):
    """A metric is a leaf — nothing derives from one — so unlike a calculated
    column it may read another calculated column with no cycle to worry about."""
    table = timesheet["table"]
    made = await client.post(
        f"/api/tables/{table}/columns/",
        json={
            "name": "line_total",
            "display_name": "Line total",
            "type": "calculated",
            "config": {"formula": "hours * rate"},
        },
    )
    assert made.status_code == 201, made.text

    created = await add_metric(client, table, "revenue", "SUM(line_total)")
    assert created.status_code == 201, created.text
    body = (await client.get(f"/api/tables/{table}/metrics/values")).json()
    assert value_of(body, "revenue") == 6 * 70 + 12 * 70 + 4 * 50


async def test_an_empty_table_totals_zero_but_averages_nothing(client, table_uuid, add_column):
    await add_column(table_uuid, name="hours", display_name="Hours", type="number")
    await add_metric(client, table_uuid, "total", "SUM(hours)")
    await add_metric(client, table_uuid, "average", "AVG(hours)")

    body = (await client.get(f"/api/tables/{table_uuid}/metrics/values")).json()
    assert body["row_count"] == 0
    assert value_of(body, "total") == 0
    assert value_of(body, "average") is None


async def test_a_renamed_column_breaks_its_card_and_says_so(client, timesheet):
    """Formulas reference columns by name and names are editable, so this can
    happen. The card must report it: a broken SUM rendering 0 is the one answer
    nobody would catch."""
    table = timesheet["table"]
    await add_metric(client, table, "total_hours", "SUM(hours)")
    await add_metric(client, table, "records", "COUNT()")

    renamed = await client.patch(
        f"/api/tables/{table}/columns/{timesheet['hours']}", json={"name": "worked"}
    )
    assert renamed.status_code == 200, renamed.text

    body = (await client.get(f"/api/tables/{table}/metrics/values")).json()
    assert value_of(body, "total_hours") is None
    assert error_of(body, "total_hours") == "Unknown column 'hours'"
    # And the break costs its own card, not the request.
    assert value_of(body, "records") == 3
    assert error_of(body, "records") is None


async def test_values_on_a_table_with_no_metrics_is_empty(client, timesheet):
    body = (await client.get(f"/api/tables/{timesheet['table']}/metrics/values")).json()
    assert body == {"row_count": 0, "truncated": False, "values": []}


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------


async def test_two_readers_get_the_totals_of_what_each_can_see(
    make_client, session_factory
):
    """The metric is one definition, but its value is a permissioned read — so a
    reader who cannot see a record must not learn its number through a total."""
    async with session_factory() as db:
        owner = await UserService.create(
            db, UserCreate(username="owner", email="owner@example.com", password="pw")
        )
        other = await UserService.create(
            db, UserCreate(username="other", email="other@example.com", password="pw")
        )
        ws = await WorkspaceService.create(
            db, owner.id, WorkspaceCreate(name="ws", display_name="WS")
        )
        table = await TableService.create(
            db, TableCreate(name="table", display_name="Lst", workspace_uuid=ws.uuid)
        )
        table_uuid = str(table.uuid)

    async with make_client(owner) as client:
        hours = (
            await client.post(
                f"/api/tables/{table_uuid}/columns/",
                json={"name": "hours", "display_name": "Hours", "type": "number"},
            )
        ).json()["uuid"]
        await add_metric(client, table_uuid, "total_hours", "SUM(hours)")
        for amount in (6, 12, 4):
            await client.post(
                f"/api/tables/{table_uuid}/records/", json={"data": {hours: amount}}
            )
        body = (await client.get(f"/api/tables/{table_uuid}/metrics/values")).json()
        assert value_of(body, "total_hours") == 22

    async with session_factory() as db:
        table = await TableService.get_any_by_uuid(db, table_uuid)
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )
        # One record leaves the table's umbrella with no grant of its own, so the
        # table grant no longer reaches it.
        records = await TableRecordService.list(db, table, owner, limit=100)
        hidden = records[0]
        hidden_hours = (hidden.data or {})[hours]
        hidden.permissions_inherit = False
        await db.commit()

    async with make_client(other) as client:
        grid = await client.get(f"/api/tables/{table_uuid}/records/")
        assert len(grid.json()) == 2
        body = (await client.get(f"/api/tables/{table_uuid}/metrics/values")).json()
        assert body["row_count"] == 2
        assert value_of(body, "total_hours") == 22 - hidden_hours


async def test_configuring_a_metric_needs_configure_on_the_table(
    make_client, session_factory
):
    async with session_factory() as db:
        owner = await UserService.create(
            db, UserCreate(username="owner", email="owner@example.com", password="pw")
        )
        other = await UserService.create(
            db, UserCreate(username="other", email="other@example.com", password="pw")
        )
        ws = await WorkspaceService.create(
            db, owner.id, WorkspaceCreate(name="ws", display_name="WS")
        )
        table = await TableService.create(
            db, TableCreate(name="table", display_name="Lst", workspace_uuid=ws.uuid)
        )
        await TableRecordService.create(db, table, TableRecordCreate(data={}))
        await PermissionService.upsert_grant(
            db,
            owner,
            GrantCreate(
                resource_type=ResourceType.table,
                resource_uuid=table.uuid,
                user_uuid=other.uuid,
                actions=[Action.view],
            ),
        )
        table_uuid = str(table.uuid)

    async with make_client(owner) as client:
        assert (await add_metric(client, table_uuid, "records", "COUNT()")).status_code == 201

    async with make_client(other) as client:
        # A viewer reads the cards...
        body = (await client.get(f"/api/tables/{table_uuid}/metrics/values")).json()
        assert value_of(body, "records") == 1
        assert (await client.get(f"/api/tables/{table_uuid}/metrics/")).status_code == 200
        # ...but changing them is a schema change.
        denied = await add_metric(client, table_uuid, "another", "COUNT()")
        assert denied.status_code == 403, denied.text


# --------------------------------------------------------------------------
# Lifecycle alongside the table
# --------------------------------------------------------------------------


async def test_metrics_go_down_and_come_back_with_their_table(client, timesheet):
    table = timesheet["table"]
    await add_metric(client, table, "total_hours", "SUM(hours)")

    assert (await client.delete(f"/api/tables/{table}")).status_code == 204
    restored = await client.post(f"/api/tables/{table}/restore")
    assert restored.status_code == 200, restored.text

    listing = await client.get(f"/api/tables/{table}/metrics/")
    assert [s["name"] for s in listing.json()] == ["total_hours"]


async def test_a_clone_carries_the_metrics(client, timesheet):
    """A metric names its columns by name, and a clone keeps every name — so
    unlike a view's config there is nothing to remap, but the rows still have to
    be copied or the clone loses its cards."""
    table = timesheet["table"]
    await add_metric(client, table, "total_hours", "SUM(hours)")

    cloned = await client.post(f"/api/tables/{table}/clone", json={"with_records": True})
    assert cloned.status_code == 201, cloned.text
    clone_uuid = cloned.json()["uuid"]

    listing = await client.get(f"/api/tables/{clone_uuid}/metrics/")
    assert [s["name"] for s in listing.json()] == ["total_hours"]

    body = (await client.get(f"/api/tables/{clone_uuid}/metrics/values")).json()
    assert value_of(body, "total_hours") == 22
