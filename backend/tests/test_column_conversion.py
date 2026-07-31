"""Changing a column's type after it holds records.

The design in one line: convert into a hidden column, let a person read the report,
then swap. So most of what these tests pin is what *does not* happen — the list is
unchanged until the commit, and the commit changes nothing except the column's type
and its values.
"""
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.infrastructure import database as db_module
from src.lists.columns.models import Column
from src.lists.items.models import ListItem


@pytest_asyncio.fixture
def make_client(engine, session_factory):
    """A client authenticated as somebody else — see test_lookup_grants."""

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


async def _rows(client, list_uuid):
    return (await client.get(f"/api/lists/{list_uuid}/items/")).json()


async def _add_rows(client, list_uuid, col, values):
    for value in values:
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
        )
        assert resp.status_code == 201, resp.text


async def _convert(client, list_uuid, col, **body):
    return await client.post(f"/api/lists/{list_uuid}/columns/{col}/convert", json=body)


async def _stored(session_factory, key):
    """Raw JSONB for every record, bypassing the API's shaping."""
    async with session_factory() as db:
        rows = (await db.execute(select(ListItem.data))).scalars().all()
    return [row.get(key) for row in rows]


# -- the text hub ---------------------------------------------------------

async def test_text_to_number(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5", "12", "0"])

    report = (await _convert(client, list_uuid, col, type="number")).json()
    assert (report["converted"], report["failed"]) == (3, 0)

    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["type"] == "number"
    assert resp.json()["uuid"] == col  # same column, not a replacement

    assert sorted(r["data"][col] for r in await _rows(client, list_uuid)) == [0, 5, 12]


async def test_number_to_text(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="number")
    await _add_rows(client, list_uuid, col, [5, 12])

    report = (await _convert(client, list_uuid, col, type="text")).json()
    await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )
    assert sorted(r["data"][col] for r in await _rows(client, list_uuid)) == ["12", "5"]


async def test_choice_to_text_keeps_every_label(client, list_uuid, add_column):
    """Leaving `choice` is free — the cell already holds the label, so only the
    declared type changes."""
    col = await add_column(
        list_uuid,
        name="stage",
        display_name="Stage",
        type="choice",
        config={"options": ["Todo", "Done"]},
    )
    await _add_rows(client, list_uuid, col, ["Todo", "Done", "Todo"])

    report = (await _convert(client, list_uuid, col, type="text")).json()
    assert report["failed"] == 0
    await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )
    assert sorted(r["data"][col] for r in await _rows(client, list_uuid)) == [
        "Done",
        "Todo",
        "Todo",
    ]


async def test_text_to_choice_mints_an_option_per_distinct_value(
    client, list_uuid, add_column
):
    col = await add_column(list_uuid, name="stage", display_name="Stage", type="text")
    await _add_rows(client, list_uuid, col, ["Todo", "Done", "Todo"])

    report = (await _convert(client, list_uuid, col, type="choice")).json()
    assert (report["converted"], report["failed"]) == (3, 0)

    committed = (
        await client.post(
            f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
        )
    ).json()
    assert committed["type"] == "choice"
    assert committed["config"]["options"] == ["Done", "Todo"]  # distinct, ordered


async def test_text_to_choice_fails_the_values_outside_a_supplied_option_set(
    client, list_uuid, add_column
):
    """Supplying options states what the choices *are*, so a stray value is a
    failure rather than a reason to widen the set."""
    col = await add_column(list_uuid, name="stage", display_name="Stage", type="text")
    await _add_rows(client, list_uuid, col, ["Todo", "Nonsense"])

    report = (
        await _convert(
            client, list_uuid, col, type="choice", config={"options": ["Todo", "Done"]}
        )
    ).json()
    assert (report["converted"], report["failed"]) == (1, 1)
    assert "Nonsense" in report["errors"][0]["message"]


async def test_text_to_date(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="due", display_name="Due", type="text")
    await _add_rows(client, list_uuid, col, ["2026-05-04"])

    report = (await _convert(client, list_uuid, col, type="date")).json()
    assert report["failed"] == 0
    await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )
    assert (await _rows(client, list_uuid))[0]["data"][col] == "2026-05-04"


async def test_date_widens_to_datetime_without_a_text_round_trip(
    client, list_uuid, add_column
):
    col = await add_column(list_uuid, name="due", display_name="Due", type="date")
    await _add_rows(client, list_uuid, col, ["2026-05-04"])

    report = (await _convert(client, list_uuid, col, type="datetime")).json()
    await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )
    assert (await _rows(client, list_uuid))[0]["data"][col] == "2026-05-04T00:00:00"


async def test_datetime_truncates_to_date(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="due", display_name="Due", type="datetime")
    await _add_rows(client, list_uuid, col, ["2026-05-04T13:22:00"])

    report = (await _convert(client, list_uuid, col, type="date")).json()
    await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )
    assert (await _rows(client, list_uuid))[0]["data"][col] == "2026-05-04"


# -- references, and the permission they depend on ------------------------

async def test_user_column_to_text_writes_usernames(
    client, list_uuid, add_column, session_factory
):
    """Rendering a reference is the export's job, so it produces exactly what an
    export would: the username, never the connected person's private name."""
    async with session_factory() as db:
        await UserService.create(
            db, UserCreate(username="rosa", email="rosa@example.com", password="pw")
        )
    people = (await client.get("/api/auth/users/?q=rosa")).json()
    rosa = people[0]["uuid"] if isinstance(people, list) else people["items"][0]["uuid"]

    col = await add_column(list_uuid, name="owner", display_name="Owner", type="user")
    await _add_rows(client, list_uuid, col, [rosa])

    report = (await _convert(client, list_uuid, col, type="text")).json()
    assert (report["converted"], report["failed"]) == (1, 0)
    await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )
    assert (await _rows(client, list_uuid))[0]["data"][col] == "rosa"


async def test_text_to_user_resolves_the_username_back_to_a_uuid(
    client, list_uuid, add_column, session_factory
):
    """The other half of the round trip, and it costs nothing: `_validate_value`
    already accepts a username because a CSV written by the export contains one."""
    async with session_factory() as db:
        created = await UserService.create(
            db, UserCreate(username="rosa", email="rosa@example.com", password="pw")
        )
        rosa = str(created.uuid)

    col = await add_column(list_uuid, name="owner", display_name="Owner", type="text")
    await _add_rows(client, list_uuid, col, ["rosa", "nobody-at-all"])

    resp = await _convert(client, list_uuid, col, type="user")
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert (report["converted"], report["failed"]) == (1, 1)
    await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )
    values = [r["data"].get(col) for r in await _rows(client, list_uuid)]
    assert sorted(values, key=lambda v: v or "") == [None, rosa]


async def test_a_lookup_is_refused_when_its_target_cannot_be_read(
    client, make_client, session_factory, workspace_uuid, list_uuid, add_column
):
    """The gate that stops a conversion baking uuid strings into a text column.

    An export writes uuids when it cannot resolve a label, because it shows one
    caller what that caller may see. A conversion writes the answer down for
    everyone, permanently — so it stops instead.
    """
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
    label = await add_column(target, name="name", display_name="Name", type="text")
    await client.patch(f"/api/lists/{target}", json={"primary_column_uuid": label})
    row = (
        await client.post(
            f"/api/lists/{target}/items/", json={"data": {label: "Springfield High"}}
        )
    ).json()["uuid"]

    lookup = await add_column(
        list_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_list_uuid": target},
    )
    await _add_rows(client, list_uuid, lookup, [row])

    # The owner can read the target, so for them it converts to the label.
    report = (await _convert(client, list_uuid, lookup, type="text")).json()
    assert report["converted"] == 1
    await client.delete(
        f"/api/lists/{list_uuid}/columns/{lookup}/convert/{report['target_uuid']}"
    )

    # Somebody who may configure the list but not read the target is refused.
    async with session_factory() as db:
        outsider = await UserService.create(
            db, UserCreate(username="outsider", email="o@example.com", password="pw")
        )
    await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "list",
            "resource_uuid": list_uuid,
            "user_uuid": str(outsider.uuid),
            "actions": ["view", "configure"],
        },
    )

    async with make_client(outsider) as other:
        resp = await other.post(
            f"/api/lists/{list_uuid}/columns/{lookup}/convert", json={"type": "text"}
        )
    assert resp.status_code == 400
    assert "cannot be read" in resp.json()["detail"]


# -- failure reporting ----------------------------------------------------

async def test_a_cell_that_will_not_convert_is_reported_not_fatal(
    client, list_uuid, add_column
):
    """Best-effort, because the whole point of a scratch column is that a person
    reads the failures while the list is still untouched."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5", "n/a", "7"])

    report = (await _convert(client, list_uuid, col, type="number")).json()
    assert (report["converted"], report["failed"]) == (2, 1)
    assert len(report["errors"]) == 1
    assert "n/a" in report["errors"][0]["message"]
    assert report["errors_truncated"] is False

    # And the list is untouched while the report is being read.
    listed = await _rows(client, list_uuid)
    assert sorted(r["data"][col] for r in listed) == ["5", "7", "n/a"]
    assert (await client.get(f"/api/lists/{list_uuid}/columns/{col}")).json()[
        "type"
    ] == "text"


async def test_a_failed_cell_lands_empty_rather_than_wrong(
    client, list_uuid, add_column
):
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5", "n/a"])

    report = (await _convert(client, list_uuid, col, type="number")).json()
    await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )
    values = sorted(
        (r["data"].get(col) is None, r["data"].get(col))
        for r in await _rows(client, list_uuid)
    )
    assert values == [(False, 5), (True, None)]


# -- the list is untouched until the commit -------------------------------

async def test_the_scratch_column_is_invisible_everywhere(
    client, list_uuid, add_column, session_factory
):
    """It exists, it holds converted values, and no reader may see either."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5"])
    report = (await _convert(client, list_uuid, col, type="number")).json()
    target = report["target_uuid"]

    columns = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    assert col in [c["uuid"] for c in columns]
    assert target not in [c["uuid"] for c in columns]  # not in the list's shape

    assert (
        await client.get(f"/api/lists/{list_uuid}/columns/{target}")
    ).status_code == 404

    assert target not in (await _rows(client, list_uuid))[0]["data"]  # not in records

    csv = (
        await client.post(f"/api/lists/{list_uuid}/export", json={"format": "csv"})
    ).text
    assert "converting" not in csv

    # But it is really there, holding the converted value.
    async with session_factory() as db:
        scratch = (
            await db.execute(select(Column).where(Column.hidden.is_(True)))
        ).scalars().one()
        assert str(scratch.uuid) == target
    assert await _stored(session_factory, target) == [5]


async def test_a_second_conversion_is_refused_while_one_is_in_flight(
    client, list_uuid, add_column
):
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5"])
    assert (await _convert(client, list_uuid, col, type="number")).status_code == 200
    second = await _convert(client, list_uuid, col, type="date")
    assert second.status_code == 409
    assert "already has a conversion" in second.json()["detail"]


async def test_abandoning_leaves_no_trace(
    client, list_uuid, add_column, session_factory
):
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5"])
    report = (await _convert(client, list_uuid, col, type="number")).json()
    target = report["target_uuid"]

    resp = await client.delete(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{target}"
    )
    assert resp.status_code == 204

    async with session_factory() as db:
        assert (
            await db.execute(select(Column).where(Column.hidden.is_(True)))
        ).scalars().first() is None
    assert await _stored(session_factory, target) == [None]  # key gone from the record

    # And the column is free to be converted again.
    assert (await _convert(client, list_uuid, col, type="number")).status_code == 200


# -- what the cutover must not disturb ------------------------------------

async def test_a_view_filtering_on_the_column_survives_the_cutover(
    client, list_uuid, add_column
):
    """The cutover retypes the column in place, keeping its uuid — so a view that
    named it goes on naming it. This is the whole reason for not swapping uuids."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5", "12"])

    view = (await client.get(f"/api/lists/{list_uuid}/views/")).json()[0]["uuid"]
    await client.patch(
        f"/api/lists/{list_uuid}/views/{view}",
        json={
            "config": {
                "filters": {
                    "conjunction": "and",
                    "conditions": [
                        {"column_uuid": col, "operator": ">", "value": 10}
                    ],
                }
            }
        },
    )

    report = (await _convert(client, list_uuid, col, type="number")).json()
    await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )

    rows = (
        await client.get(f"/api/lists/{list_uuid}/items/?view_uuid={view}")
    ).json()
    # Numeric now, so the filter compares numerically rather than as text.
    assert [r["data"][col] for r in rows] == [12]


async def test_a_calculated_column_referring_to_it_survives_the_cutover(
    client, list_uuid, add_column
):
    """Formulas name columns, and the name does not move either."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    doubled = await add_column(
        list_uuid,
        name="doubled",
        display_name="Doubled",
        type="calculated",
        config={"formula": "qty * 2"},
    )
    await _add_rows(client, list_uuid, col, ["5"])

    report = (await _convert(client, list_uuid, col, type="number")).json()
    await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )

    row = (await _rows(client, list_uuid))[0]
    assert row["data"][col] == 5
    assert row["data"][doubled] == 10


async def test_the_original_values_are_recoverable_from_the_bin(
    client, list_uuid, add_column
):
    """The cutover swaps the two keys rather than overwriting one, so the old data
    ends up in a binned column of the old type — a real undo."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5", "n/a"])

    report = (await _convert(client, list_uuid, col, type="number")).json()
    await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )

    binned = (await client.get(f"/api/lists/{list_uuid}/columns/deleted")).json()
    assert [b["name"] for b in binned] == ["qty__before_conversion"]

    restored = await client.post(
        f"/api/lists/{list_uuid}/columns/{binned[0]['uuid']}/restore"
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["type"] == "text"

    old_key = binned[0]["uuid"]
    assert sorted(
        r["data"][old_key] for r in await _rows(client, list_uuid)
    ) == ["5", "n/a"]


# -- the gates the backfill deliberately skipped --------------------------

async def test_required_is_checked_at_the_cutover_not_per_cell(
    client, list_uuid, add_column
):
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5", "n/a"])

    # The backfill does not fail every blank row...
    report = (
        await _convert(client, list_uuid, col, type="number", config={"required": True})
    ).json()
    assert (report["converted"], report["failed"]) == (1, 1)

    # ...but the cutover refuses to make a required column with a hole in it.
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )
    assert resp.status_code == 409
    assert "required" in resp.json()["detail"]

    # And the conversion is still in progress, so it can be fixed and retried.
    assert (await client.get(f"/api/lists/{list_uuid}/columns/{col}")).json()[
        "type"
    ] == "text"


async def test_unique_is_checked_at_the_cutover(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5", "5"])

    report = (
        await _convert(client, list_uuid, col, type="number", config={"unique": True})
    ).json()
    assert report["failed"] == 0  # duplicates are not a per-cell problem

    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )
    assert resp.status_code == 409
    assert "more than once" in resp.json()["detail"]


# -- duplicating rather than replacing ------------------------------------

async def test_naming_it_duplicates_instead_of_converting(
    client, list_uuid, add_column
):
    """The same engine, the other ending: the new column goes live beside the
    source and the source is left exactly as it was."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5", "12"])

    report = (
        await _convert(
            client,
            list_uuid,
            col,
            type="number",
            name="qty_number",
            display_name="Qty (number)",
        )
    ).json()
    assert report["duplicate"] is True
    assert (report["converted"], report["failed"]) == (2, 0)

    created = (
        await client.post(
            f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
        )
    ).json()
    assert created["name"] == "qty_number"
    assert created["display_name"] == "Qty (number)"
    assert created["type"] == "number"
    assert created["uuid"] != col
    # None of the conversion's bookkeeping survives onto a real column.
    assert "_conversion_of" not in created["config"]
    assert "_keep_source" not in created["config"]

    # The source is untouched: same uuid, same type, same values.
    source = (await client.get(f"/api/lists/{list_uuid}/columns/{col}")).json()
    assert source["type"] == "text"
    rows = await _rows(client, list_uuid)
    assert sorted(r["data"][col] for r in rows) == ["12", "5"]
    assert sorted(r["data"][created["uuid"]] for r in rows) == [5, 12]

    # And nothing went to the bin, because nothing was displaced.
    assert (await client.get(f"/api/lists/{list_uuid}/columns/deleted")).json() == []


async def test_a_same_type_duplicate_is_a_straight_copy(client, list_uuid, add_column):
    """The plain "copy this column" case — no conversion involved, and the config
    comes along."""
    col = await add_column(
        list_uuid,
        name="stage",
        display_name="Stage",
        type="choice",
        config={"options": ["Todo", "Done"]},
    )
    await _add_rows(client, list_uuid, col, ["Todo", "Done"])

    report = (
        await _convert(
            client, list_uuid, col, type="choice", name="stage_copy",
            config={"options": ["Todo", "Done"]},
        )
    ).json()
    assert (report["converted"], report["failed"]) == (2, 0)

    created = (
        await client.post(
            f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
        )
    ).json()
    assert created["config"]["options"] == ["Todo", "Done"]
    rows = await _rows(client, list_uuid)
    assert sorted(r["data"][created["uuid"]] for r in rows) == ["Done", "Todo"]
    assert sorted(r["data"][col] for r in rows) == ["Done", "Todo"]


async def test_duplicating_a_lookup_carries_its_target(
    client, list_uuid, add_column, workspace_uuid
):
    """The dialog seeds a duplicate's config from the source, so the most
    config-heavy type is the one worth pinning: a copied lookup must still point
    where the original pointed."""
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
    label = await add_column(target, name="name", display_name="Name", type="text")
    await client.patch(f"/api/lists/{target}", json={"primary_column_uuid": label})
    row = (
        await client.post(
            f"/api/lists/{target}/items/", json={"data": {label: "Springfield High"}}
        )
    ).json()["uuid"]

    col = await add_column(
        list_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_list_uuid": target},
    )
    await _add_rows(client, list_uuid, col, [row])

    report = (
        await _convert(
            client,
            list_uuid,
            col,
            type="lookup",
            name="school_copy",
            config={"target_list_uuid": target, "multiple": False},
        )
    ).json()
    assert (report["converted"], report["failed"]) == (1, 0)

    created = (
        await client.post(
            f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
        )
    ).json()
    assert created["config"]["target_list_uuid"] == target
    # Same row uuid under both columns — a lookup copy is a straight JSONB copy.
    listed = (await _rows(client, list_uuid))[0]["data"]
    assert listed[created["uuid"]] == listed[col] == row


async def test_a_duplicate_joins_the_list_in_creation_order(
    client, list_uuid, add_column
):
    """It inherits the source's `position`, which sounds like "next to it" and is
    not: nothing in the UI ever sets `position`, so every column is 0 and the order
    falls through to `id`. A duplicate therefore appears last, like any new column.
    Pinned because the inherited `position` reads as a placement promise."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await add_column(list_uuid, name="after", display_name="After", type="text")
    await _add_rows(client, list_uuid, col, ["5"])

    report = (
        await _convert(client, list_uuid, col, type="text", name="qty_copy")
    ).json()
    await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )

    names = [
        c["name"] for c in (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    ]
    assert names.index("qty_copy") > names.index("after")


async def test_a_duplicate_cannot_take_a_name_already_in_use(
    client, list_uuid, add_column
):
    """Checked before the backfill runs, not after: a conversion that succeeds and
    then cannot be named would throw the work away for a reason known up front."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await add_column(list_uuid, name="taken", display_name="Taken", type="text")
    await _add_rows(client, list_uuid, col, ["5"])

    resp = await _convert(client, list_uuid, col, type="text", name="taken")
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


async def test_the_reserved_name_is_released_when_a_duplicate_is_abandoned(
    client, list_uuid, add_column
):
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5"])

    report = (
        await _convert(client, list_uuid, col, type="text", name="qty_copy")
    ).json()
    await client.delete(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )
    again = await _convert(client, list_uuid, col, type="text", name="qty_copy")
    assert again.status_code == 200, again.text


async def test_a_display_name_without_a_name_is_refused(client, list_uuid, add_column):
    """Ambiguous in the one direction that matters: it reads as a duplicate but
    would run as a conversion and replace the column."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    resp = await _convert(
        client, list_uuid, col, type="number", display_name="Qty (number)"
    )
    assert resp.status_code == 422


async def test_a_duplicate_still_obeys_the_cutover_gates(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5", "5"])

    report = (
        await _convert(
            client,
            list_uuid,
            col,
            type="number",
            name="qty_number",
            config={"unique": True},
        )
    ).json()
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
    )
    assert resp.status_code == 409
    assert "more than once" in resp.json()["detail"]


# -- the payloads the column dialog actually sends ------------------------

# `ColumnDialog` builds one config object and fills in every key its target type
# understands, sending nulls rather than omitting them (a normal config PATCH
# merges, so omitting a key would preserve the old value). `ColumnConfig` is
# `extra="forbid"`, so a key the dialog sends and the schema does not know is a 422
# nobody sees until a user picks that type. These pin the exact shapes.
_DIALOG_CONFIGS = {
    "number": {
        "required": False,
        "unique": False,
        "min": None,
        "max": None,
        "integer_only": False,
        "default": None,
    },
    "choice": {"required": False, "unique": False, "choices": "", "default": None},
    "link": {
        "required": False,
        "unique": False,
        "label": None,
        "default": None,
    },
    "checkbox": {"required": False, "unique": False, "default": None},
    "date": {
        "required": False,
        "unique": False,
        "default": None,
        "onupdate": None,
    },
    "datetime": {
        "required": False,
        "unique": False,
        "default": None,
        "onupdate": None,
    },
    "time": {"required": False, "unique": False, "default": None, "onupdate": None},
    "user": {
        "required": False,
        "unique": False,
        "multiple": False,
        "default": None,
        "onupdate": None,
    },
    "group": {"required": False, "unique": False, "multiple": False},
}


@pytest.mark.parametrize("target", sorted(_DIALOG_CONFIGS))
async def test_the_column_dialogs_config_payload_is_accepted(
    client, list_uuid, add_column, target
):
    col = await add_column(list_uuid, name="c", display_name="C", type="text")
    # A value the target type can actually take, so this exercises the conversion
    # rather than only the config validation.
    sample = {
        "number": "7",
        "choice": "Alpha",
        "link": "https://example.com",
        "checkbox": "true",
        "date": "2026-05-04",
        "datetime": "2026-05-04T13:22:00",
        "time": "13:22:00",
        "user": "nobody",
        "group": "nobody",
    }[target]
    await _add_rows(client, list_uuid, col, [sample])

    resp = await _convert(client, list_uuid, col, type=target, config=_DIALOG_CONFIGS[target])
    assert resp.status_code == 200, resp.text
    assert resp.json()["new_type"] == target


async def test_the_dialogs_blank_choices_string_still_mints(
    client, list_uuid, add_column
):
    """The dialog sends `choices: ""` rather than omitting it, and an empty
    shorthand has to read as "mint them" — not as an empty option set."""
    col = await add_column(list_uuid, name="stage", display_name="Stage", type="text")
    await _add_rows(client, list_uuid, col, ["Todo", "Done"])

    report = (
        await _convert(client, list_uuid, col, type="choice", config={"choices": ""})
    ).json()
    committed = (
        await client.post(
            f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
        )
    ).json()
    assert committed["config"]["options"] == ["Done", "Todo"]


async def test_the_dialogs_choices_shorthand_is_not_overwritten_by_minting(
    client, list_uuid, add_column
):
    """And a shorthand the person typed states what the choices are, so minting
    must not widen it — `Nonsense` fails rather than becoming an option."""
    col = await add_column(list_uuid, name="stage", display_name="Stage", type="text")
    await _add_rows(client, list_uuid, col, ["Todo", "Nonsense"])

    report = (
        await _convert(
            client, list_uuid, col, type="choice", config={"choices": "Todo;Done"}
        )
    ).json()
    assert (report["converted"], report["failed"]) == (1, 1)
    committed = (
        await client.post(
            f"/api/lists/{list_uuid}/columns/{col}/convert/{report['target_uuid']}"
        )
    ).json()
    assert committed["config"]["options"] == ["Todo", "Done"]


# -- pairs that are not in v1 ---------------------------------------------

@pytest.mark.parametrize(
    "source,target,config,expected",
    [
        ("choice", "number", {}, "Convert to text first"),
        ("date", "time", {}, "Convert to text first"),
        ("calculated", "text", {}, "stores no value"),
    ],
)
async def test_an_unsupported_pair_is_refused_with_the_way_round_it(
    client, list_uuid, add_column, source, target, config, expected
):
    body = {"options": ["a", "b"]} if source == "choice" else {}
    if source == "calculated":
        body = {"formula": "1 + 1"}
    col = await add_column(
        list_uuid, name="c", display_name="C", type=source, config=body
    )
    resp = await _convert(client, list_uuid, col, type=target, config=config)
    assert resp.status_code == 400
    assert expected in resp.json()["detail"]


async def test_converting_to_calculated_is_refused(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    resp = await _convert(
        client, list_uuid, col, type="calculated", config={"formula": "1 + 1"}
    )
    assert resp.status_code == 400
    assert "stores no value" in resp.json()["detail"]
