"""The `calculated` column type, over the HTTP API.

A calculated column stores no value: it holds a formula over other columns and
is computed when a record is read. These tests pin that a record cannot set one,
that reads carry the computed value, and that the config is validated against the
list's real columns.
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


async def test_formula_referencing_a_non_number_column_is_rejected(
    client, list_uuid
):
    await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={"name": "label", "display_name": "Label", "type": "text"},
    )
    resp = await _add_calculated(client, list_uuid, "bad", "label * 2")
    assert resp.status_code == 400, resp.text
    assert "number columns" in resp.text.lower()


async def test_a_calculated_column_cannot_reference_another(client, priced_list):
    # `total` is calculated; a formula over it must be refused (no calc-on-calc).
    lst = priced_list["list"]
    resp = await _add_calculated(client, lst, "double", "total * 2")
    assert resp.status_code == 400, resp.text
    assert "number columns" in resp.text.lower()


async def test_an_injection_attempt_in_the_formula_is_rejected(client, list_uuid):
    resp = await _add_calculated(client, list_uuid, "evil", '__import__("os")')
    assert resp.status_code == 400, resp.text
