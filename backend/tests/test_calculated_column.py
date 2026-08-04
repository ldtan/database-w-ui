"""The `calculated` column type, over the HTTP API.

A calculated column holds a formula over other columns. Its value is **written into the
record** on every save, so Postgres can filter, sort, group and search it — and
**recomputed on every read**, which overwrites that stored copy. The stored value is
therefore a query index, not the source of truth.

These tests pin both halves. That a record cannot set one and that reads carry the right
answer, as before; and now that the value really is in `data`, that a deliberately
corrupted one never reaches a reader or an export, and that the index can be rebuilt when
a schema change leaves it behind.
"""
import pytest


async def _add_number(client, list_uuid, name):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={"name": name, "display_name": name.title(), "type": "number"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def _add_calculated(client, list_uuid, name, formula):
    return await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": name,
            "display_name": name.title(),
            "type": "calculated",
            "config": {"formula": formula},
        },
    )


@pytest.fixture
async def priced_list(client, list_uuid):
    """A list with `qty` and `price` number columns and a `total` formula."""
    qty = await _add_number(client, list_uuid, "qty")
    price = await _add_number(client, list_uuid, "price")
    resp = await _add_calculated(client, list_uuid, "total", "qty * price")
    assert resp.status_code == 201, resp.text
    total = resp.json()["uuid"]
    return {"list": list_uuid, "qty": qty, "price": price, "total": total}


async def test_read_carries_the_computed_value(client, priced_list):
    lst, qty, price, total = (
        priced_list["list"],
        priced_list["qty"],
        priced_list["price"],
        priced_list["total"],
    )
    resp = await client.post(
        f"/api/lists/{lst}/items/", json={"data": {qty: 3, price: 10}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][total] == 30

    # And on a fresh read of the same record.
    uuid = resp.json()["uuid"]
    got = await client.get(f"/api/lists/{lst}/items/{uuid}")
    assert got.json()["data"][total] == 30


async def test_editing_a_dependency_changes_the_computed_value(client, priced_list):
    lst, qty, price, total = priced_list.values()
    created = await client.post(
        f"/api/lists/{lst}/items/", json={"data": {qty: 3, price: 10}}
    )
    uuid = created.json()["uuid"]
    edited = await client.patch(
        f"/api/lists/{lst}/items/{uuid}", json={"data": {price: 12}}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"][total] == 36


async def test_an_incomplete_row_computes_to_blank(client, priced_list):
    lst, qty, _price, total = (
        priced_list["list"],
        priced_list["qty"],
        priced_list["price"],
        priced_list["total"],
    )
    resp = await client.post(f"/api/lists/{lst}/items/", json={"data": {qty: 3}})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][total] is None


async def test_a_record_cannot_set_a_calculated_cell(client, priced_list):
    lst, total = priced_list["list"], priced_list["total"]
    resp = await client.post(
        f"/api/lists/{lst}/items/", json={"data": {total: 999}}
    )
    assert resp.status_code == 400, resp.text
    assert "calculated" in resp.text.lower()


async def test_formula_referencing_an_unknown_column_is_rejected(client, list_uuid):
    resp = await _add_calculated(client, list_uuid, "bad", "nope * 2")
    assert resp.status_code == 400, resp.text
    assert "unknown column" in resp.text.lower()


async def test_a_text_column_can_be_referenced_and_produce_text(client, list_uuid):
    first = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={"name": "first", "display_name": "First", "type": "text"},
    )
    first_uuid = first.json()["uuid"]
    last = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={"name": "last", "display_name": "Last", "type": "text"},
    )
    last_uuid = last.json()["uuid"]
    calc = await _add_calculated(
        client, list_uuid, "full", "CONCAT(first, ' ', last)"
    )
    assert calc.status_code == 201, calc.text
    full_uuid = calc.json()["uuid"]

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {first_uuid: "Ada", last_uuid: "Lovelace"}},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][full_uuid] == "Ada Lovelace"


async def test_formula_referencing_an_unsupported_type_is_rejected(
    client, list_uuid
):
    # Date columns store ISO strings but are not offered to formulas (v1).
    await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={"name": "due", "display_name": "Due", "type": "date"},
    )
    resp = await _add_calculated(client, list_uuid, "bad", "LEN(due)")
    assert resp.status_code == 400, resp.text
    assert "number, text or calculated" in resp.text.lower()


async def test_a_calculated_column_can_reference_another(client, priced_list):
    # Calc-on-calc, which used to be refused because nothing ordered the evaluation.
    # `chain.build_chain` topologically sorts it now, so `double` reads *this* write's
    # `total` rather than whatever the record arrived holding.
    lst, qty, price = priced_list["list"], priced_list["qty"], priced_list["price"]
    resp = await _add_calculated(client, lst, "double", "total * 2")
    assert resp.status_code == 201, resp.text
    double = resp.json()["uuid"]

    created = await client.post(
        f"/api/lists/{lst}/items/", json={"data": {qty: 3, price: 10}}
    )
    assert created.status_code == 201, created.text
    assert created.json()["data"][double] == 60


async def test_a_chain_settles_in_one_write_whatever_order_the_columns_were_made(
    client, list_uuid
):
    # The dependent is created *before* its operand's formula is, so column order and
    # dependency order disagree. A plan derived from the graph gets this right; one
    # that walked the columns as they came would compute `grand` against the previous
    # write's `sub`, and the record would need saving twice to settle.
    base = await _add_number(client, list_uuid, "base")
    grand = await _add_calculated(client, list_uuid, "grand", "sub + 1")
    assert grand.status_code == 400, "sub does not exist yet"

    sub = await _add_calculated(client, list_uuid, "sub", "base * 2")
    assert sub.status_code == 201, sub.text
    grand = await _add_calculated(client, list_uuid, "grand", "sub + 1")
    assert grand.status_code == 201, grand.text

    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {base: 5}}
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data[sub.json()["uuid"]] == 10
    assert data[grand.json()["uuid"]] == 11


async def test_a_formula_cannot_form_a_cycle(client, list_uuid):
    # A ring is what calc-on-calc opens up, so it is refused at save time and the
    # message names the ring rather than saying "invalid".
    await _add_number(client, list_uuid, "base")
    assert (await _add_calculated(client, list_uuid, "a", "base * 2")).status_code == 201
    assert (await _add_calculated(client, list_uuid, "b", "a + 1")).status_code == 201

    # `a` now wants to read `b`, which reads `a`.
    columns = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    a_uuid = next(c["uuid"] for c in columns if c["name"] == "a")
    resp = await client.patch(
        f"/api/lists/{list_uuid}/columns/{a_uuid}",
        json={"config": {"formula": "b + 1"}},
    )
    assert resp.status_code == 400, resp.text
    assert "a -> b -> a" in resp.text


async def test_a_formula_cannot_reference_itself(client, list_uuid):
    # The one-step cycle. It used to fall out of the type check (a calculated column
    # is not a `number`); now that calc-on-calc is legal it has to be a real ring.
    await _add_number(client, list_uuid, "base")
    assert (await _add_calculated(client, list_uuid, "a", "base * 2")).status_code == 201
    columns = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    a_uuid = next(c["uuid"] for c in columns if c["name"] == "a")

    resp = await client.patch(
        f"/api/lists/{list_uuid}/columns/{a_uuid}",
        json={"config": {"formula": "a + 1"}},
    )
    assert resp.status_code == 400, resp.text
    assert "a -> a" in resp.text


async def test_a_calculated_column_named_like_a_function_can_be_referenced(
    client, list_uuid
):
    # A column whose slug collides with a function name. These used to be reported as
    # functions rather than columns, so nothing checked them: `max * 2` saved happily
    # and then read whichever value the row order happened to produce. The reporting
    # fix stands — `max` is seen as a column — and now that the chain is ordered, the
    # reference it enables is answered correctly rather than refused.
    qty = await _add_number(client, list_uuid, "qty")
    calc = await _add_calculated(client, list_uuid, "max", "qty * 2")
    assert calc.status_code == 201, calc.text

    resp = await _add_calculated(client, list_uuid, "double", "max * 2")
    assert resp.status_code == 201, resp.text

    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {qty: 3}}
    )
    assert created.json()["data"][calc.json()["uuid"]] == 6
    assert created.json()["data"][resp.json()["uuid"]] == 12


async def test_a_calculated_column_cannot_be_unique(client, priced_list):
    # Meaningless while nothing was stored. Now that something is, `_enforce_unique`
    # would start rejecting writes because two rows computed the same answer — a
    # property of the formula, not of anything the writer typed.
    resp = await client.post(
        f"/api/lists/{priced_list['list']}/columns/",
        json={
            "name": "u",
            "display_name": "U",
            "type": "calculated",
            "config": {"formula": "qty * 2", "unique": True},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "unique" in resp.text.lower()


async def test_a_number_column_named_like_a_function_still_works(client, list_uuid):
    # The flip side: `max` is a perfectly good column name, and a formula over it
    # must compute — the fix must not make these names unusable.
    max_uuid = await _add_number(client, list_uuid, "max")
    calc = await _add_calculated(client, list_uuid, "doubled", "max * 2")
    assert calc.status_code == 201, calc.text
    doubled = calc.json()["uuid"]

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {max_uuid: 21}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][doubled] == 42


async def test_a_formula_nested_too_deeply_is_rejected(client, list_uuid):
    # Inside the character limit, but deep enough that walking it used to overflow
    # the stack — saving a 500, or worse, saving fine and then failing every read.
    resp = await _add_calculated(client, list_uuid, "deep", "-" * 900 + "1")
    assert resp.status_code == 400, resp.text
    assert "nest" in resp.text.lower()


def test_a_stored_formula_that_no_longer_parses_blanks_its_own_cell():
    # Built directly rather than over the API, because the API now refuses this
    # formula — the point is a column saved *before* the bounds tightened. Such a
    # column must cost its own cell and nothing else: parsing it while shaping a
    # read used to abort the whole list, so one stale column hid every record.
    from uuid import uuid4

    from src.lists.columns.enums import ColumnType
    from src.lists.columns.models import Column
    from src.lists.items.models import ListItem
    from src.lists.items.services import ListItemService

    qty, stale, live = str(uuid4()), str(uuid4()), str(uuid4())
    columns = {
        qty: Column(name="qty", type=ColumnType.number, config={}),
        stale: Column(
            name="stale",
            type=ColumnType.calculated,
            config={"formula": "-" * 900 + "1"},
        ),
        live: Column(
            name="total", type=ColumnType.calculated, config={"formula": "qty * 2"}
        ),
    }
    item = ListItem(data={qty: 21})

    ListItemService._shape_for_read(columns, [item])

    assert item.data[live] == 42  # the healthy sibling still computes
    assert item.data.get(stale) is None  # and the stale one is simply blank


async def test_an_injection_attempt_in_the_formula_is_rejected(client, list_uuid):
    resp = await _add_calculated(client, list_uuid, "evil", '__import__("os")')
    assert resp.status_code == 400, resp.text


# -- the stored index, and re-indexing it ------------------------------------
#
# A calculated value is stored so Postgres can filter and sort on it, but the stored
# copy is an *index*: every read recomputes and overwrites it. These pin both halves —
# that no reader is ever served a stale value, and that the index can be rebuilt.


async def _corrupt_index(session_factory, key, value="999"):
    """Write a wrong value straight into the record, the way a stale index looks."""
    import sqlalchemy as sa

    async with session_factory() as db:
        await db.execute(
            sa.text(
                f"UPDATE list_items "
                f"SET data = jsonb_set(data, ARRAY[:key], '{value}'::jsonb)"
            ),
            {"key": key},
        )
        await db.commit()


async def test_the_value_is_stored_not_only_computed(
    client, priced_list, session_factory
):
    """The point of the change: there is a real `data ->> key` for Postgres to see."""
    import sqlalchemy as sa

    lst, qty, price, total = priced_list.values()
    await client.post(f"/api/lists/{lst}/items/", json={"data": {qty: 3, price: 10}})

    async with session_factory() as db:
        stored = await db.scalar(
            sa.text("SELECT data ->> :key FROM list_items LIMIT 1"), {"key": total}
        )
    assert stored == "30"


async def test_a_read_recomputes_over_a_stale_stored_value(
    client, priced_list, session_factory
):
    """The load-bearing guarantee. A stale index must never reach a person.

    Both read paths: the single-record fetch and the grid listing.
    """
    lst, qty, price, total = priced_list.values()
    created = await client.post(
        f"/api/lists/{lst}/items/", json={"data": {qty: 3, price: 10}}
    )
    uuid = created.json()["uuid"]
    await _corrupt_index(session_factory, total)

    one = await client.get(f"/api/lists/{lst}/items/{uuid}")
    assert one.json()["data"][total] == 30

    many = await client.get(f"/api/lists/{lst}/items/")
    assert many.json()[0]["data"][total] == 30


async def test_recompute_rebuilds_the_index(client, priced_list, session_factory):
    import sqlalchemy as sa

    lst, qty, price, total = priced_list.values()
    await client.post(f"/api/lists/{lst}/items/", json={"data": {qty: 3, price: 10}})
    await _corrupt_index(session_factory, total)

    resp = await client.post(f"/api/lists/{lst}/columns/recompute")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"records": 1, "deferred": False, "changed": 1}

    async with session_factory() as db:
        stored = await db.scalar(
            sa.text("SELECT data ->> :key FROM list_items LIMIT 1"), {"key": total}
        )
    assert stored == "30"


async def test_recompute_is_idempotent_and_reports_no_change(client, priced_list):
    """Rows that already agree are left alone rather than rewritten with equal bytes."""
    lst, qty, price, _ = priced_list.values()
    await client.post(f"/api/lists/{lst}/items/", json={"data": {qty: 3, price: 10}})

    first = await client.post(f"/api/lists/{lst}/columns/recompute")
    assert first.json()["changed"] == 0  # the write already indexed it
    second = await client.post(f"/api/lists/{lst}/columns/recompute")
    assert second.json()["changed"] == 0


async def test_a_formula_edit_leaves_sorting_behind_until_a_recompute(
    client, priced_list
):
    """The whole shape of the design, end to end.

    The grid is correct the moment the formula changes; the *order* is not, because
    that is decided in Postgres over the stored bytes. A re-index settles it.
    """
    lst, qty, price, total = priced_list.values()
    for q in (1, 2, 3):
        await client.post(f"/api/lists/{lst}/items/", json={"data": {qty: q, price: 10}})

    columns = (await client.get(f"/api/lists/{lst}/columns/")).json()
    total_col = next(c["uuid"] for c in columns if c["name"] == "total")
    # Negate it, so the correct descending order is the exact reverse of the old one.
    edit = await client.patch(
        f"/api/lists/{lst}/columns/{total_col}",
        json={"config": {"formula": "0 - qty * price"}},
    )
    assert edit.status_code == 200, edit.text

    # Values: right immediately, with nothing rebuilt.
    rows = (await client.get(f"/api/lists/{lst}/items/")).json()
    assert sorted(r["data"][total] for r in rows) == [-30, -20, -10]

    # Order: still the old formula's, because the index has not been rebuilt.
    stale = await client.get(
        f"/api/lists/{lst}/items/?sort_by={total}&sort_dir=asc"
    )
    assert [r["data"][total] for r in stale.json()] == [-10, -20, -30]

    assert (await client.post(f"/api/lists/{lst}/columns/recompute")).status_code == 200

    fresh = await client.get(f"/api/lists/{lst}/items/?sort_by={total}&sort_dir=asc")
    assert [r["data"][total] for r in fresh.json()] == [-30, -20, -10]


async def test_a_second_recompute_is_refused_while_one_holds_the_lock(
    client, priced_list, session_factory
):
    """Keyed on the list, not the caller — a second *person* gets the same answer."""
    from src.lists.columns.backfill import try_lock, unlock
    from src.lists.models import List as ListModel
    import sqlalchemy as sa

    lst = priced_list["list"]
    async with session_factory() as db:
        list_id = await db.scalar(
            sa.select(ListModel.id).where(ListModel.uuid == lst)
        )
        assert await try_lock(db, list_id)
        try:
            resp = await client.post(f"/api/lists/{lst}/columns/recompute")
            assert resp.status_code == 409, resp.text
            assert "already running" in resp.text
        finally:
            await unlock(db, list_id)

    # Released, so the next attempt goes through.
    assert (await client.post(f"/api/lists/{lst}/columns/recompute")).status_code == 200


async def test_recompute_defers_above_the_threshold(client, priced_list, monkeypatch):
    from src.core.config import settings

    lst, qty, price, _ = priced_list.values()
    await client.post(f"/api/lists/{lst}/items/", json={"data": {qty: 3, price: 10}})

    monkeypatch.setattr(settings, "FORMULA_INLINE_MAX_RECORDS", 0)
    resp = await client.post(f"/api/lists/{lst}/columns/recompute")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"records": 1, "deferred": True, "changed": None}


async def test_a_partial_reindex_leaves_the_list_readable(
    client, priced_list, session_factory
):
    """Chunks commit independently, so a killed run is half-indexed — not broken.

    Simulated by corrupting one row's index and leaving the other correct, which is
    exactly what a truncated run leaves behind. Reads stay right, and a re-run converges.
    """
    import sqlalchemy as sa

    lst, qty, price, total = priced_list.values()
    for q in (1, 2):
        await client.post(f"/api/lists/{lst}/items/", json={"data": {qty: q, price: 10}})

    async with session_factory() as db:
        await db.execute(
            sa.text(
                "UPDATE list_items SET data = jsonb_set(data, ARRAY[:key], '999'::jsonb)"
                " WHERE id = (SELECT min(id) FROM list_items)"
            ),
            {"key": total},
        )
        await db.commit()

    rows = (await client.get(f"/api/lists/{lst}/items/")).json()
    assert sorted(r["data"][total] for r in rows) == [10, 20]  # both still correct

    assert (await client.post(f"/api/lists/{lst}/columns/recompute")).json()["changed"] == 1
    assert (await client.post(f"/api/lists/{lst}/columns/recompute")).json()["changed"] == 0


async def test_filter_and_search_match_a_calculated_column(client, priced_list):
    """All three used to fail silently rather than error.

    `data ->> key` was NULL for every row, so `is`/`contains`/comparators matched
    nothing and `is_empty` matched everything — the worst kind of wrong, because it
    looks like an answer.
    """
    import json

    lst, qty, price, total = priced_list.values()
    for q in (1, 2, 3):
        await client.post(f"/api/lists/{lst}/items/", json={"data": {qty: q, price: 10}})
    # One row the formula cannot answer for, so `is_empty` has something real to find.
    await client.post(f"/api/lists/{lst}/items/", json={"data": {qty: 9}})

    def where(op, value=None):
        cond = {"column_uuid": total, "operator": op}
        if value is not None:
            cond["value"] = value
        return json.dumps({"conjunction": "and", "conditions": [cond]})

    exact = await client.get(
        f"/api/lists/{lst}/items/", params={"filters": where("is", 20)}
    )
    assert [r["data"][qty] for r in exact.json()] == [2]

    above = await client.get(
        f"/api/lists/{lst}/items/", params={"filters": where("gt", 15)}
    )
    assert sorted(r["data"][qty] for r in above.json()) == [2, 3]

    blank = await client.get(
        f"/api/lists/{lst}/items/", params={"filters": where("is_empty")}
    )
    assert [r["data"][qty] for r in blank.json()] == [9]

    found = await client.get(f"/api/lists/{lst}/items/", params={"q": "30"})
    assert [r["data"][qty] for r in found.json()] == [3]


async def test_a_text_formula_is_searchable(client, list_uuid):
    """Search matches the computed text, not just the columns it was built from."""
    first = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={"name": "first", "display_name": "First", "type": "text"},
    )
    first_uuid = first.json()["uuid"]
    calc = await _add_calculated(client, list_uuid, "shout", "UPPER(first)")
    shout = calc.json()["uuid"]

    for name in ("ada", "grace"):
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {first_uuid: name}}
        )

    # "ADA" appears only in the computed cell — the source column holds "ada".
    found = await client.get(f"/api/lists/{list_uuid}/items/?q=ADA")
    assert len(found.json()) == 1
    assert found.json()[0]["data"][shout] == "ADA"


async def test_an_exported_file_imports_straight_back(client, priced_list):
    """The reason the export used to leave calculated columns out.

    The file now carries the computed value, so the round trip has to be pinned: the
    formula rewrites the cell on the way in and the value in the file is ignored.
    """
    lst, qty, price, total = priced_list.values()
    await client.post(f"/api/lists/{lst}/items/", json={"data": {qty: 3, price: 10}})

    exported = await client.post(f"/api/lists/{lst}/export", json={"format": "csv"})
    assert exported.status_code == 200, exported.text
    csv_text = exported.text
    assert "total" in csv_text.splitlines()[0]
    assert "30" in csv_text

    back = await client.post(
        f"/api/lists/{lst}/import",
        files={"file": ("rows.csv", csv_text, "text/csv")},
    )
    assert back.status_code == 200, back.text
    assert back.json() == {"created": 1, "errors": []}

    rows = (await client.get(f"/api/lists/{lst}/items/")).json()
    assert [r["data"][total] for r in rows] == [30, 30]


async def test_grouping_by_a_calculated_column_makes_its_rows_adjacent(
    client, priced_list
):
    """Group-by is a sort, so it goes through the same two ORDER BY terms.

    It used to be dropped along with every other calculated sort term, which left
    the grid's group headings falling wherever insertion order put them.
    """
    lst, qty, price, total = priced_list.values()
    for q in (2, 1, 2, 1):
        await client.post(f"/api/lists/{lst}/items/", json={"data": {qty: q, price: 10}})

    grouped = await client.get(
        f"/api/lists/{lst}/items/", params={"group_by": total}
    )
    assert [r["data"][total] for r in grouped.json()] == [10, 10, 20, 20]
