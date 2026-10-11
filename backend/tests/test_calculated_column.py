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


async def _add_number(client, table_uuid, name):
    resp = await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={"name": name, "display_name": name.title(), "type": "number"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def _add_calculated(client, table_uuid, name, formula):
    return await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={
            "name": name,
            "display_name": name.title(),
            "type": "calculated",
            "config": {"formula": formula},
        },
    )


@pytest.fixture
async def priced_table(client, table_uuid):
    """A table with `qty` and `price` number columns and a `total` formula."""
    qty = await _add_number(client, table_uuid, "qty")
    price = await _add_number(client, table_uuid, "price")
    resp = await _add_calculated(client, table_uuid, "total", "qty * price")
    assert resp.status_code == 201, resp.text
    total = resp.json()["uuid"]
    return {"table": table_uuid, "qty": qty, "price": price, "total": total}


async def test_read_carries_the_computed_value(client, priced_table):
    table, qty, price, total = (
        priced_table["table"],
        priced_table["qty"],
        priced_table["price"],
        priced_table["total"],
    )
    resp = await client.post(
        f"/api/tables/{table}/records/", json={"data": {qty: 3, price: 10}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][total] == 30

    # And on a fresh read of the same record.
    uuid = resp.json()["uuid"]
    got = await client.get(f"/api/tables/{table}/records/{uuid}")
    assert got.json()["data"][total] == 30


async def test_editing_a_dependency_changes_the_computed_value(client, priced_table):
    table, qty, price, total = priced_table.values()
    created = await client.post(
        f"/api/tables/{table}/records/", json={"data": {qty: 3, price: 10}}
    )
    uuid = created.json()["uuid"]
    edited = await client.patch(
        f"/api/tables/{table}/records/{uuid}", json={"data": {price: 12}}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"][total] == 36


async def test_an_incomplete_row_computes_to_blank(client, priced_table):
    table, qty, _price, total = (
        priced_table["table"],
        priced_table["qty"],
        priced_table["price"],
        priced_table["total"],
    )
    resp = await client.post(f"/api/tables/{table}/records/", json={"data": {qty: 3}})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][total] is None


async def test_a_record_cannot_set_a_calculated_cell(client, priced_table):
    table, total = priced_table["table"], priced_table["total"]
    resp = await client.post(
        f"/api/tables/{table}/records/", json={"data": {total: 999}}
    )
    assert resp.status_code == 400, resp.text
    assert "calculated" in resp.text.lower()


async def test_formula_referencing_an_unknown_column_is_rejected(client, table_uuid):
    resp = await _add_calculated(client, table_uuid, "bad", "nope * 2")
    assert resp.status_code == 400, resp.text
    assert "unknown column" in resp.text.lower()


async def test_a_text_column_can_be_referenced_and_produce_text(client, table_uuid):
    first = await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={"name": "first", "display_name": "First", "type": "text"},
    )
    first_uuid = first.json()["uuid"]
    last = await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={"name": "last", "display_name": "Last", "type": "text"},
    )
    last_uuid = last.json()["uuid"]
    calc = await _add_calculated(
        client, table_uuid, "full", "CONCAT(first, ' ', last)"
    )
    assert calc.status_code == 201, calc.text
    full_uuid = calc.json()["uuid"]

    resp = await client.post(
        f"/api/tables/{table_uuid}/records/",
        json={"data": {first_uuid: "Ada", last_uuid: "Lovelace"}},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][full_uuid] == "Ada Lovelace"


async def test_formula_referencing_an_unsupported_type_is_rejected(
    client, table_uuid
):
    # A tick box has no text form worth computing over, so it stays outside
    # FORMULA_REFERENCEABLE_TYPES along with `link` and everything that stores an id.
    await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={"name": "paid", "display_name": "Paid", "type": "checkbox"},
    )
    resp = await _add_calculated(client, table_uuid, "bad", "LEN(paid)")
    assert resp.status_code == 400, resp.text
    # The message is built from the set, so it tables what *is* allowed rather than
    # a hardcoded phrase that could outlive the rule.
    assert "is checkbox" in resp.text
    for allowed in ("number", "text", "calculated", "choice", "date", "datetime"):
        assert allowed in resp.text


async def test_a_calculated_column_can_reference_another(client, priced_table):
    # Calc-on-calc, which used to be refused because nothing ordered the evaluation.
    # `chain.build_chain` topologically sorts it now, so `double` reads *this* write's
    # `total` rather than whatever the record arrived holding.
    table, qty, price = priced_table["table"], priced_table["qty"], priced_table["price"]
    resp = await _add_calculated(client, table, "double", "total * 2")
    assert resp.status_code == 201, resp.text
    double = resp.json()["uuid"]

    created = await client.post(
        f"/api/tables/{table}/records/", json={"data": {qty: 3, price: 10}}
    )
    assert created.status_code == 201, created.text
    assert created.json()["data"][double] == 60


async def test_a_chain_settles_in_one_write_whatever_order_the_columns_were_made(
    client, table_uuid
):
    # The dependent is created *before* its operand's formula is, so column order and
    # dependency order disagree. A plan derived from the graph gets this right; one
    # that walked the columns as they came would compute `grand` against the previous
    # write's `sub`, and the record would need saving twice to settle.
    base = await _add_number(client, table_uuid, "base")
    grand = await _add_calculated(client, table_uuid, "grand", "sub + 1")
    assert grand.status_code == 400, "sub does not exist yet"

    sub = await _add_calculated(client, table_uuid, "sub", "base * 2")
    assert sub.status_code == 201, sub.text
    grand = await _add_calculated(client, table_uuid, "grand", "sub + 1")
    assert grand.status_code == 201, grand.text

    created = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {base: 5}}
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data[sub.json()["uuid"]] == 10
    assert data[grand.json()["uuid"]] == 11


async def test_a_formula_cannot_form_a_cycle(client, table_uuid):
    # A ring is what calc-on-calc opens up, so it is refused at save time and the
    # message names the ring rather than saying "invalid".
    await _add_number(client, table_uuid, "base")
    assert (await _add_calculated(client, table_uuid, "a", "base * 2")).status_code == 201
    assert (await _add_calculated(client, table_uuid, "b", "a + 1")).status_code == 201

    # `a` now wants to read `b`, which reads `a`.
    columns = (await client.get(f"/api/tables/{table_uuid}/columns/")).json()
    a_uuid = next(c["uuid"] for c in columns if c["name"] == "a")
    resp = await client.patch(
        f"/api/tables/{table_uuid}/columns/{a_uuid}",
        json={"config": {"formula": "b + 1"}},
    )
    assert resp.status_code == 400, resp.text
    assert "a -> b -> a" in resp.text


async def test_a_formula_cannot_reference_itself(client, table_uuid):
    # The one-step cycle. It used to fall out of the type check (a calculated column
    # is not a `number`); now that calc-on-calc is legal it has to be a real ring.
    await _add_number(client, table_uuid, "base")
    assert (await _add_calculated(client, table_uuid, "a", "base * 2")).status_code == 201
    columns = (await client.get(f"/api/tables/{table_uuid}/columns/")).json()
    a_uuid = next(c["uuid"] for c in columns if c["name"] == "a")

    resp = await client.patch(
        f"/api/tables/{table_uuid}/columns/{a_uuid}",
        json={"config": {"formula": "a + 1"}},
    )
    assert resp.status_code == 400, resp.text
    assert "a -> a" in resp.text


async def test_a_calculated_column_named_like_a_function_can_be_referenced(
    client, table_uuid
):
    # A column whose slug collides with a function name. These used to be reported as
    # functions rather than columns, so nothing checked them: `max * 2` saved happily
    # and then read whichever value the row order happened to produce. The reporting
    # fix stands — `max` is seen as a column — and now that the chain is ordered, the
    # reference it enables is answered correctly rather than refused.
    qty = await _add_number(client, table_uuid, "qty")
    calc = await _add_calculated(client, table_uuid, "max", "qty * 2")
    assert calc.status_code == 201, calc.text

    resp = await _add_calculated(client, table_uuid, "double", "max * 2")
    assert resp.status_code == 201, resp.text

    created = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {qty: 3}}
    )
    assert created.json()["data"][calc.json()["uuid"]] == 6
    assert created.json()["data"][resp.json()["uuid"]] == 12


async def test_a_calculated_column_can_be_unique(client, priced_table):
    # A formula is how you express a key no single column holds. It works because
    # `_validate_data` writes the computed cell before `_enforce_unique` reads it,
    # so the check sees this write's answer rather than the previous one.
    table, qty, price = priced_table["table"], priced_table["qty"], priced_table["price"]
    resp = await client.post(
        f"/api/tables/{table}/columns/",
        json={
            "name": "key",
            "display_name": "Key",
            "type": "calculated",
            "config": {"formula": "qty * price", "unique": True},
        },
    )
    assert resp.status_code == 201, resp.text

    first = await client.post(
        f"/api/tables/{table}/records/", json={"data": {qty: 3, price: 10}}
    )
    assert first.status_code == 201, first.text

    # Different operands, same answer — which is exactly what a unique formula is
    # asked to refuse, and the message says so rather than blaming a typed value.
    clash = await client.post(
        f"/api/tables/{table}/records/", json={"data": {qty: 5, price: 6}}
    )
    assert clash.status_code == 409, clash.text
    assert "computes" in clash.text.lower()

    # A different answer still saves.
    ok = await client.post(
        f"/api/tables/{table}/records/", json={"data": {qty: 5, price: 7}}
    )
    assert ok.status_code == 201, ok.text


async def test_editing_into_a_taken_answer_is_refused(client, priced_table):
    # The collision an edit causes: nothing about the *unique* column was touched,
    # so this is the case the wording has to survive.
    table, qty, price = priced_table["table"], priced_table["qty"], priced_table["price"]
    resp = await client.post(
        f"/api/tables/{table}/columns/",
        json={
            "name": "key",
            "display_name": "Key",
            "type": "calculated",
            "config": {"formula": "qty * price", "unique": True},
        },
    )
    assert resp.status_code == 201, resp.text

    await client.post(f"/api/tables/{table}/records/", json={"data": {qty: 3, price: 10}})
    other = await client.post(
        f"/api/tables/{table}/records/", json={"data": {qty: 4, price: 10}}
    )
    uuid = other.json()["uuid"]

    clash = await client.patch(
        f"/api/tables/{table}/records/{uuid}", json={"data": {qty: 3}}
    )
    assert clash.status_code == 409, clash.text
    assert "key" in clash.text

    # And a record does not collide with itself: re-saving the same operands is fine.
    same = await client.patch(
        f"/api/tables/{table}/records/{uuid}", json={"data": {qty: 4}}
    )
    assert same.status_code == 200, same.text


async def test_a_blank_computed_value_does_not_collide(client, priced_table):
    # `_enforce_unique` skips empty values, and an incomplete row computes to blank —
    # so a unique formula must not make "not filled in yet" a one-record-only state.
    table, qty = priced_table["table"], priced_table["qty"]
    resp = await client.post(
        f"/api/tables/{table}/columns/",
        json={
            "name": "key",
            "display_name": "Key",
            "type": "calculated",
            "config": {"formula": "qty * price", "unique": True},
        },
    )
    assert resp.status_code == 201, resp.text

    first = await client.post(f"/api/tables/{table}/records/", json={"data": {qty: 3}})
    assert first.status_code == 201, first.text
    second = await client.post(f"/api/tables/{table}/records/", json={"data": {qty: 4}})
    assert second.status_code == 201, second.text


async def test_a_calculated_column_still_cannot_have_a_default(client, priced_table):
    # The other half of the pair, and the reason they part company: a default is a
    # value somebody supplies, and the formula would overwrite it on the same write.
    resp = await client.post(
        f"/api/tables/{priced_table['table']}/columns/",
        json={
            "name": "d",
            "display_name": "D",
            "type": "calculated",
            "config": {"formula": "qty * 2", "default": 5},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "default" in resp.text.lower()


async def test_a_number_column_named_like_a_function_still_works(client, table_uuid):
    # The flip side: `max` is a perfectly good column name, and a formula over it
    # must compute — the fix must not make these names unusable.
    max_uuid = await _add_number(client, table_uuid, "max")
    calc = await _add_calculated(client, table_uuid, "doubled", "max * 2")
    assert calc.status_code == 201, calc.text
    doubled = calc.json()["uuid"]

    resp = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {max_uuid: 21}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][doubled] == 42


async def test_a_formula_nested_too_deeply_is_rejected(client, table_uuid):
    # Inside the character limit, but deep enough that walking it used to overflow
    # the stack — saving a 500, or worse, saving fine and then failing every read.
    resp = await _add_calculated(client, table_uuid, "deep", "-" * 900 + "1")
    assert resp.status_code == 400, resp.text
    assert "nest" in resp.text.lower()


def test_a_stored_formula_that_no_longer_parses_blanks_its_own_cell():
    # Built directly rather than over the API, because the API now refuses this
    # formula — the point is a column saved *before* the bounds tightened. Such a
    # column must cost its own cell and nothing else: parsing it while shaping a
    # read used to abort the whole table, so one stale column hid every record.
    from uuid import uuid4

    from src.tables.columns.enums import ColumnType
    from src.tables.columns.models import Column
    from src.tables.records.models import TableRecord
    from src.tables.records.services import TableRecordService

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
    record = TableRecord(data={qty: 21})

    TableRecordService._shape_for_read(columns, [record])

    assert record.data[live] == 42  # the healthy sibling still computes
    assert record.data.get(stale) is None  # and the stale one is simply blank


async def test_an_injection_attempt_in_the_formula_is_rejected(client, table_uuid):
    resp = await _add_calculated(client, table_uuid, "evil", '__import__("os")')
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
                f"UPDATE table_records "
                f"SET data = jsonb_set(data, ARRAY[:key], '{value}'::jsonb)"
            ),
            {"key": key},
        )
        await db.commit()


async def test_the_value_is_stored_not_only_computed(
    client, priced_table, session_factory
):
    """The point of the change: there is a real `data ->> key` for Postgres to see."""
    import sqlalchemy as sa

    table, qty, price, total = priced_table.values()
    await client.post(f"/api/tables/{table}/records/", json={"data": {qty: 3, price: 10}})

    async with session_factory() as db:
        stored = await db.scalar(
            sa.text("SELECT data ->> :key FROM table_records LIMIT 1"), {"key": total}
        )
    assert stored == "30"


async def test_a_read_recomputes_over_a_stale_stored_value(
    client, priced_table, session_factory
):
    """The load-bearing guarantee. A stale index must never reach a person.

    Both read paths: the single-record fetch and the grid listing.
    """
    table, qty, price, total = priced_table.values()
    created = await client.post(
        f"/api/tables/{table}/records/", json={"data": {qty: 3, price: 10}}
    )
    uuid = created.json()["uuid"]
    await _corrupt_index(session_factory, total)

    one = await client.get(f"/api/tables/{table}/records/{uuid}")
    assert one.json()["data"][total] == 30

    many = await client.get(f"/api/tables/{table}/records/")
    assert many.json()[0]["data"][total] == 30


async def test_recompute_rebuilds_the_index(client, priced_table, session_factory):
    import sqlalchemy as sa

    table, qty, price, total = priced_table.values()
    await client.post(f"/api/tables/{table}/records/", json={"data": {qty: 3, price: 10}})
    await _corrupt_index(session_factory, total)

    resp = await client.post(f"/api/tables/{table}/columns/recompute")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"records": 1, "deferred": False, "changed": 1}

    async with session_factory() as db:
        stored = await db.scalar(
            sa.text("SELECT data ->> :key FROM table_records LIMIT 1"), {"key": total}
        )
    assert stored == "30"


async def test_recompute_is_idempotent_and_reports_no_change(client, priced_table):
    """Rows that already agree are left alone rather than rewritten with equal bytes."""
    table, qty, price, _ = priced_table.values()
    await client.post(f"/api/tables/{table}/records/", json={"data": {qty: 3, price: 10}})

    first = await client.post(f"/api/tables/{table}/columns/recompute")
    assert first.json()["changed"] == 0  # the write already indexed it
    second = await client.post(f"/api/tables/{table}/columns/recompute")
    assert second.json()["changed"] == 0


async def test_a_formula_edit_leaves_sorting_behind_until_a_recompute(
    client, priced_table
):
    """The whole shape of the design, end to end.

    The grid is correct the moment the formula changes; the *order* is not, because
    that is decided in Postgres over the stored bytes. A re-index settles it.
    """
    table, qty, price, total = priced_table.values()
    for q in (1, 2, 3):
        await client.post(f"/api/tables/{table}/records/", json={"data": {qty: q, price: 10}})

    columns = (await client.get(f"/api/tables/{table}/columns/")).json()
    total_col = next(c["uuid"] for c in columns if c["name"] == "total")
    # Negate it, so the correct descending order is the exact reverse of the old one.
    edit = await client.patch(
        f"/api/tables/{table}/columns/{total_col}",
        json={"config": {"formula": "0 - qty * price"}},
    )
    assert edit.status_code == 200, edit.text

    # Values: right immediately, with nothing rebuilt.
    rows = (await client.get(f"/api/tables/{table}/records/")).json()
    assert sorted(r["data"][total] for r in rows) == [-30, -20, -10]

    # Order: still the old formula's, because the index has not been rebuilt.
    stale = await client.get(
        f"/api/tables/{table}/records/?sort_by={total}&sort_dir=asc"
    )
    assert [r["data"][total] for r in stale.json()] == [-10, -20, -30]

    assert (await client.post(f"/api/tables/{table}/columns/recompute")).status_code == 200

    fresh = await client.get(f"/api/tables/{table}/records/?sort_by={total}&sort_dir=asc")
    assert [r["data"][total] for r in fresh.json()] == [-30, -20, -10]


async def test_a_second_recompute_is_refused_while_one_holds_the_lock(
    client, priced_table, session_factory
):
    """Keyed on the table, not the caller — a second *person* gets the same answer."""
    from src.tables.columns.backfill import try_lock, unlock
    from src.tables.models import Table
    import sqlalchemy as sa

    table = priced_table["table"]
    async with session_factory() as db:
        table_id = await db.scalar(
            sa.select(Table.id).where(Table.uuid == table)
        )
        assert await try_lock(db, table_id)
        try:
            resp = await client.post(f"/api/tables/{table}/columns/recompute")
            assert resp.status_code == 409, resp.text
            assert "already running" in resp.text
        finally:
            await unlock(db, table_id)

    # Released, so the next attempt goes through.
    assert (await client.post(f"/api/tables/{table}/columns/recompute")).status_code == 200


async def test_recompute_defers_above_the_threshold(client, priced_table, monkeypatch):
    from src.core.config import settings

    table, qty, price, _ = priced_table.values()
    await client.post(f"/api/tables/{table}/records/", json={"data": {qty: 3, price: 10}})

    monkeypatch.setattr(settings, "FORMULA_INLINE_MAX_RECORDS", 0)
    resp = await client.post(f"/api/tables/{table}/columns/recompute")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"records": 1, "deferred": True, "changed": None}


async def test_a_partial_reindex_leaves_the_table_readable(
    client, priced_table, session_factory
):
    """Chunks commit independently, so a killed run is half-indexed — not broken.

    Simulated by corrupting one row's index and leaving the other correct, which is
    exactly what a truncated run leaves behind. Reads stay right, and a re-run converges.
    """
    import sqlalchemy as sa

    table, qty, price, total = priced_table.values()
    for q in (1, 2):
        await client.post(f"/api/tables/{table}/records/", json={"data": {qty: q, price: 10}})

    async with session_factory() as db:
        await db.execute(
            sa.text(
                "UPDATE table_records SET data = jsonb_set(data, ARRAY[:key], '999'::jsonb)"
                " WHERE id = (SELECT min(id) FROM table_records)"
            ),
            {"key": total},
        )
        await db.commit()

    rows = (await client.get(f"/api/tables/{table}/records/")).json()
    assert sorted(r["data"][total] for r in rows) == [10, 20]  # both still correct

    assert (await client.post(f"/api/tables/{table}/columns/recompute")).json()["changed"] == 1
    assert (await client.post(f"/api/tables/{table}/columns/recompute")).json()["changed"] == 0


async def test_filter_and_search_match_a_calculated_column(client, priced_table):
    """All three used to fail silently rather than error.

    `data ->> key` was NULL for every row, so `is`/`contains`/comparators matched
    nothing and `is_empty` matched everything — the worst kind of wrong, because it
    looks like an answer.
    """
    import json

    table, qty, price, total = priced_table.values()
    # A price of 1000 rather than 10, so the totals are four-digit and the search
    # below cannot be answered by the clock. `_apply_search` matches its term
    # against *every* column, and every table carries `created_at` and `updated_at`
    # as ordinary datetime columns holding an ISO string -- so a search for "30"
    # also finds any record stamped at 18:30, or at :30 seconds, or on the 30th.
    # Which made this test fail for two minutes in every hour, and only inside a
    # run long enough to cross one. An ISO timestamp's only four-digit run is its
    # year, so "3000" can match nothing but the total it is looking for.
    for q in (1, 2, 3):
        await client.post(
            f"/api/tables/{table}/records/", json={"data": {qty: q, price: 1000}}
        )
    # One row the formula cannot answer for, so `is_empty` has something real to find.
    await client.post(f"/api/tables/{table}/records/", json={"data": {qty: 9}})

    def where(op, value=None):
        cond = {"column_uuid": total, "operator": op}
        if value is not None:
            cond["value"] = value
        return json.dumps({"conjunction": "and", "conditions": [cond]})

    exact = await client.get(
        f"/api/tables/{table}/records/", params={"filters": where("is", 2000)}
    )
    assert [r["data"][qty] for r in exact.json()] == [2]

    above = await client.get(
        f"/api/tables/{table}/records/", params={"filters": where("gt", 1500)}
    )
    assert sorted(r["data"][qty] for r in above.json()) == [2, 3]

    blank = await client.get(
        f"/api/tables/{table}/records/", params={"filters": where("is_empty")}
    )
    assert [r["data"][qty] for r in blank.json()] == [9]

    found = await client.get(f"/api/tables/{table}/records/", params={"q": "3000"})
    assert [r["data"][qty] for r in found.json()] == [3]


async def test_a_text_formula_is_searchable(client, table_uuid):
    """Search matches the computed text, not just the columns it was built from."""
    first = await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={"name": "first", "display_name": "First", "type": "text"},
    )
    first_uuid = first.json()["uuid"]
    calc = await _add_calculated(client, table_uuid, "shout", "UPPER(first)")
    shout = calc.json()["uuid"]

    for name in ("ada", "grace"):
        await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {first_uuid: name}}
        )

    # "ADA" appears only in the computed cell — the source column holds "ada".
    found = await client.get(f"/api/tables/{table_uuid}/records/?q=ADA")
    assert len(found.json()) == 1
    assert found.json()[0]["data"][shout] == "ADA"


async def test_an_exported_file_imports_straight_back(client, priced_table):
    """The reason the export used to leave calculated columns out.

    The file now carries the computed value, so the round trip has to be pinned: the
    formula rewrites the cell on the way in and the value in the file is ignored.
    """
    table, qty, price, total = priced_table.values()
    await client.post(f"/api/tables/{table}/records/", json={"data": {qty: 3, price: 10}})

    exported = await client.post(f"/api/tables/{table}/export", json={"format": "csv"})
    assert exported.status_code == 200, exported.text
    csv_text = exported.text
    assert "total" in csv_text.splitlines()[0]
    assert "30" in csv_text

    back = await client.post(
        f"/api/tables/{table}/import",
        files={"file": ("rows.csv", csv_text, "text/csv")},
    )
    assert back.status_code == 200, back.text
    assert back.json() == {"created": 1, "errors": []}

    rows = (await client.get(f"/api/tables/{table}/records/")).json()
    assert [r["data"][total] for r in rows] == [30, 30]


async def test_grouping_by_a_calculated_column_makes_its_rows_adjacent(
    client, priced_table
):
    """Group-by is a sort, so it goes through the same two ORDER BY terms.

    It used to be dropped along with every other calculated sort term, which left
    the grid's group headings falling wherever insertion order put them.
    """
    table, qty, price, total = priced_table.values()
    for q in (2, 1, 2, 1):
        await client.post(f"/api/tables/{table}/records/", json={"data": {qty: q, price: 10}})

    grouped = await client.get(
        f"/api/tables/{table}/records/", params={"group_by": total}
    )
    assert [r["data"][total] for r in grouped.json()] == [10, 10, 20, 20]


# -- the types a formula may name, beyond number and text ------------------------


async def test_a_choice_column_can_be_referenced_as_its_label(
    client, table_uuid, add_column
):
    """A choice cell stores the label itself, so it needs no resolving to be text.

    It used to be refused on the grounds that it stored an id, which stopped being
    true at the `choice_options_store_labels` migration.
    """
    status = await add_column(
        table_uuid,
        name="status",
        display_name="Status",
        type="choice",
        config={"choices": "Todo;Doing;Done"},
    )
    calc = await _add_calculated(
        client, table_uuid, "flag", "IF(status == 'Done', 'Finished', UPPER(status))"
    )
    assert calc.status_code == 201, calc.text
    flag = calc.json()["uuid"]

    done = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {status: "Done"}}
    )
    assert done.json()["data"][flag] == "Finished"
    doing = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {status: "Doing"}}
    )
    assert doing.json()["data"][flag] == "DOING"


async def test_a_multiple_choice_column_reads_as_its_labels_joined(
    client, table_uuid, add_column
):
    # Created through the API because the column dialog does not yet offer
    # `multiple` for a choice column.
    programs = await add_column(
        table_uuid,
        name="programs",
        display_name="Programs",
        type="choice",
        config={"choices": "Robotics;Choir;Chess", "multiple": True},
    )
    calc = await _add_calculated(
        client, table_uuid, "summary", "CONCAT('In: ', programs)"
    )
    assert calc.status_code == 201, calc.text
    summary = calc.json()["uuid"]

    resp = await client.post(
        f"/api/tables/{table_uuid}/records/",
        json={"data": {programs: ["Robotics", "Choir"]}},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][summary] == "In: Robotics, Choir"

    # No selection is an empty cell, so the formula has nothing to answer with.
    empty = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {programs: []}}
    )
    assert empty.json()["data"][summary] is None


async def test_age_over_a_date_column(client, table_uuid, add_column):
    from datetime import date

    born = await add_column(
        table_uuid, name="born", display_name="Born", type="date"
    )
    calc = await _add_calculated(client, table_uuid, "years", "AGE(born)")
    assert calc.status_code == 201, calc.text
    years = calc.json()["uuid"]

    # Computed against the real clock rather than a pinned one — this test is about
    # a date column reaching the evaluator at all, and `test_formula.py` pins the
    # arithmetic. A date exactly N years ago is N whatever day it runs, except on a
    # 29 February, which is why the fixture date is the 1st.
    today = date.today()
    long_ago = date(today.year - 30, 3, 1) if today.month > 3 else date(
        today.year - 31, 3, 1
    )
    resp = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {born: long_ago.isoformat()}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][years] == today.year - long_ago.year - (
        (today.month, today.day) < (3, 1)
    )


async def test_dt_format_over_a_datetime_column(client, table_uuid, add_column):
    enrolled = await add_column(
        table_uuid, name="enrolled", display_name="Enrolled", type="datetime"
    )
    calc = await _add_calculated(
        client, table_uuid, "pretty", "DT_FORMAT(enrolled, '%d %b %Y at %H:%M')"
    )
    assert calc.status_code == 201, calc.text
    pretty = calc.json()["uuid"]

    resp = await client.post(
        f"/api/tables/{table_uuid}/records/",
        json={"data": {enrolled: "2026-08-06T09:30:00"}},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][pretty] == "06 Aug 2026 at 09:30"


async def test_a_written_out_format_that_makes_no_sense_is_refused_at_save(
    client, table_uuid, add_column
):
    await add_column(table_uuid, name="due", display_name="Due", type="date")
    resp = await _add_calculated(client, table_uuid, "bad", "DT_FORMAT(due, '%q')")
    assert resp.status_code == 400, resp.text
    assert "%q" in resp.text


async def test_a_formula_that_only_reads_the_clock_references_no_column(
    client, table_uuid
):
    """`TODAY()`/`NOW()` are the one shape that names nothing, and it still saves.

    Every other formula references a column, so the save path's "does each of these
    exist, and may a formula read it" loop always had something to walk. These hand
    it an empty set, and a record with no data at all still gets a value.
    """
    from datetime import datetime, timezone

    calc = await _add_calculated(
        client, table_uuid, "stamp", "CONCAT('as of ', TODAY())"
    )
    assert calc.status_code == 201, calc.text
    stamp = calc.json()["uuid"]

    resp = await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {}})
    assert resp.status_code == 201, resp.text
    # Against the real clock, as `test_age_over_a_date_column` is: `test_formula.py`
    # pins the arithmetic and this is about the answer reaching a record at all. UTC
    # because that is the zone the evaluator reads, which is not the runner's.
    today = datetime.now(timezone.utc).date().isoformat()
    assert resp.json()["data"][stamp] == f"as of {today}"


async def test_today_compares_against_a_date_column(client, table_uuid, add_column):
    """What the function is for: a cell that says whether this row is past its date."""
    due = await add_column(table_uuid, name="due", display_name="Due", type="date")
    calc = await _add_calculated(
        client, table_uuid, "state", "IF(due < TODAY(), 'Overdue', 'Open')"
    )
    assert calc.status_code == 201, calc.text
    state = calc.json()["uuid"]

    # Decades either side, so which side of midnight the suite runs on cannot matter.
    for value, expected in (("2000-01-01", "Overdue"), ("2099-01-01", "Open")):
        resp = await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {due: value}}
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"][state] == expected

    # An empty date is an empty answer, like every other missing operand.
    blank = await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {}})
    assert blank.json()["data"][state] is None


async def test_int_makes_a_text_code_filterable_as_a_number(
    client, table_uuid, add_column
):
    """The cast reaches the stored index, not just the screen.

    A number written into a text column sorts and filters as text — "9" after "10" —
    until something reads it as a number. `INT` is that something, and because the
    answer is stored under the calculated column's uuid, Postgres can compare it.
    """
    import json

    code = await add_column(table_uuid, name="code", display_name="Code", type="text")
    calc = await _add_calculated(client, table_uuid, "num", "INT(code)")
    assert calc.status_code == 201, calc.text
    num = calc.json()["uuid"]

    # Inserted out of order, so a real sort is distinguishable from insertion order —
    # and "100" before "9" is exactly the pair that text ordering gets wrong.
    for raw in ("100", "9", "not a number", "10"):
        resp = await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {code: raw}}
        )
        assert resp.status_code == 201, resp.text

    filters = json.dumps(
        {
            "conjunction": "and",
            "conditions": [{"column_uuid": num, "operator": "gt", "value": 9}],
        }
    )
    above = await client.get(f"/api/tables/{table_uuid}/records/", params={"filters": filters})
    assert sorted(r["data"][num] for r in above.json()) == [10, 100]

    ordered = await client.get(
        f"/api/tables/{table_uuid}/records/", params={"sort_by": num}
    )
    # The unparseable row has no value; the rest come back in numeric order.
    assert [r["data"][num] for r in ordered.json() if r["data"][num] is not None] == [
        9,
        10,
        100,
    ]
