"""Restoring a selection out of the bin, where one row failing is not the batch failing.

`bulk-restore` is the only bulk record write that cannot be a single UPDATE, and
every test here is about the reason: a binned row *releases* its `unique` values, so
each one has to be re-checked before it may come back. That makes two things true
which the delete, archive and unarchive routes never have to think about.

- **A conflict is per record.** A value taken while the row sat in the bin is an
  ordinary thing to have happened, so the response carries `conflicts` beside
  `restored` and answers 200. The conflicted rows stay in the bin, restorable once
  whatever took the value is dealt with.
- **The batch can collide with itself.** Two binned rows may legitimately hold the
  same unique value — nothing was enforcing anything while both were away. Checked
  independently they would both pass, so the pass claims each value as it accepts it
  and the first row in id order wins.

Everything else about the route is a silent skip rather than a conflict, and the
tests say which: an unknown uuid, one from another list, a live record, and a row
past the retention window all simply fail to appear in `restored`.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import update

from src.core.config import settings
from src.lists.items.models import ListItem


@pytest_asyncio.fixture
async def ledger(client, list_uuid, add_column):
    """Four records with a plain text column, none of them unique."""
    title = await add_column(
        list_uuid, name="title", display_name="Title", type="text"
    )
    uuids = []
    for name in ("alpha", "beta", "gamma", "delta"):
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {title: name}}
        )
        assert resp.status_code == 201, resp.text
        uuids.append(resp.json()["uuid"])
    return {"title": title, "uuids": uuids}


async def _bin(client, list_uuid, item_uuid):
    resp = await client.delete(f"/api/lists/{list_uuid}/items/{item_uuid}")
    assert resp.status_code == 204, resp.text


def _titles(payload, key):
    return sorted(row["data"][key] for row in payload)


async def _age(session_factory, uuid, days):
    """Push a binned record's `deleted_at` back past the retention window."""
    async with session_factory() as db:
        await db.execute(
            update(ListItem)
            .where(ListItem.uuid == uuid)
            .values(deleted_at=datetime.now(timezone.utc) - timedelta(days=days))
        )
        await db.commit()


# -- the ordinary path ------------------------------------------------------


async def test_bulk_restore_returns_records_and_counts_rows_not_uuids(
    client, list_uuid, ledger
):
    await _bin(client, list_uuid, ledger["uuids"][0])
    await _bin(client, list_uuid, ledger["uuids"][1])

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-restore",
        # A repeat and a record that was never binned, neither of which counts.
        json={
            "uuids": [
                ledger["uuids"][0],
                ledger["uuids"][0],
                ledger["uuids"][1],
                ledger["uuids"][2],
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"restored": 2, "conflicts": []}

    page = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert _titles(page, ledger["title"]) == ["alpha", "beta", "delta", "gamma"]
    assert (await client.get(f"/api/lists/{list_uuid}/items/deleted")).json() == []


async def test_bulk_restore_is_idempotent(client, list_uuid, ledger):
    """A second call names rows that are already live. They are not in the bin, so
    they are skipped like unknown uuids rather than restored twice."""
    await _bin(client, list_uuid, ledger["uuids"][0])

    first = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-restore",
        json={"uuids": [ledger["uuids"][0]]},
    )
    assert first.json() == {"restored": 1, "conflicts": []}

    again = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-restore",
        json={"uuids": [ledger["uuids"][0]]},
    )
    assert again.status_code == 200
    assert again.json() == {"restored": 0, "conflicts": []}


async def test_bulk_restore_skips_uuids_from_another_list(
    client, list_uuid, workspace_uuid, add_column, ledger
):
    other = (
        await client.post(
            "/api/lists/",
            json={"name": "other", "display_name": "Other",
                  "workspace_uuid": workspace_uuid},
        )
    ).json()["uuid"]
    other_col = await add_column(other, name="t", display_name="T", type="text")
    stray = (
        await client.post(
            f"/api/lists/{other}/items/", json={"data": {other_col: "x"}}
        )
    ).json()["uuid"]
    await _bin(client, other, stray)
    await _bin(client, list_uuid, ledger["uuids"][0])

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-restore",
        json={"uuids": [ledger["uuids"][0], stray, str(uuid4())]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"restored": 1, "conflicts": []}

    # The stray is still in its own list's bin — `_select_deleted` carries the
    # list, which is the only thing scoping this.
    still = (await client.get(f"/api/lists/{other}/items/deleted")).json()
    assert [row["uuid"] for row in still] == [stray]


async def test_bulk_restore_skips_a_record_past_the_retention_window(
    client, list_uuid, ledger, session_factory
):
    """The bin stops offering a record the purge job may take at any moment, and the
    bulk route has to agree with the listing it is reached from — otherwise a uuid
    the UI never showed could still be restored by naming it."""
    await _bin(client, list_uuid, ledger["uuids"][0])
    await _bin(client, list_uuid, ledger["uuids"][1])
    await _age(session_factory, ledger["uuids"][0], settings.PURGE_DELETED_AFTER_DAYS + 1)

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-restore",
        json={"uuids": [ledger["uuids"][0], ledger["uuids"][1]]},
    )
    assert resp.status_code == 200
    # Skipped, not conflicted: nothing was wrong with the request.
    assert resp.json() == {"restored": 1, "conflicts": []}

    page = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert "alpha" not in _titles(page, ledger["title"])
    assert "beta" in _titles(page, ledger["title"])


async def test_bulk_restore_refuses_a_payload_over_the_cap(client, list_uuid):
    too_many = [str(uuid4()) for _ in range(settings.BULK_DELETE_MAX + 1)]
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-restore", json={"uuids": too_many}
    )
    assert resp.status_code == 422


# -- the uniqueness pass, which is the whole feature ------------------------


@pytest_asyncio.fixture
async def coded(client, list_uuid, add_column):
    """A list with one `unique` text column, and a helper to add rows to it."""
    code = await add_column(
        list_uuid,
        name="code",
        display_name="Code",
        type="text",
        config={"unique": True},
    )

    async def add(value):
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {code: value}}
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["uuid"]

    return {"code": code, "add": add}


async def test_a_value_taken_while_the_row_sat_in_the_bin_is_a_conflict_not_a_failure(
    client, list_uuid, coded
):
    """The case that makes this endpoint different from the other three: the batch
    answers 200, the rows that could come back did, and the one that could not is
    named with the message a single restore would have 409'd with."""
    doomed = await coded["add"]("AB-1")
    spare = await coded["add"]("AB-2")
    await _bin(client, list_uuid, doomed)
    await _bin(client, list_uuid, spare)

    # The value is free now, so something else takes it.
    await coded["add"]("AB-1")

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-restore",
        json={"uuids": [doomed, spare]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["restored"] == 1
    assert len(body["conflicts"]) == 1
    assert body["conflicts"][0]["uuid"] == doomed
    assert "must be unique" in body["conflicts"][0]["message"]
    assert "code" in body["conflicts"][0]["message"]

    # The conflicted row is still in the bin, restorable once the clash is dealt
    # with — a failed restore must not quietly consume the chance to retry.
    binned = (await client.get(f"/api/lists/{list_uuid}/items/deleted")).json()
    assert [row["uuid"] for row in binned] == [doomed]


async def test_two_binned_rows_holding_the_same_unique_value_do_not_both_come_back(
    client, list_uuid, coded
):
    """The intra-batch collision, and the reason a per-record scan is not enough.

    Nothing enforces uniqueness among binned rows — the scan reads live rows only —
    so both of these legitimately hold `AB-1` while they are away. Checked
    independently both would pass and both would come back, breaking the invariant
    the single-record path protects.
    """
    first = await coded["add"]("AB-1")
    await _bin(client, list_uuid, first)
    # Free now, so a second row can take it and then be binned as well.
    second = await coded["add"]("AB-1")
    await _bin(client, list_uuid, second)

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-restore",
        json={"uuids": [first, second]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["restored"] == 1
    # Oldest first, deterministically: the batch is ordered by id, so the row that
    # held the value first is the one that gets it back.
    assert [c["uuid"] for c in body["conflicts"]] == [second]

    live = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert [row["uuid"] for row in live] == [first]


async def test_restoring_a_batch_with_no_unique_column_asks_nothing(
    client, list_uuid, ledger
):
    """The cheap path. With nothing unique on the list there is no value to
    re-check, so the whole pass — advisory lock included — is skipped and this is
    the single UPDATE the other bulk routes are."""
    for uuid in ledger["uuids"]:
        await _bin(client, list_uuid, uuid)

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-restore",
        json={"uuids": ledger["uuids"]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"restored": 4, "conflicts": []}


async def test_an_empty_unique_cell_never_conflicts(client, list_uuid, coded):
    """`_is_empty` values are not compared, here as everywhere else: several records
    with nothing in a unique column is not several records sharing a value."""
    blanks = [await coded["add"](None) for _ in range(3)]
    for uuid in blanks:
        await _bin(client, list_uuid, uuid)

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-restore", json={"uuids": blanks}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"restored": 3, "conflicts": []}


async def test_an_archived_row_still_blocks_a_restore(
    client, list_uuid, coded
):
    """The asymmetry between the two surfaces, from the bin's side. An archived row
    keeps its unique value reserved, so a binned row that wants the same value
    cannot have it back — even though nothing visible is holding it."""
    binned = await coded["add"]("AB-1")
    await _bin(client, list_uuid, binned)
    archived = await coded["add"]("AB-1")
    resp = await client.post(f"/api/lists/{list_uuid}/items/{archived}/archive")
    assert resp.status_code == 204, resp.text

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-restore", json={"uuids": [binned]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["restored"] == 0
    assert [c["uuid"] for c in body["conflicts"]] == [binned]


async def test_a_number_and_its_string_spelling_are_not_the_same_value(
    client, list_uuid, add_column
):
    """The unique key is JSON, not `str`. Were it `str`, the number 1 and the text
    "1" would collide across columns of different types — and a dict (a location
    cell) would not be hashable at all."""
    qty = await add_column(
        list_uuid,
        name="qty",
        display_name="Qty",
        type="number",
        config={"unique": True},
    )
    label = await add_column(
        list_uuid, name="label", display_name="Label", type="text",
        config={"unique": True},
    )
    row = (
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {qty: 1, label: "1"}}
        )
    ).json()["uuid"]
    await _bin(client, list_uuid, row)

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-restore", json={"uuids": [row]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"restored": 1, "conflicts": []}
