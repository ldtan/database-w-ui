"""The `calculated` column type, over the HTTP API.

A calculated column holds a formula over other columns of its list, and its value is
**stored**: written from the formula on create and on every update, and read back like
any other cell. These tests pin the three things that follow from that — a record still
cannot set one, the stored value stays in step with edits to the schema as well as to
the record, and the column filters and sorts like the rest of the list.

They also cover the chain: a formula may reference another calculated column, the order
they compute in is settled by `compute_order`, and a loop is refused.
"""
import pytest


async def _add_number(client, list_uuid, name):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={"name": name, "display_name": name.title(), "type": "number"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def _add_calculated(client, list_uuid, name, formula, **config):
    return await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": name,
            "display_name": name.title(),
            "type": "calculated",
            "config": {"formula": formula, **config},
        },
    )


async def _rows(client, list_uuid, **params):
    resp = await client.get(f"/api/lists/{list_uuid}/items/", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
async def priced_list(client, list_uuid):
    """A list with `qty` and `price` number columns and a `total` formula."""
    qty = await _add_number(client, list_uuid, "qty")
    price = await _add_number(client, list_uuid, "price")
    resp = await _add_calculated(client, list_uuid, "total", "qty * price")
    assert resp.status_code == 201, resp.text
    total = resp.json()["uuid"]
    return {"list": list_uuid, "qty": qty, "price": price, "total": total}


# -- the value, and where it lives ------------------------------------------


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


async def test_the_value_is_stored_not_computed_on_read(
    client, priced_list, session_factory
):
    """The point of the whole change: Postgres can see the value.

    Read straight off the row rather than through the API, because everything the API
    returns would look the same whether the value was stored or injected on the way
    out — and only a stored one can be filtered, sorted or grouped in SQL.
    """
    from sqlalchemy import select

    from src.lists.items.models import ListItem

    lst, qty, price, total = priced_list.values()
    resp = await client.post(
        f"/api/lists/{lst}/items/", json={"data": {qty: 4, price: 25}}
    )
    uuid = resp.json()["uuid"]

    async with session_factory() as db:
        stored = (
            await db.execute(select(ListItem.data).where(ListItem.uuid == uuid))
        ).scalar_one()
    assert stored[total] == 100


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


# -- keeping stored values in step with the schema --------------------------


async def test_a_new_calculated_column_backfills_existing_records(client, list_uuid):
    """The records were there first. Adding the column must not leave them blank."""
    qty = await _add_number(client, list_uuid, "qty")
    price = await _add_number(client, list_uuid, "price")
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {qty: 6, price: 7}}
    )
    uuid = created.json()["uuid"]

    resp = await _add_calculated(client, list_uuid, "total", "qty * price")
    assert resp.status_code == 201, resp.text
    total = resp.json()["uuid"]

    got = await client.get(f"/api/lists/{list_uuid}/items/{uuid}")
    assert got.json()["data"][total] == 42


async def test_editing_the_formula_rewrites_every_record(client, priced_list):
    lst, qty, price, total = priced_list.values()
    created = await client.post(
        f"/api/lists/{lst}/items/", json={"data": {qty: 3, price: 10}}
    )
    uuid = created.json()["uuid"]

    edited = await client.patch(
        f"/api/lists/{lst}/columns/{total}",
        json={"config": {"formula": "qty + price"}},
    )
    assert edited.status_code == 200, edited.text

    got = await client.get(f"/api/lists/{lst}/items/{uuid}")
    assert got.json()["data"][total] == 13


async def test_deleting_a_referenced_column_blanks_the_stored_value(
    client, priced_list
):
    """A stale number in a filterable column is worse than a blank one."""
    lst, qty, price, total = priced_list.values()
    created = await client.post(
        f"/api/lists/{lst}/items/", json={"data": {qty: 3, price: 10}}
    )
    uuid = created.json()["uuid"]
    assert created.json()["data"][total] == 30

    dropped = await client.delete(f"/api/lists/{lst}/columns/{price}")
    assert dropped.status_code == 204, dropped.text

    got = await client.get(f"/api/lists/{lst}/items/{uuid}")
    assert got.json()["data"][total] is None


async def test_restoring_a_referenced_column_brings_the_value_back(
    client, priced_list
):
    lst, qty, price, total = priced_list.values()
    created = await client.post(
        f"/api/lists/{lst}/items/", json={"data": {qty: 3, price: 10}}
    )
    uuid = created.json()["uuid"]
    await client.delete(f"/api/lists/{lst}/columns/{price}")

    restored = await client.post(f"/api/lists/{lst}/columns/{price}/restore")
    assert restored.status_code == 200, restored.text

    got = await client.get(f"/api/lists/{lst}/items/{uuid}")
    assert got.json()["data"][total] == 30


async def test_renaming_a_referenced_column_blanks_the_stored_value(
    client, priced_list
):
    """A formula references by name, so a rename breaks it — and the stored value
    has to follow, rather than sitting there looking current."""
    lst, qty, price, total = priced_list.values()
    created = await client.post(
        f"/api/lists/{lst}/items/", json={"data": {qty: 3, price: 10}}
    )
    uuid = created.json()["uuid"]

    renamed = await client.patch(
        f"/api/lists/{lst}/columns/{price}", json={"name": "unit_price"}
    )
    assert renamed.status_code == 200, renamed.text

    got = await client.get(f"/api/lists/{lst}/items/{uuid}")
    assert got.json()["data"][total] is None


# -- what storing the value bought: filter, sort, search --------------------


@pytest.fixture
async def priced_rows(client, priced_list):
    """Three records whose `total` is 10, 30 and 200."""
    lst, qty, price = priced_list["list"], priced_list["qty"], priced_list["price"]
    for q, p in ((1, 10), (3, 10), (10, 20)):
        resp = await client.post(
            f"/api/lists/{lst}/items/", json={"data": {qty: q, price: p}}
        )
        assert resp.status_code == 201, resp.text
    return priced_list


async def test_a_calculated_column_sorts_numerically(client, priced_rows):
    lst, total = priced_rows["list"], priced_rows["total"]
    rows = await _rows(client, lst, sort_by=total, sort_dir="asc")
    assert [row["data"][total] for row in rows] == [10, 30, 200]

    rows = await _rows(client, lst, sort_by=total, sort_dir="desc")
    assert [row["data"][total] for row in rows] == [200, 30, 10]


async def test_a_calculated_column_filters_by_range(client, priced_rows):
    lst, total = priced_rows["list"], priced_rows["total"]
    resp = await client.get(
        f"/api/lists/{lst}/items/",
        params={
            "filters": (
                '{"conditions": [{"column_uuid": "%s", "operator": ">", '
                '"value": 20}]}' % total
            )
        },
    )
    assert resp.status_code == 200, resp.text
    assert sorted(row["data"][total] for row in resp.json()) == [30, 200]


async def test_a_text_formula_sorts_alphabetically(client, list_uuid):
    first = (
        await client.post(
            f"/api/lists/{list_uuid}/columns/",
            json={"name": "first", "display_name": "First", "type": "text"},
        )
    ).json()["uuid"]
    calc = await _add_calculated(client, list_uuid, "shout", "UPPER(first)")
    assert calc.status_code == 201, calc.text
    shout = calc.json()["uuid"]

    for name in ("Grace", "Ada", "Katherine"):
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {first: name}}
        )

    rows = await _rows(client, list_uuid, sort_by=shout, sort_dir="asc")
    assert [row["data"][shout] for row in rows] == ["ADA", "GRACE", "KATHERINE"]


async def test_a_calculated_value_is_searchable(client, list_uuid):
    first = (
        await client.post(
            f"/api/lists/{list_uuid}/columns/",
            json={"name": "first", "display_name": "First", "type": "text"},
        )
    ).json()["uuid"]
    last = (
        await client.post(
            f"/api/lists/{list_uuid}/columns/",
            json={"name": "last", "display_name": "Last", "type": "text"},
        )
    ).json()["uuid"]
    full = (
        await _add_calculated(client, list_uuid, "full", "CONCAT(first, ' ', last)")
    ).json()["uuid"]

    await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {first: "Ada", last: "Lovelace"}},
    )
    await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {first: "Grace", last: "Hopper"}},
    )

    # "Ada Lov" appears in no single stored column but the calculated one.
    rows = await _rows(client, list_uuid, q="Ada Lov")
    assert [row["data"][full] for row in rows] == ["Ada Lovelace"]


# -- chains ------------------------------------------------------------------


@pytest.fixture
async def chained_list(client, list_uuid):
    """`subtotal` -> `tax` -> `grand`, each built on the one before it."""
    qty = await _add_number(client, list_uuid, "qty")
    price = await _add_number(client, list_uuid, "price")
    subtotal = (
        await _add_calculated(client, list_uuid, "subtotal", "qty * price")
    ).json()["uuid"]
    tax = (await _add_calculated(client, list_uuid, "tax", "subtotal * 0.1")).json()[
        "uuid"
    ]
    grand = (
        await _add_calculated(client, list_uuid, "grand", "subtotal + tax")
    ).json()["uuid"]
    return {
        "list": list_uuid,
        "qty": qty,
        "price": price,
        "subtotal": subtotal,
        "tax": tax,
        "grand": grand,
    }


async def test_a_calculated_column_can_build_on_another(client, chained_list):
    lst = chained_list["list"]
    resp = await client.post(
        f"/api/lists/{lst}/items/",
        json={"data": {chained_list["qty"]: 2, chained_list["price"]: 50}},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data[chained_list["subtotal"]] == 100
    assert data[chained_list["tax"]] == 10
    assert data[chained_list["grand"]] == 110


async def test_a_chain_recomputes_in_one_pass_on_update(client, chained_list):
    """The whole reason order matters: `grand` must read *this* write's `subtotal`,
    not the one the record arrived with."""
    lst = chained_list["list"]
    created = await client.post(
        f"/api/lists/{lst}/items/",
        json={"data": {chained_list["qty"]: 2, chained_list["price"]: 50}},
    )
    uuid = created.json()["uuid"]

    edited = await client.patch(
        f"/api/lists/{lst}/items/{uuid}", json={"data": {chained_list["price"]: 100}}
    )
    assert edited.status_code == 200, edited.text
    data = edited.json()["data"]
    assert data[chained_list["subtotal"]] == 200
    assert data[chained_list["tax"]] == 20
    assert data[chained_list["grand"]] == 220


async def test_compute_order_records_the_chain(client, chained_list):
    lst = chained_list["list"]
    resp = await client.get(f"/api/lists/{lst}/columns/")
    orders = {
        c["uuid"]: c["config"].get("compute_order")
        for c in resp.json()
        if c["type"] == "calculated"
    }
    assert orders[chained_list["subtotal"]] == 0
    assert orders[chained_list["tax"]] == 1
    assert orders[chained_list["grand"]] == 2


async def test_compute_order_can_be_raised_by_hand(client, list_uuid):
    """Among columns that do not read each other the graph has nothing to say, so an
    explicit order is the only way to pin which computes first."""
    await _add_number(client, list_uuid, "qty")
    a = (await _add_calculated(client, list_uuid, "doubled", "qty * 2")).json()["uuid"]
    b = (
        await _add_calculated(client, list_uuid, "tripled", "qty * 3", compute_order=5)
    ).json()["uuid"]

    resp = await client.get(f"/api/lists/{list_uuid}/columns/")
    orders = {c["uuid"]: c["config"].get("compute_order") for c in resp.json()}
    assert orders[a] == 0
    assert orders[b] == 5


async def test_an_explicit_order_before_a_dependency_is_refused(client, chained_list):
    lst, tax = chained_list["list"], chained_list["tax"]
    # `tax` reads `subtotal`, which sits at 0 — so it cannot itself sit at 0.
    resp = await client.patch(
        f"/api/lists/{lst}/columns/{tax}", json={"config": {"compute_order": 0}}
    )
    assert resp.status_code == 400, resp.text
    assert "compute_order" in resp.text
    assert "subtotal" in resp.text


async def test_adding_a_dependency_pushes_a_column_down_the_chain(client, list_uuid):
    """A stored order is a floor, not a contract: gaining a dependency lifts the
    column past it rather than 400ing over a number nobody typed."""
    await _add_number(client, list_uuid, "qty")
    late = (await _add_calculated(client, list_uuid, "late", "qty * 2")).json()["uuid"]
    early = (
        await _add_calculated(client, list_uuid, "early", "qty + 1", compute_order=4)
    ).json()["uuid"]

    resp = await client.patch(
        f"/api/lists/{list_uuid}/columns/{late}",
        json={"config": {"formula": "early * 2"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["compute_order"] == 5

    orders = {
        c["uuid"]: c["config"].get("compute_order")
        for c in (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    }
    assert orders[early] == 4 and orders[late] == 5


async def test_a_formula_that_names_itself_is_refused(client, priced_list):
    lst, total = priced_list["list"], priced_list["total"]
    resp = await client.patch(
        f"/api/lists/{lst}/columns/{total}", json={"config": {"formula": "total + 1"}}
    )
    assert resp.status_code == 400, resp.text
    assert "loop" in resp.text.lower()


async def test_a_two_step_loop_is_refused(client, chained_list):
    """`subtotal` -> `tax` exists; pointing `subtotal` back at `tax` closes the ring."""
    lst, subtotal = chained_list["list"], chained_list["subtotal"]
    resp = await client.patch(
        f"/api/lists/{lst}/columns/{subtotal}", json={"config": {"formula": "tax * 2"}}
    )
    assert resp.status_code == 400, resp.text
    assert "loop" in resp.text.lower()
    assert "tax" in resp.text


async def test_a_longer_loop_is_refused(client, chained_list):
    """`grand` reads `tax` reads `subtotal`; sending `subtotal` back to `grand` makes
    a three-step ring, which no single edge reveals on its own."""
    lst, subtotal = chained_list["list"], chained_list["subtotal"]
    resp = await client.patch(
        f"/api/lists/{lst}/columns/{subtotal}",
        json={"config": {"formula": "grand + 1"}},
    )
    assert resp.status_code == 400, resp.text
    assert "loop" in resp.text.lower()


# -- validation that survived the change ------------------------------------


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


async def test_a_calculated_column_named_like_a_function_can_be_chained(
    client, list_uuid
):
    """A column slug can collide with a function name, and the two namespaces are
    told apart by position rather than spelling — so `max` chains like any other."""
    await _add_number(client, list_uuid, "qty")
    calc = await _add_calculated(client, list_uuid, "max", "qty * 2")
    assert calc.status_code == 201, calc.text

    resp = await _add_calculated(client, list_uuid, "double", "max * 2")
    assert resp.status_code == 201, resp.text
    double = resp.json()["uuid"]

    created = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {(await _columns_by_name(client, list_uuid))["qty"]: 5}},
    )
    assert created.json()["data"][double] == 20


async def _columns_by_name(client, list_uuid):
    resp = await client.get(f"/api/lists/{list_uuid}/columns/")
    return {c["name"]: c["uuid"] for c in resp.json()}


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
    # column must cost its own cell and nothing else: parsing it while planning a
    # write used to abort the whole list, so one stale column hid every record.
    from uuid import uuid4

    from src.lists.columns.chain import apply_formulas, build_chain
    from src.lists.columns.enums import ColumnType
    from src.lists.columns.models import Column

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

    plan, key_of_name = build_chain(columns)
    values = apply_formulas(plan, key_of_name, {qty: 21, stale: 999})

    assert values[live] == 42  # the healthy sibling still computes
    # And the stale one is blanked rather than left holding the last answer it gave.
    assert values[stale] is None


def test_a_cycle_reached_outside_the_api_blanks_its_cells():
    """The API refuses a loop, but stored config can reach one another way (a
    restore, a hand edit). Evaluating must not hang or invent an order."""
    from uuid import uuid4

    from src.lists.columns.chain import apply_formulas, build_chain
    from src.lists.columns.enums import ColumnType
    from src.lists.columns.models import Column

    a, b, ok = str(uuid4()), str(uuid4()), str(uuid4())
    columns = {
        a: Column(name="a", type=ColumnType.calculated, config={"formula": "b + 1"}),
        b: Column(name="b", type=ColumnType.calculated, config={"formula": "a + 1"}),
        ok: Column(name="ok", type=ColumnType.calculated, config={"formula": "1 + 1"}),
    }

    plan, key_of_name = build_chain(columns)
    values = apply_formulas(plan, key_of_name, {a: 5, b: 6})

    assert values[ok] == 2  # a column outside the ring is unaffected
    assert values[a] is None and values[b] is None


async def test_an_injection_attempt_in_the_formula_is_rejected(client, list_uuid):
    resp = await _add_calculated(client, list_uuid, "evil", '__import__("os")')
    assert resp.status_code == 400, resp.text


async def test_overlapping_formula_edits_converge(client, priced_list, session_factory):
    """Two formula edits racing must not leave two formulas' answers in one column.

    A backfill captures its plan once and then scans; the second edit's PATCH arrives
    while the first is still scanning. Without the signature re-check at the end of a
    pass, the older edit carries on writing its own answers into rows the newer one has
    already passed — no error, just a column holding both. Which rows land wrong depends
    on scan timing, so this asserts the invariant that matters instead: when the dust
    settles, every row agrees with the formula the column actually stores.
    """
    import asyncio

    from src.lists.columns.services import ColumnService
    from src.lists.columns.schemas import ColumnConfig, ColumnUpdate
    from src.lists.models import List
    from sqlalchemy import select

    lst_uuid, qty, price, total = priced_list.values()
    for q in range(1, 31):
        await client.post(
            f"/api/lists/{lst_uuid}/items/", json={"data": {qty: q, price: 10}}
        )

    async def edit(formula, delay):
        await asyncio.sleep(delay)
        async with session_factory() as db:
            lst = (
                await db.execute(select(List).where(List.uuid == lst_uuid))
            ).scalars().first()
            await ColumnService.update(
                db,
                lst,
                total,
                ColumnUpdate(config=ColumnConfig(formula=formula)),
                user=None,
            )

    await asyncio.gather(
        edit("qty * price + 100", 0.0),
        edit("qty * price + 200", 0.01),
    )

    columns = (await client.get(f"/api/lists/{lst_uuid}/columns/")).json()
    stored = next(c for c in columns if c["uuid"] == total)["config"]["formula"]
    offset = 100 if stored.endswith("100") else 200

    rows = (await client.get(f"/api/lists/{lst_uuid}/items/?limit=1000")).json()
    assert len(rows) == 30
    for row in rows:
        expected = row["data"][qty] * row["data"][price] + offset
        assert row["data"][total] == expected, (
            f"row disagrees with the stored formula {stored!r}"
        )


async def test_a_superseded_pass_stops_without_writing(
    client, priced_list, session_factory, monkeypatch
):
    """A backfill that has been overtaken must not land a single row.

    `test_overlapping_formula_edits_converge` asserts the right invariant but cannot
    currently *fail*: both scans walk rows in `id` order, so the later-starting one
    trails the earlier one and overwrites it everywhere. The race needs the stale pass
    to be the one that finishes last, which is a matter of timing rather than something
    a test can arrange.

    So arrange the state instead. `list_columns` is made to hand out the *old* formula
    the first time it is asked — which is exactly what a pass holds when another session
    commits an edit a moment after it started — and the truth every time after. The pass
    must notice at its first commit gate, roll that chunk back, and leave the column
    pending rather than stamping a version its values do not have.
    """
    from sqlalchemy import select, text

    from src.lists.columns import backfill
    from src.lists.columns.models import Column as ColumnModel
    from src.lists.models import List

    lst_uuid, qty, price, total = priced_list.values()
    for q in (2, 5, 9):
        await client.post(
            f"/api/lists/{lst_uuid}/items/", json={"data": {qty: q, price: 10}}
        )

    async with session_factory() as db:
        list_id = (
            await db.execute(select(List.id).where(List.uuid == lst_uuid))
        ).scalar_one()
        # The new formula lands in the config with no backfill behind it, which is the
        # state `ColumnService.update` is in for the moment between its commit and its
        # call into here.
        await db.execute(
            text(
                "UPDATE columns SET config = jsonb_set(config, '{formula}', "
                "'\"qty * price + 500\"') WHERE uuid = CAST(:u AS uuid)"
            ),
            {"u": total},
        )
        await db.commit()

        real = backfill.list_columns
        calls = {"n": 0}

        async def stale_first(db_, lid):
            columns = await real(db_, lid)
            calls["n"] += 1
            if calls["n"] > 1:
                return columns
            # Detached copies, so nothing here is tracked or flushed by the session.
            return {
                key: ColumnModel(
                    name=c.name,
                    type=c.type,
                    position=c.position,
                    config=(
                        {**(c.config or {}), "formula": "qty * price"}
                        if key == total
                        else dict(c.config or {})
                    ),
                )
                for key, c in columns.items()
            }

        monkeypatch.setattr(backfill, "list_columns", stale_first)
        changed = await backfill.recompute_records(db, list_id)

    # Stopped at the first commit gate rather than scanning on.
    assert calls["n"] == 2
    assert changed == 0
    # And claimed nothing: the column still reports its values as not current, so the
    # superseding run (or the maintenance job) is what will settle it.
    assert await _pending(client, lst_uuid) == {"total"}


# -- "are these values current?" ---------------------------------------------


async def _pending(client, list_uuid):
    cols = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    return {c["name"] for c in cols if c["recompute_pending"]}


async def test_a_settled_column_is_not_pending(client, priced_list):
    lst = priced_list["list"]
    await client.post(
        f"/api/lists/{lst}/items/",
        json={"data": {priced_list["qty"]: 3, priced_list["price"]: 10}},
    )
    assert await _pending(client, lst) == set()


async def test_a_plain_column_is_never_pending(client, priced_list):
    """The flag is about formulas. A number column has nothing to recompute, and a
    warning on one would be noise nobody could act on."""
    cols = (await client.get(f"/api/lists/{priced_list['list']}/columns/")).json()
    assert all(
        c["recompute_pending"] is False for c in cols if c["type"] != "calculated"
    )


async def test_a_formula_edit_leaves_nothing_pending(client, priced_list):
    """The backfill runs inside the PATCH, so by the time it answers it is done."""
    lst, total = priced_list["list"], priced_list["total"]
    resp = await client.patch(
        f"/api/lists/{lst}/columns/{total}",
        json={"config": {"formula": "qty + price"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["recompute_pending"] is False
    assert await _pending(client, lst) == set()


async def test_values_left_behind_by_a_failed_backfill_read_as_pending(
    client, priced_list, session_factory, monkeypatch
):
    """The case the flag exists for: the recompute did not finish.

    Simulated by failing the backfill partway, which is what a redeploy, an instance
    restart or a client that gives up mid-PATCH all look like from the database's side.
    The config change is already committed by then, so the column is live with a new
    formula and values that predate it — and without the flag, nothing anywhere says so.
    """
    from src.lists.columns import backfill

    lst, total = priced_list["list"], priced_list["total"]
    await client.post(
        f"/api/lists/{lst}/items/",
        json={"data": {priced_list["qty"]: 3, priced_list["price"]: 10}},
    )

    async def die(*args, **kwargs):
        raise RuntimeError("instance went away")

    monkeypatch.setattr(backfill, "recompute_records", die)
    with pytest.raises(RuntimeError):
        await client.patch(
            f"/api/lists/{lst}/columns/{total}",
            json={"config": {"formula": "qty + price"}},
        )
    monkeypatch.undo()

    assert await _pending(client, lst) == {"total"}
    # And the values really are the old formula's, which is what makes it worth saying.
    rows = (await client.get(f"/api/lists/{lst}/items/")).json()
    assert rows[0]["data"][total] == 30  # qty * price, not qty + price

    # Running the repair clears it.
    async with session_factory() as db:
        from sqlalchemy import select

        from src.lists.models import List

        list_id = (
            await db.execute(select(List.id).where(List.uuid == lst))
        ).scalar_one()
        await backfill.recompute_records(db, list_id)

    assert await _pending(client, lst) == set()
    rows = (await client.get(f"/api/lists/{lst}/items/")).json()
    assert rows[0]["data"][total] == 13


async def test_a_whole_chain_reports_pending_together(
    client, chained_list, monkeypatch
):
    """A backfill rewrites every calculated column of the list, so they are stale or
    current as a group — reporting only the edited one would understate it."""
    from src.lists.columns import backfill

    lst = chained_list["list"]

    async def die(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(backfill, "recompute_records", die)
    with pytest.raises(RuntimeError):
        await client.patch(
            f"/api/lists/{lst}/columns/{chained_list['subtotal']}",
            json={"config": {"formula": "qty * price * 2"}},
        )
    monkeypatch.undo()

    assert await _pending(client, lst) == {"subtotal", "tax", "grand"}


async def test_adding_an_unrelated_column_does_not_raise_the_flag(
    client, priced_list
):
    """The signature covers what changes an answer, not everything about the list.

    Scoped generously it would mark every formula stale each time somebody added a
    text column, and a warning that cries wolf is one people learn to click past.
    """
    lst = priced_list["list"]
    resp = await client.post(
        f"/api/lists/{lst}/columns/",
        json={"name": "notes", "display_name": "Notes", "type": "text"},
    )
    assert resp.status_code == 201, resp.text
    assert await _pending(client, lst) == set()


# -- deferring the work on a list too big to do in the request ---------------


class _FakeDeferrer:
    """Stands in for FastAPI's `BackgroundTasks`, which is structurally all this is.

    Real background tasks are awaited inside the ASGI response cycle, so through the
    test client they would have run before `client.patch` even returned — which is
    exactly the timing this needs to be able to look inside.
    """

    def __init__(self):
        self.tasks = []

    def add_task(self, func, /, *args, **kwargs):
        self.tasks.append((func, args, kwargs))

    async def run(self):
        for func, args, kwargs in self.tasks:
            await func(*args, **kwargs)


async def _list_id(session_factory, list_uuid):
    from sqlalchemy import select

    from src.lists.models import List

    async with session_factory() as db:
        return (
            await db.execute(select(List.id).where(List.uuid == list_uuid))
        ).scalar_one()


async def test_a_small_list_is_recomputed_in_the_request(
    client, priced_list, session_factory, monkeypatch
):
    """Under the threshold nothing is deferred even though there is somewhere to defer
    to, which is what keeps the pending flag from flickering on ordinary lists."""
    from src.core.config import settings
    from src.lists.columns import backfill

    lst_uuid, qty, price, _total = priced_list.values()
    await client.post(
        f"/api/lists/{lst_uuid}/items/", json={"data": {qty: 3, price: 10}}
    )
    monkeypatch.setattr(settings, "FORMULA_INLINE_MAX_RECORDS", 5_000)

    deferrer = _FakeDeferrer()
    list_id = await _list_id(session_factory, lst_uuid)
    async with session_factory() as db:
        deferred = await backfill.resettle(db, list_id, defer=deferrer)

    assert deferred is False
    assert deferrer.tasks == []
    assert await _pending(client, lst_uuid) == set()


async def test_a_big_list_hands_the_backfill_to_the_deferrer(
    client, priced_list, session_factory, monkeypatch
):
    """Over the threshold the request does no record work, and the column says so.

    The values stay on the *old* formula meanwhile -- uniformly, not half rewritten --
    so a filter or sort over the column still answers coherently while it waits.
    """
    from src.core.config import settings
    from src.lists.columns import backfill

    from sqlalchemy import text

    lst_uuid, qty, price, total = priced_list.values()
    for q in (2, 5, 9):
        await client.post(
            f"/api/lists/{lst_uuid}/items/", json={"data": {qty: q, price: 10}}
        )
    # Three records is now "too big", which is the cheapest way to be over a threshold.
    monkeypatch.setattr(settings, "FORMULA_INLINE_MAX_RECORDS", 1)

    # The formula moves with no backfill behind it — the state `ColumnService.update`
    # is in between its commit and its call into `resettle`. Going through the API
    # instead would be no test at all: a real `BackgroundTasks` is awaited inside the
    # ASGI response, so the work would already be finished before the PATCH returned.
    list_id = await _list_id(session_factory, lst_uuid)
    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE columns SET config = jsonb_set(config, '{formula}', "
                "'\"qty * price + 7\"') WHERE uuid = CAST(:u AS uuid)"
            ),
            {"u": total},
        )
        await db.commit()

    deferrer = _FakeDeferrer()
    async with session_factory() as db:
        deferred = await backfill.resettle(db, list_id, defer=deferrer)

    assert deferred is True
    assert len(deferrer.tasks) == 1
    assert await _pending(client, lst_uuid) == {"total"}

    # Running what was deferred finishes the whole list, not a slice of it.
    await deferrer.run()
    assert await _pending(client, lst_uuid) == set()
    rows = (await client.get(f"/api/lists/{lst_uuid}/items/")).json()
    assert len(rows) == 3
    for row in rows:
        assert row["data"][total] == row["data"][qty] * row["data"][price] + 7


async def test_a_deferred_backfill_that_raises_leaves_the_flag_up(
    client, priced_list, session_factory, monkeypatch
):
    """Nothing may escape `_run_deferred` -- there is no caller left to catch it -- and
    it must not pretend to have succeeded either."""
    from sqlalchemy import text

    from src.lists.columns import backfill

    lst_uuid, qty, price, total = priced_list.values()
    await client.post(
        f"/api/lists/{lst_uuid}/items/", json={"data": {qty: 3, price: 10}}
    )
    list_id = await _list_id(session_factory, lst_uuid)

    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE columns SET config = jsonb_set(config, '{formula}', "
                "'\"qty * price + 1\"') WHERE uuid = CAST(:u AS uuid)"
            ),
            {"u": total},
        )
        await db.commit()

    async def boom(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(backfill, "recompute_records", boom)
    await backfill._run_deferred(list_id)  # must not raise
    monkeypatch.undo()

    assert await _pending(client, lst_uuid) == {"total"}


async def test_a_deferred_edit_still_ends_up_correct_over_the_api(
    client, priced_list, monkeypatch
):
    """End to end, with the real `BackgroundTasks` rather than a stand-in.

    Through the test client the task runs inside the ASGI response cycle, so this
    cannot observe the window where the flag is up — what it does prove is that the
    deferred path is actually wired to something that runs, and that it does the whole
    list rather than the slice the threshold names.
    """
    from src.core.config import settings

    lst_uuid, qty, price, total = priced_list.values()
    for q in (2, 5, 9):
        await client.post(
            f"/api/lists/{lst_uuid}/items/", json={"data": {qty: q, price: 10}}
        )
    monkeypatch.setattr(settings, "FORMULA_INLINE_MAX_RECORDS", 1)

    edited = await client.patch(
        f"/api/lists/{lst_uuid}/columns/{total}",
        json={"config": {"formula": "qty * price + 7"}},
    )
    assert edited.status_code == 200, edited.text

    rows = (await client.get(f"/api/lists/{lst_uuid}/items/")).json()
    assert len(rows) == 3
    for row in rows:
        assert row["data"][total] == row["data"][qty] * row["data"][price] + 7
    assert await _pending(client, lst_uuid) == set()
