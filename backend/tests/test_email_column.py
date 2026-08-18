"""The `email` column type, over the HTTP API.

The load-bearing property is not the grammar check — it is that a cell is stored
in one canonical form. Two people typing the same mailbox differently must end up
holding the same value, or `unique` misses the duplicate it exists to catch and a
filter for one address misses the other. These tests pin the normalization on
every write path that reaches a cell: a record, a column default, and an import.
"""
import pytest


async def _add_email_column(client, list_uuid: str, name="contact", **config) -> str:
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": name,
            "display_name": name.title(),
            "type": "email",
            "config": config,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def test_accepts_an_address(client, list_uuid):
    col = await _add_email_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {col: "ada@example.com"}},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == "ada@example.com"


@pytest.mark.parametrize(
    "value",
    [
        "not-an-address",
        "@example.com",  # no local part
        "ada@",  # no host
        "ada@example",  # no dot in the host
        "ada@@example.com",  # two separators
        "ada lovelace@example.com",  # whitespace inside
        "ada@exa mple.com",
    ],
)
async def test_rejects_malformed_addresses(client, list_uuid, value):
    col = await _add_email_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
    )
    assert resp.status_code == 400, f"{value!r} was accepted: {resp.text}"


async def test_an_address_is_stored_trimmed_and_lowercased(client, list_uuid):
    col = await _add_email_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {col: "  Ada.Lovelace@Example.COM  "}},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == "ada.lovelace@example.com"


async def test_unique_catches_the_same_mailbox_typed_differently(client, list_uuid):
    """The whole point of normalizing: without it these are two values."""
    col = await _add_email_column(client, list_uuid, unique=True)
    first = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "ada@example.com"}}
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "ADA@Example.com"}}
    )
    assert second.status_code == 409, second.text


async def test_max_length_is_measured_on_the_stored_form(client, list_uuid):
    col = await _add_email_column(client, list_uuid, max_length=16)
    ok = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "  ada@example.com  "}}
    )
    assert ok.status_code == 201, ok.text  # 15 characters once trimmed

    nope = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {col: "ada.lovelace@example.com"}},
    )
    assert nope.status_code == 400


async def test_a_columns_default_is_validated_and_normalized(client, list_uuid):
    bad = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "contact",
            "display_name": "Contact",
            "type": "email",
            "config": {"default": "not-an-address"},
        },
    )
    assert bad.status_code == 400, bad.text

    good = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "contact",
            "display_name": "Contact",
            "type": "email",
            "config": {"default": "  Ada@Example.COM  "},
        },
    )
    assert good.status_code == 201, good.text
    # Stored canonically, so a defaulted cell and a typed one are one value.
    assert good.json()["config"]["default"] == "ada@example.com"


async def test_import_normalizes_and_rejects_row_by_row(client, list_uuid):
    col = await _add_email_column(client, list_uuid)
    csv = "contact\nnot-an-address\nAda@Example.COM\n"
    resp = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("rows.csv", csv, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert len(body["errors"]) == 1

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert [i["data"][col] for i in items] == ["ada@example.com"]
