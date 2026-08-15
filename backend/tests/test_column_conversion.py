"""Changing a column's type after it holds records.

The design in one line: preview it without writing anything, then do the whole thing
in one transaction. So most of what these tests pin is what *does not* happen — the
preview leaves no trace at all, and the apply changes nothing except the column's type
and its values, or nothing whatsoever if it refuses.

This used to be a two-call design that staged the work in a hidden column, and the
gap between the calls was wide enough for a person to read a report in. A record
created in that gap was blanked and a record edited in it was reverted, both silently.
The tests that pinned the machinery of that design — the invisible scratch column, the
409 on a second start, the cleanup on abandon — are gone with it, replaced by the ones
under "the preview writes nothing" and "records written during the apply".
"""
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

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
    """The dry run. Writes nothing; returns the report."""
    return await client.post(f"/api/lists/{list_uuid}/columns/{col}/convert", json=body)


async def _apply(client, list_uuid, col, report, **extra):
    """Ask for the conversion the report describes.

    The two calls share no server-side handle — that is the whole point of the
    design — so this one carries everything it needs. It sends the report's
    *resolved* config rather than whatever was originally typed, because for
    `text` -> `choice` the preview mints options from the source's distinct values
    and the apply has to be asking the same question the person read.
    """
    body = {
        "type": report["new_type"],
        "config": report["config"],
        "name": report["name"],
        "display_name": report["display_name"],
        **extra,
    }
    return await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/apply", json=body
    )


async def _hidden_columns(session_factory):
    """Nothing may ever be hidden again — the staged design was the only thing that
    set it, and a stranded hidden column was one of the four bugs this replaced."""
    async with session_factory() as db:
        return (
            await db.execute(select(Column).where(Column.hidden.is_(True)))
        ).scalars().all()


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

    resp = await _apply(client, list_uuid, col, report)
    assert resp.status_code == 200, resp.text
    assert resp.json()["type"] == "number"
    assert resp.json()["uuid"] == col  # same column, not a replacement

    assert sorted(r["data"][col] for r in await _rows(client, list_uuid)) == [0, 5, 12]


async def test_number_to_text(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="number")
    await _add_rows(client, list_uuid, col, [5, 12])

    report = (await _convert(client, list_uuid, col, type="text")).json()
    await _apply(client, list_uuid, col, report)
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
    await _apply(client, list_uuid, col, report)
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
        await _apply(client, list_uuid, col, report)
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


async def test_text_to_choice_keeps_stray_values_when_others_are_allowed(
    client, list_uuid, add_column
):
    """`allow_other` is what a free-text column converting to a choice usually
    wants: the options say what people should pick from now on, and the answers
    already written stay as they are rather than failing a row at a time."""
    col = await add_column(list_uuid, name="stage", display_name="Stage", type="text")
    await _add_rows(client, list_uuid, col, ["Todo", "Nonsense"])

    report = (
        await _convert(
            client,
            list_uuid,
            col,
            type="choice",
            config={"options": ["Todo", "Done"], "allow_other": True},
        )
    ).json()
    assert (report["converted"], report["failed"]) == (2, 0)

    await _apply(client, list_uuid, col, report)
    assert sorted(r["data"][col] for r in await _rows(client, list_uuid)) == [
        "Nonsense",
        "Todo",
    ]


async def test_text_to_date(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="due", display_name="Due", type="text")
    await _add_rows(client, list_uuid, col, ["2026-05-04"])

    report = (await _convert(client, list_uuid, col, type="date")).json()
    assert report["failed"] == 0
    await _apply(client, list_uuid, col, report)
    assert (await _rows(client, list_uuid))[0]["data"][col] == "2026-05-04"


async def test_date_widens_to_datetime_without_a_text_round_trip(
    client, list_uuid, add_column
):
    col = await add_column(list_uuid, name="due", display_name="Due", type="date")
    await _add_rows(client, list_uuid, col, ["2026-05-04"])

    report = (await _convert(client, list_uuid, col, type="datetime")).json()
    await _apply(client, list_uuid, col, report)
    assert (await _rows(client, list_uuid))[0]["data"][col] == "2026-05-04T00:00:00"


async def test_datetime_truncates_to_date(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="due", display_name="Due", type="datetime")
    await _add_rows(client, list_uuid, col, ["2026-05-04T13:22:00"])

    report = (await _convert(client, list_uuid, col, type="date")).json()
    await _apply(client, list_uuid, col, report)
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
    await _apply(client, list_uuid, col, report)
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
    await _apply(client, list_uuid, col, report)
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

    # The owner can read the target, so for them it converts to the label. Nothing
    # to throw away afterwards — the preview wrote nothing, so simply not applying
    # it is the whole of "changed my mind".
    report = (await _convert(client, list_uuid, lookup, type="text")).json()
    assert report["converted"] == 1

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
    """Best-effort, because the whole point of the preview is that a person reads the
    failures while the list is still untouched -- and it still is, since the preview
    has written nothing at this point."""
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
    await _apply(client, list_uuid, col, report)
    values = sorted(
        (r["data"].get(col) is None, r["data"].get(col))
        for r in await _rows(client, list_uuid)
    )
    assert values == [(False, 5), (True, None)]


# -- the preview writes nothing -------------------------------------------
#
# These four replace the tests that used to pin the staged design — that the hidden
# scratch column was invisible everywhere, that a second conversion was refused while
# one was in flight, and that abandoning cleaned up after itself. All three described
# machinery that existed only to make a two-call conversion survivable, and all three
# are moot now that the preview creates nothing at all. What is pinned instead is the
# stronger property that made them unnecessary.

async def test_the_preview_writes_nothing_at_all(
    client, list_uuid, add_column, session_factory
):
    """No column, no value, no state of any kind.

    The old design staged the work in a hidden column, which is what made a record
    written afterwards get lost. The preview leaves nothing behind for anything to
    be lost *into*."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5"])

    before = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    async with session_factory() as db:
        rows_before = (await db.execute(select(ListItem.data))).scalars().all()

    report = (await _convert(client, list_uuid, col, type="number")).json()
    assert report["converted"] == 1

    after = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    assert [c["uuid"] for c in after] == [c["uuid"] for c in before]
    assert next(c for c in after if c["uuid"] == col)["type"] == "text"

    # Not one row of the columns table either — a hidden column would not show up
    # in the listing above, which is exactly how the old scratch column hid.
    async with session_factory() as db:
        assert len((await db.execute(select(Column))).scalars().all()) == len(before)
    assert not await _hidden_columns(session_factory)

    # And every record is byte-for-byte what it was: the value unconverted, and no
    # second key written beside it.
    async with session_factory() as db:
        assert (
            await db.execute(select(ListItem.data))
        ).scalars().all() == rows_before


async def test_previewing_twice_is_fine(client, list_uuid, add_column):
    """There is no in-flight state, so there is nothing to conflict with.

    The staged design refused a second `start` with a 409 naming a scratch column
    the person had never seen — and could not clear it, because abandoning needed a
    `target_uuid` that only the first response carried. Previewing is now just a
    question, and asking it twice is allowed, including with a different answer in
    mind."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5"])

    assert (await _convert(client, list_uuid, col, type="number")).status_code == 200
    assert (await _convert(client, list_uuid, col, type="number")).status_code == 200
    # Including changing your mind about the target type, which under the old design
    # meant first finding and abandoning a conversion you could not name.
    assert (await _convert(client, list_uuid, col, type="date")).status_code == 200
    assert (await _convert(client, list_uuid, col, type="number")).status_code == 200


async def test_walking_away_from_a_preview_leaves_no_trace(
    client, list_uuid, add_column, session_factory
):
    """The replacement for "abandoning leaves no trace": there is nothing to abandon.

    Not applying a preview is simply not making the second call, so the column stays
    convertible and no cleanup route has to exist for anyone to find."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5"])
    await _convert(client, list_uuid, col, type="number")

    assert not await _hidden_columns(session_factory)
    assert await _stored(session_factory, col) == ["5"]

    # And it converts normally afterwards.
    report = (await _convert(client, list_uuid, col, type="number")).json()
    assert (await _apply(client, list_uuid, col, report)).status_code == 200


async def test_nothing_is_ever_hidden_any_more(
    client, list_uuid, add_column, session_factory
):
    """`Column.hidden` was set by exactly one thing, and that thing is gone.

    The primitive stays — `drop_conversion_scratch` needs it to find rows the old
    design stranded — but nothing in the conversion path may set it again. A hidden
    column is unreachable by construction: no reader sees it and no code path will
    ever finish it."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5"])

    report = (await _convert(client, list_uuid, col, type="number")).json()
    assert not await _hidden_columns(session_factory)
    await _apply(client, list_uuid, col, report)
    assert not await _hidden_columns(session_factory)

    dup = (await _convert(client, list_uuid, col, type="text", name="copy")).json()
    await _apply(client, list_uuid, col, dup, name="copy")
    assert not await _hidden_columns(session_factory)


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
    await _apply(client, list_uuid, col, report)

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
    await _apply(client, list_uuid, col, report)

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
    await _apply(client, list_uuid, col, report)

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
    resp = await _apply(client, list_uuid, col, report)
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

    resp = await _apply(client, list_uuid, col, report)
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
        await _apply(client, list_uuid, col, report, name="qty_number")
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
        await _apply(client, list_uuid, col, report, name="stage_copy")
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
        await _apply(client, list_uuid, col, report, name="school_copy")
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
    await _apply(client, list_uuid, col, report, name="qty_copy")

    names = [
        c["name"] for c in (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    ]
    assert names.index("qty_copy") > names.index("after")


async def test_a_duplicate_cannot_take_a_name_already_in_use(
    client, list_uuid, add_column
):
    """Reported by the preview as well as refused by the apply.

    The preview cannot *reserve* the name — it creates nothing — so this check is a
    courtesy rather than the guard: it means somebody reads "that name is taken"
    instead of reading a report and only then being refused. The real guard is the
    partial unique index, via the IntegrityError the apply catches, since nothing
    here can stop the name being taken in between."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await add_column(list_uuid, name="taken", display_name="Taken", type="text")
    await _add_rows(client, list_uuid, col, ["5"])

    resp = await _convert(client, list_uuid, col, type="text", name="taken")
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]

    # And again at the apply, which is the one that actually holds the line.
    report = (
        await _convert(client, list_uuid, col, type="text", name="free")
    ).json()
    await add_column(list_uuid, name="free", display_name="Free", type="text")
    late = await _apply(client, list_uuid, col, report)
    assert late.status_code == 409, late.text


async def test_a_previewed_duplicate_does_not_reserve_its_name(
    client, list_uuid, add_column
):
    """Replaces "the reserved name is released when a duplicate is abandoned".

    There is nothing to release, because nothing is taken: the staged design held
    the name against the unique index from the moment the report was built, which is
    what made releasing it a thing that had to be got right. Previewing the same
    duplicate twice is fine, and an ordinary column may still take the name in
    between — the last of those being the point, since a name held by a report
    nobody applied was a name nobody could use and nobody could see was held."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5"])

    first = await _convert(client, list_uuid, col, type="text", name="qty_copy")
    assert first.status_code == 200, first.text
    second = await _convert(client, list_uuid, col, type="text", name="qty_copy")
    assert second.status_code == 200, second.text

    ordinary = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={"name": "qty_copy", "display_name": "Qty copy", "type": "text"},
    )
    assert ordinary.status_code == 201, ordinary.text


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
    resp = await _apply(client, list_uuid, col, report, name="qty_number")
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
        await _apply(client, list_uuid, col, report)
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
        await _apply(client, list_uuid, col, report)
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


# -- records written while the conversion runs ----------------------------
#
# The bug this design replaced, pinned from every side. Under the staged version a
# record created between the report and the commit was blanked — the swap wrote JSON
# null over the live key — and a record edited in that window was reverted to whatever
# the backfill had computed beforehand. Both were silent, and both were reported as a
# successful conversion.
#
# There is no such window now, so what these pin is the property that replaced it:
# writing between the two calls is ordinary editing and survives, because the apply
# reads the records as they are rather than trusting anything the preview saw.


async def test_a_record_created_between_the_preview_and_the_apply_keeps_its_value(
    client, list_uuid, add_column
):
    """The exact shape of the old data loss. The record is created after the report,
    so the staged design had nothing for it under the target key and the swap wrote a
    null over the value the person had just typed."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5"])

    report = (await _convert(client, list_uuid, col, type="number")).json()
    assert report["converted"] == 1  # the report only ever saw the first record

    await _add_rows(client, list_uuid, col, ["7"])
    resp = await _apply(client, list_uuid, col, report)
    assert resp.status_code == 200, resp.text

    assert sorted(r["data"][col] for r in await _rows(client, list_uuid)) == [5, 7]


async def test_a_record_edited_between_the_preview_and_the_apply_keeps_the_edit(
    client, list_uuid, add_column
):
    """The other half: the staged design held a *stale* value under the target key and
    installed it, quietly undoing the edit."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "5"}}
    )
    item = created.json()["uuid"]

    report = (await _convert(client, list_uuid, col, type="number")).json()

    await client.patch(
        f"/api/lists/{list_uuid}/items/{item}", json={"data": {col: "9"}}
    )
    assert (await _apply(client, list_uuid, col, report)).status_code == 200
    assert [r["data"][col] for r in await _rows(client, list_uuid)] == [9]


async def test_a_cell_cleared_between_the_preview_and_the_apply_stays_cleared(
    client, list_uuid, add_column
):
    """An emptied cell is a write like any other, and the swap used to resurrect it
    from the backfill's copy."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "5"}}
    )
    item = created.json()["uuid"]

    report = (await _convert(client, list_uuid, col, type="number")).json()
    await client.patch(
        f"/api/lists/{list_uuid}/items/{item}", json={"data": {col: None}}
    )

    assert (await _apply(client, list_uuid, col, report)).status_code == 200
    assert (await _rows(client, list_uuid))[0]["data"].get(col) is None


async def test_a_duplicate_also_sees_records_added_after_its_report(
    client, list_uuid, add_column
):
    """The duplicate path had the same hole — it published a copy missing exactly the
    rows and edits made while its report was on screen — and it is closed by the same
    single transaction rather than by a second mechanism."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5"])

    report = (
        await _convert(client, list_uuid, col, type="number", name="qty_number")
    ).json()
    await _add_rows(client, list_uuid, col, ["7"])

    created = (await _apply(client, list_uuid, col, report)).json()
    copy = created["uuid"]
    rows = await _rows(client, list_uuid)
    assert sorted(r["data"][copy] for r in rows) == [5, 7]
    assert sorted(r["data"][col] for r in rows) == ["5", "7"]  # source untouched


async def test_an_empty_cell_gains_no_key_under_either_column(
    client, list_uuid, add_column, session_factory
):
    """The swap built its result with `jsonb_build_object`, and `->` on an absent key
    yields SQL NULL, which becomes an explicit `{"k": null}` rather than no key at
    all — so every record with an empty cell came out of a conversion carrying two
    JSON nulls it did not have before. Writing only the keys there are values for
    ends that."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})

    report = (await _convert(client, list_uuid, col, type="number")).json()
    assert (report["converted"], report["failed"]) == (0, 0)
    assert (await _apply(client, list_uuid, col, report)).status_code == 200

    async with session_factory() as db:
        data = (await db.execute(select(ListItem.data))).scalars().one()
    assert col not in data
    assert all(value is not None for value in data.values())


async def test_a_failed_gate_leaves_absolutely_nothing_behind(
    client, list_uuid, add_column, session_factory
):
    """`required` and `unique` are still cutover gates, but failing one is free now.

    The staged design left the conversion in progress — a hidden column full of
    converted values, and a 409 waiting for the next attempt. Everything is one
    transaction now, so a refused gate rolls the whole thing back: no backup column,
    no retype, no hidden anything."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})

    before = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    report = (
        await _convert(client, list_uuid, col, type="number", config={"required": True})
    ).json()
    resp = await _apply(client, list_uuid, col, report)
    assert resp.status_code == 409, resp.text
    assert "required" in resp.json()["detail"]

    after = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    assert [c["uuid"] for c in after] == [c["uuid"] for c in before]
    assert next(c for c in after if c["uuid"] == col)["type"] == "text"
    assert not await _hidden_columns(session_factory)
    async with session_factory() as db:
        assert (
            await db.execute(
                select(Column).where(Column.name.like("%__before_conversion"))
            )
        ).scalars().first() is None


async def test_the_original_values_are_recoverable_after_a_conversion(
    client, list_uuid, add_column, session_factory
):
    """The undo the key swap used to provide, provided by writing two keys instead:
    the backup column is binned, carries the *old* type, and holds what was there."""
    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5"])
    report = (await _convert(client, list_uuid, col, type="number")).json()
    await _apply(client, list_uuid, col, report)

    async with session_factory() as db:
        backup = (
            await db.execute(
                select(Column).where(Column.name == "qty__before_conversion")
            )
        ).scalars().one()
        assert backup.deleted_at is not None  # in the bin
        assert backup.type.value == "text"  # the old type
        backup_uuid = str(backup.uuid)
    assert await _stored(session_factory, backup_uuid) == ["5"]


async def test_a_write_landing_during_the_apply_refuses_the_whole_conversion(
    client, list_uuid, add_column, session_factory, monkeypatch
):
    """The last guard, and the only one that cannot be reached through the API.

    Everything above closes the *human-scale* window, which is the one that mattered.
    What is left is a sub-second one: under READ COMMITTED a write can commit after
    the pass has read a row and before the transaction ends, and that row would then
    be converted from a value nobody has any more. `_fingerprint` reads the list's
    row count and newest `updated_at` before and after; if either moved, the whole
    thing rolls back.

    Driving that through the ASGI client does not work — httpx serializes the
    requests — so the concurrent write is injected into the pass itself from a second
    session, which is the same trick `test_unique_concurrency` uses for the same
    reason. Note it has to be a *committed* write from another connection: our own
    writes go through raw SQL precisely so they do not stamp `updated_at` and trip
    this check on themselves.

    It is an INSERT rather than an UPDATE, for two reasons. It is the dangerous case
    — a record created mid-apply holds a value the pass never saw and would come out
    the far side unconverted under a column that has just been retyped. And an UPDATE
    would simply hang: the pass has already written those rows, so a second connection
    touching them blocks on locks this transaction will not release until it finishes
    waiting for that very statement.
    """
    from src.lists.columns.conversion_service import ConversionService

    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5"])
    report = (await _convert(client, list_uuid, col, type="number")).json()

    original = ConversionService._run.__func__

    async def _run_then_interfere(cls, db, lst, source, target, user, keys):
        result = await original(cls, db, lst, source, target, user, keys=keys)
        if keys is not None:  # the writing pass, not a dry run
            async with session_factory() as other:
                await other.execute(
                    text(
                        "INSERT INTO list_items "
                        "  (uuid, list_id, data, created_at, updated_at) "
                        "VALUES (gen_random_uuid(), :lid, '{}'::jsonb, now(), now())"
                    ),
                    {"lid": lst.id},
                )
                await other.commit()
        return result

    monkeypatch.setattr(
        ConversionService, "_run", classmethod(_run_then_interfere)
    )

    resp = await _apply(client, list_uuid, col, report)
    assert resp.status_code == 409, resp.text
    assert "changed while the conversion was running" in resp.json()["detail"]

    # Nothing the conversion did survived: the column is still text, and the value is
    # still the *string* rather than the number it was converting to. (The row the
    # interference inserted is legitimately still there — it was committed by another
    # connection and is nothing to do with the rollback, which is why this asserts on
    # the original cell rather than on the whole list.)
    columns = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    assert next(c for c in columns if c["uuid"] == col)["type"] == "text"
    assert await _stored(session_factory, col) == ["5", None]
    assert not await _hidden_columns(session_factory)
    async with session_factory() as db:
        assert (
            await db.execute(
                select(Column).where(Column.name.like("%__before_conversion"))
            )
        ).scalars().first() is None


async def test_an_update_during_the_apply_is_caught_by_the_timestamp_half(
    client, list_uuid, add_column, session_factory, monkeypatch
):
    """`_fingerprint` is a pair, and the test above exercises only one half of it.

    An insert moves the row count *and* the newest `updated_at`, so deleting the
    timestamp from the fingerprint entirely would not fail that one. This moves only
    the timestamp: it updates a record whose source cell is **empty**, which the pass
    skips and therefore never locks — so the write lands instead of deadlocking, and
    the count does not change. Without `max(updated_at)` in the fingerprint this
    conversion would finish straight over the top of it.
    """
    from src.lists.columns.conversion_service import ConversionService

    col = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    await _add_rows(client, list_uuid, col, ["5"])
    blank = (
        await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
    ).json()["uuid"]

    report = (await _convert(client, list_uuid, col, type="number")).json()
    original = ConversionService._run.__func__

    async def _run_then_touch(cls, db, lst, source, target, user, keys):
        result = await original(cls, db, lst, source, target, user, keys=keys)
        if keys is not None:
            async with session_factory() as other:
                await other.execute(
                    text(
                        "UPDATE list_items SET updated_at = now() "
                        " WHERE uuid = CAST(:u AS uuid)"
                    ),
                    {"u": blank},
                )
                await other.commit()
        return result

    monkeypatch.setattr(ConversionService, "_run", classmethod(_run_then_touch))

    resp = await _apply(client, list_uuid, col, report)
    assert resp.status_code == 409, resp.text
    columns = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    assert next(c for c in columns if c["uuid"] == col)["type"] == "text"
