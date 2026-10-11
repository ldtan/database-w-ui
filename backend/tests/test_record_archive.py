"""Archiving takes a record out of every normal read, and out of nothing else.

The archive is a second lifecycle axis beside the bin, and the two are orthogonal:
a delete says "this was a mistake, and it goes for good in thirty days", an archive
says "this happened and I have finished with it". So there is no retention window
here, nothing purges an archived record for being archived, and — the part most
likely to be broken by a later change — an archived record keeps its inbound
references resolvable and keeps its `unique` values reserved.

The exclusions are the whole feature and they all hang off one predicate in
`TableRecordService._select`, so most of the tests below would pass or fail together.
They are written out separately anyway, because the next person to add a read path
needs to see which questions it is expected to answer.

The load-bearing subtleties, each pinned by a test whose name says so:

- **Delete wins over archive.** Archiving then deleting puts a record in the bin, not
  the archive, because the bin is the surface with the deadline on it.
- **Unarchive cannot 409**, unlike restore, precisely because the unique value was
  never released.
- **Rendering a label is uuid-keyed and always works; matching a label is search,**
  so an archived row still names itself in a cell it is already in, but no longer
  answers a search or a picker in the table that points at it.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from src.core.config import settings
from src.tables.records.models import TableRecord
from src.maintenance.purge_deleted_records import purge

from test_metrics import add_metric, value_of  # noqa: F401  (shared helpers)


@pytest_asyncio.fixture
async def ledger(client, table_uuid, add_column):
    """Four records, one of which gets archived by the tests that need it.

    `title` is text so it can be searched; `qty` is a number so a metric has
    something to add up.
    """
    title = await add_column(table_uuid, name="title", display_name="Title", type="text")
    qty = await add_column(table_uuid, name="qty", display_name="Qty", type="number")
    uuids = []
    for name, amount in (
        ("alpha", 1),
        ("beta", 2),
        ("gamma", 4),
        ("delta", 8),
    ):
        resp = await client.post(
            f"/api/tables/{table_uuid}/records/",
            json={"data": {title: name, qty: amount}},
        )
        assert resp.status_code == 201, resp.text
        uuids.append(resp.json()["uuid"])
    return {"title": title, "qty": qty, "uuids": uuids}


async def _archive(client, table_uuid, record_uuid):
    resp = await client.post(f"/api/tables/{table_uuid}/records/{record_uuid}/archive")
    assert resp.status_code == 204, resp.text


def _titles(payload, key):
    return sorted(row["data"][key] for row in payload)


# -- the exclusions ---------------------------------------------------------


async def test_an_archived_record_leaves_the_listing_and_the_count(
    client, table_uuid, ledger
):
    await _archive(client, table_uuid, ledger["uuids"][0])

    page = (await client.get(f"/api/tables/{table_uuid}/records/")).json()
    assert _titles(page, ledger["title"]) == ["beta", "delta", "gamma"]

    total = (await client.get(f"/api/tables/{table_uuid}/records/count")).json()["total"]
    # The count is built off the same statement as the page, so a drift between
    # these two is the bug this asserts against — not merely a wrong number.
    assert total == 3


async def test_an_archived_record_is_not_searchable(client, table_uuid, ledger):
    await _archive(client, table_uuid, ledger["uuids"][0])

    found = (await client.get(f"/api/tables/{table_uuid}/records/?q=alpha")).json()
    assert found == []
    counted = (
        await client.get(f"/api/tables/{table_uuid}/records/count?q=alpha")
    ).json()["total"]
    assert counted == 0


async def test_an_archived_record_is_not_filtered_or_sorted_into_view(
    client, table_uuid, ledger
):
    await _archive(client, table_uuid, ledger["uuids"][3])  # "delta", qty 8

    filters = (
        '{"conjunction":"and","conditions":'
        f'[{{"column_uuid":"{ledger["qty"]}","operator":">","value":1}}]}}'
    )
    filtered = (
        await client.get(f"/api/tables/{table_uuid}/records/?filters={filters}")
    ).json()
    assert _titles(filtered, ledger["title"]) == ["beta", "gamma"]

    # Sorting is the other half: an archived row must not reappear merely because
    # the ordering changed, and descending by qty is where it would have led.
    sorted_rows = (
        await client.get(
            f"/api/tables/{table_uuid}/records/"
            f"?sort_by={ledger['qty']}&sort_dir=desc"
        )
    ).json()
    assert [r["data"][ledger["title"]] for r in sorted_rows] == [
        "gamma",
        "beta",
        "alpha",
    ]


async def test_an_archived_record_is_left_out_of_a_metric(
    client, table_uuid, ledger
):
    await add_metric(client, table_uuid, "total_qty", "SUM(qty)")

    before = (await client.get(f"/api/tables/{table_uuid}/metrics/values")).json()
    assert value_of(before, "total_qty") == 15

    await _archive(client, table_uuid, ledger["uuids"][3])  # qty 8

    after = (await client.get(f"/api/tables/{table_uuid}/metrics/values")).json()
    assert value_of(after, "total_qty") == 7


async def test_a_cursor_cannot_name_an_archived_record(client, table_uuid, ledger):
    """`after` names a position in the listing, so it has to name a record the
    listing contains — otherwise the page after it is a page of nothing."""
    await _archive(client, table_uuid, ledger["uuids"][0])

    resp = await client.get(
        f"/api/tables/{table_uuid}/records/?after={ledger['uuids'][0]}"
    )
    assert resp.status_code == 400


# -- the archive surface ----------------------------------------------------


async def test_the_archive_lists_what_was_archived_and_unarchive_returns_it(
    client, table_uuid, ledger
):
    await _archive(client, table_uuid, ledger["uuids"][0])

    resp = await client.get(f"/api/tables/{table_uuid}/records/archived")
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "1"
    archived = resp.json()
    assert _titles(archived, ledger["title"]) == ["alpha"]
    # The listing carries when it happened; there is no retention window to count
    # down from, so this is the only thing the UI can say about it.
    assert archived[0]["archived_at"] is not None

    back = await client.post(
        f"/api/tables/{table_uuid}/records/{ledger['uuids'][0]}/unarchive"
    )
    assert back.status_code == 200

    page = (await client.get(f"/api/tables/{table_uuid}/records/")).json()
    assert _titles(page, ledger["title"]) == ["alpha", "beta", "delta", "gamma"]
    assert (await client.get(f"/api/tables/{table_uuid}/records/archived")).json() == []


async def test_the_archive_has_no_retention_window(
    client, table_uuid, ledger, session_factory
):
    """The bin drops a record once it is past `PURGE_DELETED_AFTER_DAYS`, because the
    purge job may take it at any moment. Nothing is coming for an archived record, so
    age must not remove it from the surface that can bring it back."""
    await _archive(client, table_uuid, ledger["uuids"][0])
    async with session_factory() as db:
        await db.execute(
            update(TableRecord)
            .where(TableRecord.uuid == ledger["uuids"][0])
            .values(archived_at=datetime.now(timezone.utc) - timedelta(days=3650))
        )
        await db.commit()

    archived = (await client.get(f"/api/tables/{table_uuid}/records/archived")).json()
    assert _titles(archived, ledger["title"]) == ["alpha"]


async def test_bulk_unarchive_returns_records_and_counts_rows_not_uuids(
    client, table_uuid, ledger
):
    """The inverse of `bulk_archive`, and idempotent in the same way: a uuid that is
    already live matches nothing rather than erroring, so a double-click on a
    selection is not a different outcome from a single one."""
    await _archive(client, table_uuid, ledger["uuids"][0])
    await _archive(client, table_uuid, ledger["uuids"][1])

    resp = await client.post(
        f"/api/tables/{table_uuid}/records/bulk-unarchive",
        # A repeat and an already-live uuid, both of which must count for nothing.
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
    assert resp.json() == {"unarchived": 2}

    page = (await client.get(f"/api/tables/{table_uuid}/records/")).json()
    assert _titles(page, ledger["title"]) == ["alpha", "beta", "delta", "gamma"]
    assert (await client.get(f"/api/tables/{table_uuid}/records/archived")).json() == []

    # And again, now that nothing is archived: still 200, still nothing moved.
    again = await client.post(
        f"/api/tables/{table_uuid}/records/bulk-unarchive",
        json={"uuids": [ledger["uuids"][0]]},
    )
    assert again.status_code == 200
    assert again.json() == {"unarchived": 0}


async def test_bulk_unarchive_skips_uuids_from_another_table(
    client, table_uuid, workspace_uuid, add_column, ledger
):
    other = (
        await client.post(
            "/api/tables/",
            json={"name": "other2", "display_name": "Other 2",
                  "workspace_uuid": workspace_uuid},
        )
    ).json()["uuid"]
    other_col = await add_column(other, name="t", display_name="T", type="text")
    stray = (
        await client.post(
            f"/api/tables/{other}/records/", json={"data": {other_col: "x"}}
        )
    ).json()["uuid"]
    await _archive(client, other, stray)
    await _archive(client, table_uuid, ledger["uuids"][0])

    resp = await client.post(
        f"/api/tables/{table_uuid}/records/bulk-unarchive",
        json={"uuids": [ledger["uuids"][0], stray]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"unarchived": 1}

    # The stray is still archived in its own table — `table_id` is what scoped it out,
    # exactly as in `bulk_delete`.
    still = (await client.get(f"/api/tables/{other}/records/archived")).json()
    assert [row["uuid"] for row in still] == [stray]


async def test_bulk_unarchive_cannot_conflict_on_a_unique_value(
    client, table_uuid, add_column
):
    """The whole reason this endpoint returns a bare count where a bulk *restore*
    has to report per-uuid conflicts. Two archived rows holding `unique` values
    never released them, so bringing both back at once cannot fail."""
    code = await add_column(
        table_uuid,
        name="code",
        display_name="Code",
        type="text",
        config={"unique": True},
    )
    uuids = []
    for value in ("AB-1", "AB-2"):
        uuids.append(
            (
                await client.post(
                    f"/api/tables/{table_uuid}/records/", json={"data": {code: value}}
                )
            ).json()["uuid"]
        )
        await _archive(client, table_uuid, uuids[-1])

    resp = await client.post(
        f"/api/tables/{table_uuid}/records/bulk-unarchive", json={"uuids": uuids}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"unarchived": 2}
    page = (await client.get(f"/api/tables/{table_uuid}/records/")).json()
    assert sorted(row["data"][code] for row in page) == ["AB-1", "AB-2"]


async def test_bulk_unarchive_leaves_a_binned_record_in_the_bin(
    client, table_uuid, ledger
):
    """Delete wins over archive, so the archive's bulk route must not be a way to
    pull a record off the surface that has the purge deadline on it."""
    await _archive(client, table_uuid, ledger["uuids"][0])
    await client.delete(f"/api/tables/{table_uuid}/records/{ledger['uuids'][0]}")

    resp = await client.post(
        f"/api/tables/{table_uuid}/records/bulk-unarchive",
        json={"uuids": [ledger["uuids"][0]]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"unarchived": 0}

    binned = (await client.get(f"/api/tables/{table_uuid}/records/deleted")).json()
    assert _titles(binned, ledger["title"]) == ["alpha"]


async def test_bulk_unarchive_refuses_a_payload_over_the_cap(client, table_uuid):
    """Rejected by the schema before any work happens, which is what makes the limit
    a published one the SPA can chunk against rather than a surprise."""
    too_many = [str(uuid4()) for _ in range(settings.BULK_DELETE_MAX + 1)]
    resp = await client.post(
        f"/api/tables/{table_uuid}/records/bulk-unarchive", json={"uuids": too_many}
    )
    assert resp.status_code == 422


async def test_bulk_archive_skips_uuids_from_another_table(
    client, table_uuid, workspace_uuid, add_column, ledger
):
    other = (
        await client.post(
            "/api/tables/",
            json={"name": "other", "display_name": "Other",
                  "workspace_uuid": workspace_uuid},
        )
    ).json()["uuid"]
    other_col = await add_column(other, name="t", display_name="T", type="text")
    stray = (
        await client.post(
            f"/api/tables/{other}/records/", json={"data": {other_col: "x"}}
        )
    ).json()["uuid"]

    resp = await client.post(
        f"/api/tables/{table_uuid}/records/bulk-archive",
        # The repeat is deliberate: rows are counted, not uuids, so a duplicate in
        # the payload must not be counted twice.
        json={"uuids": [ledger["uuids"][0], ledger["uuids"][0], stray]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"archived": 1}

    # The stray is untouched in its own table.
    assert len((await client.get(f"/api/tables/{other}/records/")).json()) == 1


# -- where the archive meets the bin ----------------------------------------


async def test_deleting_an_archived_record_puts_it_in_the_bin_not_the_archive(
    client, table_uuid, ledger
):
    """Delete wins. A record on its way out must not be hiding on a surface with no
    deadline, or the purge job would destroy something the archive still offered."""
    await _archive(client, table_uuid, ledger["uuids"][0])
    await client.delete(f"/api/tables/{table_uuid}/records/{ledger['uuids'][0]}")

    assert (await client.get(f"/api/tables/{table_uuid}/records/archived")).json() == []
    binned = (await client.get(f"/api/tables/{table_uuid}/records/deleted")).json()
    assert _titles(binned, ledger["title"]) == ["alpha"]

    # Restoring returns it to the state it was in, which was archived — not live.
    await client.post(f"/api/tables/{table_uuid}/records/{ledger['uuids'][0]}/restore")
    assert (await client.get(f"/api/tables/{table_uuid}/records/")).json() != []
    still_archived = (
        await client.get(f"/api/tables/{table_uuid}/records/archived")
    ).json()
    assert _titles(still_archived, ledger["title"]) == ["alpha"]
    page = (await client.get(f"/api/tables/{table_uuid}/records/")).json()
    assert "alpha" not in _titles(page, ledger["title"])


async def test_the_purge_job_still_sees_archived_records(
    client, table_uuid, ledger, session_factory
):
    """Maintenance runs over everything. An archived record that is then deleted and
    left past the window is as purgeable as any other — archiving is not a way to
    keep a record out of the reach of the jobs."""
    await _archive(client, table_uuid, ledger["uuids"][0])
    await client.delete(f"/api/tables/{table_uuid}/records/{ledger['uuids'][0]}")
    async with session_factory() as db:
        await db.execute(
            update(TableRecord)
            .where(TableRecord.uuid == ledger["uuids"][0])
            .values(deleted_at=datetime.now(timezone.utc) - timedelta(days=90))
        )
        await db.commit()

    assert await purge(days=30) == 1

    async with session_factory() as db:
        gone = (
            await db.execute(
                select(TableRecord.id).where(TableRecord.uuid == ledger["uuids"][0])
            )
        ).scalars().first()
    assert gone is None


# -- what archiving deliberately does not touch -----------------------------


async def test_an_archived_record_keeps_its_unique_value_reserved(
    client, table_uuid, add_column
):
    """This is what makes `unarchive` incapable of conflicting, and it is the one
    asymmetry with the bin: a binned row *releases* its value and `restore` can 409.
    """
    code = await add_column(
        table_uuid,
        name="code",
        display_name="Code",
        type="text",
        config={"unique": True},
    )
    original = (
        await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {code: "AB-1"}}
        )
    ).json()["uuid"]

    await _archive(client, table_uuid, original)

    # Still taken, even though nothing can see the record holding it.
    clash = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {code: "AB-1"}}
    )
    assert clash.status_code == 409

    # So coming back is always possible.
    back = await client.post(f"/api/tables/{table_uuid}/records/{original}/unarchive")
    assert back.status_code == 200


@pytest_asyncio.fixture
async def linked(client, workspace_uuid, table_uuid, add_column):
    """A `schools` table with one row, and a lookup at it from `table_uuid`."""
    schools = (
        await client.post(
            "/api/tables/",
            json={"name": "schools", "display_name": "Schools",
                  "workspace_uuid": workspace_uuid},
        )
    ).json()["uuid"]
    label = await add_column(schools, name="name", display_name="Name", type="text")
    row = (
        await client.post(
            f"/api/tables/{schools}/records/", json={"data": {label: "Springfield"}}
        )
    ).json()["uuid"]
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": schools},
    )
    record = (
        await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {lookup: row}}
        )
    ).json()["uuid"]
    return {"schools": schools, "row": row, "lookup": lookup, "record": record}


async def test_an_archived_row_still_names_itself_in_a_cell_that_holds_it(
    client, table_uuid, linked
):
    """Rendering a held reference is a uuid lookup, so it is unaffected — otherwise
    archiving one table would turn another table's live relationships into stubs."""
    await _archive(client, linked["schools"], linked["row"])

    resolved = (
        await client.get(
            f"/api/tables/{table_uuid}/columns/{linked['lookup']}/options"
            f"?uuid={linked['row']}"
        )
    ).json()
    assert resolved == [
        {
            "uuid": linked["row"],
            "label": "Springfield",
            "deleted": False,
            "archived": True,
        }
    ]


async def test_no_picker_offers_an_archived_row_and_no_search_finds_one(
    client, table_uuid, linked
):
    """The other half of the rule: matching a *label* is search, and archived
    records are not searchable — including as a search term in another table."""
    await _archive(client, linked["schools"], linked["row"])

    offered = (
        await client.get(
            f"/api/tables/{table_uuid}/columns/{linked['lookup']}/options?q=spring"
        )
    ).json()
    assert offered == []

    # And the record pointing at it is no longer reachable by typing the label.
    found = (await client.get(f"/api/tables/{table_uuid}/records/?q=springfield")).json()
    assert found == []


async def test_nothing_new_can_be_linked_to_an_archived_row_by_label_or_by_uuid(
    client, table_uuid, linked
):
    """Both branches of `_resolve_lookup_values` have to agree, or a pasted uuid
    would make a link the picker refuses to offer."""
    await _archive(client, linked["schools"], linked["row"])

    by_label = await client.post(
        f"/api/tables/{table_uuid}/records/",
        json={"data": {linked["lookup"]: "Springfield"}},
    )
    assert by_label.status_code == 400

    by_uuid = await client.post(
        f"/api/tables/{table_uuid}/records/",
        json={"data": {linked["lookup"]: linked["row"]}},
    )
    assert by_uuid.status_code == 400

    # But the cell that already held it is untouched by all of the above.
    held = (
        await client.get(f"/api/tables/{table_uuid}/records/{linked['record']}")
    ).json()
    assert held["data"][linked["lookup"]] == linked["row"]


# -- export -----------------------------------------------------------------


@pytest.mark.parametrize(
    "include_archived, expected",
    [(False, ["beta", "delta", "gamma"]), (True, ["alpha", "beta", "delta", "gamma"])],
)
async def test_export_excludes_archived_records_unless_asked(
    client, table_uuid, ledger, include_archived, expected
):
    """Excluded by default like every other read of the table, but reachable on
    request — otherwise archiving would be a one-way door for anyone trying to get
    their data out."""
    await _archive(client, table_uuid, ledger["uuids"][0])

    resp = await client.post(
        f"/api/tables/{table_uuid}/export",
        json={"format": "json", "include_archived": include_archived},
    )
    assert resp.status_code == 200
    rows = resp.json() if isinstance(resp.json(), list) else resp.json()
    assert sorted(row["title"] for row in rows) == expected


async def test_cloning_a_table_carries_its_archive_over(
    client, table_uuid, ledger
):
    """A clone with records is asked for to get the data. Dropping the archived rows
    — or bringing them back live — would both be the clone disagreeing with the
    source about what it holds."""
    await _archive(client, table_uuid, ledger["uuids"][0])

    clone = (
        await client.post(
            f"/api/tables/{table_uuid}/clone", json={"with_records": True}
        )
    ).json()["uuid"]

    # The clone has new column uuids, so find the title column by name.
    cols = (await client.get(f"/api/tables/{clone}/columns/")).json()
    title = next(c["uuid"] for c in cols if c["name"] == "title")

    live = (await client.get(f"/api/tables/{clone}/records/")).json()
    assert _titles(live, title) == ["beta", "delta", "gamma"]
    archived = (await client.get(f"/api/tables/{clone}/records/archived")).json()
    assert _titles(archived, title) == ["alpha"]
