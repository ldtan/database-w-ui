"""The bin for a table's views, forms, metrics and columns.

Until this surface existed, deleting one of the four was silently permanent: the row
stayed on disk forever and nothing could bring it back. These cover the round trip,
the two edges that are real (a name taken while the row sat binned, and the retention
window), and the parts that are deliberately *not* symmetric with the live routes.

The three routes sit on each child's own collection — `/views/deleted`,
`/views/{uuid}/restore`, `/views/{uuid}/permanent` — parametrized here over the four
kinds because one implementation serves all of them (src.tables.bin.services).

Columns get their own block at the bottom. They are the kind that owns data: their
values sit in every record's `data`, which makes restore and permanent delete mean
more for them than for the three that are pure config.

Runs as the seeded 'tester' user (DISABLE_AUTH), who owns everything, so the
configure-level gate resolves via the owner short-circuit.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from src.tables.columns.models import Column
from src.tables.forms.models import Form
from src.tables.records.models import TableRecord
from src.tables.metrics.models import Metric
from src.tables.views.models import View

# The four kinds, the payload that creates one, and the model behind it.
KINDS = {
    "views": (
        {"name": "by_owner", "display_name": "By owner", "config": {}},
        View,
    ),
    "forms": (
        {
            "name": "intake",
            "display_name": "Intake",
            "type": "create",
            "config": {},
        },
        Form,
    ),
    "metrics": (
        {
            "name": "row_count",
            "display_name": "Row count",
            "config": {"formula": "COUNT()"},
        },
        Metric,
    ),
    "columns": (
        {"name": "notes", "display_name": "Notes", "type": "text", "config": {}},
        Column,
    ),
}


async def _create(client, table_uuid, kind):
    payload, _ = KINDS[kind]
    resp = await client.post(f"/api/tables/{table_uuid}/{kind}/", json=dict(payload))
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def _age(session_factory, model, uuid: str, days: int) -> None:
    """Backdate a row's deletion so the retention window can be tested without
    waiting a month."""
    when = datetime.now(timezone.utc) - timedelta(days=days)
    async with session_factory() as db:
        await db.execute(
            update(model).where(model.uuid == uuid).values(deleted_at=when)
        )
        await db.commit()


@pytest.mark.parametrize("kind", list(KINDS))
async def test_delete_then_restore_round_trip(client, table_uuid, kind):
    uuid = await _create(client, table_uuid, kind)

    assert (await client.delete(f"/api/tables/{table_uuid}/{kind}/{uuid}")).status_code == 204
    # Gone from the live collection...
    live = await client.get(f"/api/tables/{table_uuid}/{kind}/")
    assert uuid not in [row["uuid"] for row in live.json()]
    assert (await client.get(f"/api/tables/{table_uuid}/{kind}/{uuid}")).status_code == 404

    # ...and present in the bin, with the timestamp a caller needs to show how much
    # of the window is left.
    binned = await client.get(f"/api/tables/{table_uuid}/{kind}/deleted")
    assert binned.status_code == 200, binned.text
    assert binned.headers["X-Total-Count"] == "1"
    (row,) = binned.json()
    assert row["uuid"] == uuid
    assert row["deleted_at"] is not None

    restored = await client.post(f"/api/tables/{table_uuid}/{kind}/{uuid}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["uuid"] == uuid

    # Back in the live collection, addressable again, and out of the bin.
    assert (await client.get(f"/api/tables/{table_uuid}/{kind}/{uuid}")).status_code == 200
    assert (await client.get(f"/api/tables/{table_uuid}/{kind}/deleted")).json() == []


@pytest.mark.parametrize("kind", list(KINDS))
async def test_restore_conflicts_when_the_name_was_taken(client, table_uuid, kind):
    """A name is unique per table among *live* rows only, so the binned row's name is
    free the moment it is deleted — and taking it is what makes the restore a 409."""
    uuid = await _create(client, table_uuid, kind)
    assert (await client.delete(f"/api/tables/{table_uuid}/{kind}/{uuid}")).status_code == 204

    # The same name is available again precisely because the index is partial.
    replacement = await _create(client, table_uuid, kind)
    assert replacement != uuid

    clash = await client.post(f"/api/tables/{table_uuid}/{kind}/{uuid}/restore")
    assert clash.status_code == 409, clash.text

    # The failed restore left it in the bin rather than half-applied.
    binned = await client.get(f"/api/tables/{table_uuid}/{kind}/deleted")
    assert [row["uuid"] for row in binned.json()] == [uuid]


@pytest.mark.parametrize("kind", list(KINDS))
async def test_permanent_delete_only_from_the_bin(client, table_uuid, kind):
    uuid = await _create(client, table_uuid, kind)

    # A live one cannot be purged — destroying one is always two deliberate steps.
    early = await client.delete(f"/api/tables/{table_uuid}/{kind}/{uuid}/permanent")
    assert early.status_code == 404

    assert (await client.delete(f"/api/tables/{table_uuid}/{kind}/{uuid}")).status_code == 204
    gone = await client.delete(f"/api/tables/{table_uuid}/{kind}/{uuid}/permanent")
    assert gone.status_code == 204

    assert (await client.get(f"/api/tables/{table_uuid}/{kind}/deleted")).json() == []
    # And it is not restorable afterwards.
    assert (
        await client.post(f"/api/tables/{table_uuid}/{kind}/{uuid}/restore")
    ).status_code == 404


@pytest.mark.parametrize("kind", list(KINDS))
async def test_past_the_window_it_leaves_the_bin(client, session_factory, table_uuid, kind):
    """Past the retention window a row is due to be purged and is no longer promised
    back, so the bin stops offering it — the rule the records and tables bins follow."""
    _, model = KINDS[kind]
    uuid = await _create(client, table_uuid, kind)
    assert (await client.delete(f"/api/tables/{table_uuid}/{kind}/{uuid}")).status_code == 204
    await _age(session_factory, model, uuid, days=45)

    binned = await client.get(f"/api/tables/{table_uuid}/{kind}/deleted")
    assert binned.json() == []
    assert binned.headers["X-Total-Count"] == "0"
    assert (
        await client.post(f"/api/tables/{table_uuid}/{kind}/{uuid}/restore")
    ).status_code == 404


async def test_the_bin_is_addressed_by_uuid_not_by_name(client, table_uuid):
    """Every child route is uuid-only now, but the bin is the one that could never
    have been anything else: a binned `by_owner` and a live one coexist legitimately,
    so a name here has no single answer even in principle."""
    uuid = await _create(client, table_uuid, "views")
    assert (await client.delete(f"/api/tables/{table_uuid}/views/{uuid}")).status_code == 204

    by_name = await client.post("/api/tables/%s/views/by_owner/restore" % table_uuid)
    assert by_name.status_code == 422  # not a uuid, and not coerced into one


async def test_a_child_binned_on_its_own_survives_its_tables_round_trip(
    client, session_factory, table_uuid
):
    """A table delete stamps every child with the table's own `deleted_at` and its
    restore reverses exactly that set. A view binned *earlier* carries a different
    timestamp, so it is left behind — and has to still be in the bin afterwards."""
    view = await _create(client, table_uuid, "views")
    assert (await client.delete(f"/api/tables/{table_uuid}/views/{view}")).status_code == 204

    assert (await client.delete(f"/api/tables/{table_uuid}")).status_code == 204
    assert (await client.post(f"/api/tables/{table_uuid}/restore")).status_code == 200

    binned = await client.get(f"/api/tables/{table_uuid}/views/deleted")
    assert [row["uuid"] for row in binned.json()] == [view]
    # The table's own default views came back live, so the restore did not simply
    # revive everything it found.
    live = await client.get(f"/api/tables/{table_uuid}/views/")
    assert live.status_code == 200
    assert view not in [row["uuid"] for row in live.json()]
    assert len(live.json()) >= 1


async def test_purging_a_view_takes_its_grants(client, session_factory, table_uuid):
    """`permission_grants` has no foreign key to its resource, so the hard delete is
    the only thing that can clear them — otherwise they outlive the row."""
    from src.auth.users.schemas import UserCreate
    from src.auth.users.services import UserService
    from src.permissions.models import PermissionGrant

    async with session_factory() as db:
        alice = await UserService.create(
            db, UserCreate(username="alice", email="a@example.com", password="pw")
        )
        alice_uuid = str(alice.uuid)

    view = await _create(client, table_uuid, "views")
    granted = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "view",
            "resource_uuid": view,
            "user_uuid": alice_uuid,
            "actions": ["view"],
        },
    )
    assert granted.status_code == 201, granted.text

    async with session_factory() as db:
        before = (
            await db.execute(
                select(PermissionGrant).where(
                    PermissionGrant.resource_type == "view"
                )
            )
        ).scalars().all()
    assert len(before) == 1

    assert (await client.delete(f"/api/tables/{table_uuid}/views/{view}")).status_code == 204
    assert (
        await client.delete(f"/api/tables/{table_uuid}/views/{view}/permanent")
    ).status_code == 204

    async with session_factory() as db:
        after = (
            await db.execute(
                select(PermissionGrant).where(
                    PermissionGrant.resource_type == "view"
                )
            )
        ).scalars().all()
    assert after == []


# --- columns, which are the odd kind: they own data inside every record ---


async def _record_data(client, table_uuid, record_uuid) -> dict:
    resp = await client.get(f"/api/tables/{table_uuid}/records/{record_uuid}")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _stored_data(session_factory, record_uuid) -> dict:
    """Straight off the row, past `shape_values` — the only way to see what a deleted
    column still holds."""
    async with session_factory() as db:
        row = (
            await db.execute(select(TableRecord).where(TableRecord.uuid == record_uuid))
        ).scalars().first()
        return dict(row.data or {})


async def test_a_deleted_columns_values_are_kept_but_not_served(
    client, session_factory, table_uuid
):
    """The two halves that make restore an undo without making delete a leak: the
    values stay on the row, and no reader sees them while the column is gone.

    `shape_values` drops keys with no live column, so the payload matches the schema.
    Nothing erases them, though — which is why permanent delete has to exist.
    """
    column = await _create(client, table_uuid, "columns")
    record = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {column: "secret value"}}
    )
    assert record.status_code == 201, record.text
    record_uuid = record.json()["uuid"]

    assert (
        await client.delete(f"/api/tables/{table_uuid}/columns/{column}")
    ).status_code == 204

    # Gone from the schema, and gone from the payload with it.
    live = await client.get(f"/api/tables/{table_uuid}/columns/")
    assert column not in [c["uuid"] for c in live.json()]
    assert column not in await _record_data(client, table_uuid, record_uuid)
    assert column not in (await client.get(f"/api/tables/{table_uuid}/records/")).json()[0]["data"]

    # Still on the row, untouched.
    assert (await _stored_data(session_factory, record_uuid))[column] == "secret value"

    restored = await client.post(f"/api/tables/{table_uuid}/columns/{column}/restore")
    assert restored.status_code == 200, restored.text
    assert column in [
        c["uuid"] for c in (await client.get(f"/api/tables/{table_uuid}/columns/")).json()
    ]
    # And served again the moment the column is live, never having been rewritten.
    assert (await _record_data(client, table_uuid, record_uuid))[column] == "secret value"


async def test_writing_a_record_does_not_erase_a_deleted_columns_values(
    client, session_factory, table_uuid
):
    """The hazard filtering could have introduced. A client reads a record without the
    deleted column's key and PATCHes it back; the merge starts from the *stored* dict
    (`_validate_data`), so what the reader never saw is not what the writer drops."""
    hidden = await _create(client, table_uuid, "columns")
    visible = (
        await client.post(
            f"/api/tables/{table_uuid}/columns/",
            json={"name": "shown", "display_name": "Shown", "type": "text", "config": {}},
        )
    ).json()["uuid"]
    record_uuid = (
        await client.post(
            f"/api/tables/{table_uuid}/records/",
            json={"data": {hidden: "keep me", visible: "before"}},
        )
    ).json()["uuid"]

    assert (
        await client.delete(f"/api/tables/{table_uuid}/columns/{hidden}")
    ).status_code == 204

    patched = await client.patch(
        f"/api/tables/{table_uuid}/records/{record_uuid}", json={"data": {visible: "after"}}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"][visible] == "after"

    assert (await _stored_data(session_factory, record_uuid))[hidden] == "keep me"
    # So the restore is still whole after a write it was invisible to.
    assert (
        await client.post(f"/api/tables/{table_uuid}/columns/{hidden}/restore")
    ).status_code == 200
    assert (await _record_data(client, table_uuid, record_uuid))[hidden] == "keep me"


async def test_permanent_delete_strips_the_column_from_every_record(
    client, session_factory, table_uuid
):
    """The one route that can free a deleted column's data. Without this the values
    outlive the column with nothing left to name them: no schema to read them through,
    no route to reach them, and no way to delete them."""
    column = await _create(client, table_uuid, "columns")
    records = []
    for value in ("first", "second", "third"):
        resp = await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {column: value}}
        )
        assert resp.status_code == 201, resp.text
        records.append(resp.json()["uuid"])

    assert (
        await client.delete(f"/api/tables/{table_uuid}/columns/{column}")
    ).status_code == 204
    assert (
        await client.delete(f"/api/tables/{table_uuid}/columns/{column}/permanent")
    ).status_code == 204

    for record_uuid in records:
        assert column not in await _record_data(client, table_uuid, record_uuid)

    # Gone from the table too, not merely stripped from the records.
    async with session_factory() as db:
        assert (
            await db.execute(select(Column).where(Column.uuid == column))
        ).scalars().first() is None


async def test_permanent_delete_leaves_other_columns_values_alone(client, table_uuid):
    """`data - key` removes one key; the record's other values are untouched."""
    doomed = await _create(client, table_uuid, "columns")
    keeper = (
        await client.post(
            f"/api/tables/{table_uuid}/columns/",
            json={"name": "keep", "display_name": "Keep", "type": "text", "config": {}},
        )
    ).json()["uuid"]
    record_uuid = (
        await client.post(
            f"/api/tables/{table_uuid}/records/",
            json={"data": {doomed: "goes", keeper: "stays"}},
        )
    ).json()["uuid"]

    assert (
        await client.delete(f"/api/tables/{table_uuid}/columns/{doomed}")
    ).status_code == 204
    assert (
        await client.delete(f"/api/tables/{table_uuid}/columns/{doomed}/permanent")
    ).status_code == 204

    data = await _record_data(client, table_uuid, record_uuid)
    assert doomed not in data
    assert data[keeper] == "stays"


async def test_restore_reports_that_the_table_lost_its_primary_column(
    client, table_uuid
):
    """Deleting the primary column nulls `tables.primary_column_id`, and restore does
    not put it back — so the response says so rather than leaving it to be noticed."""
    column = await _create(client, table_uuid, "columns")
    assert (
        await client.patch(
            f"/api/tables/{table_uuid}", json={"primary_column_uuid": column}
        )
    ).status_code == 200

    assert (
        await client.delete(f"/api/tables/{table_uuid}/columns/{column}")
    ).status_code == 204
    restored = await client.post(f"/api/tables/{table_uuid}/columns/{column}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["table_has_no_primary_column"] is True

    # The column is back but the table still has no primary — the report is honest.
    table = await client.get(f"/api/tables/{table_uuid}")
    assert table.json()["primary_column"] is None


async def test_the_report_is_about_the_table_now_not_this_columns_history(
    client, table_uuid
):
    """A primary chosen while the column sat binned is not something to flag."""
    column = await _create(client, table_uuid, "columns")
    other = (
        await client.post(
            f"/api/tables/{table_uuid}/columns/",
            json={"name": "title", "display_name": "Title", "type": "text", "config": {}},
        )
    ).json()["uuid"]

    await client.patch(f"/api/tables/{table_uuid}", json={"primary_column_uuid": column})
    assert (
        await client.delete(f"/api/tables/{table_uuid}/columns/{column}")
    ).status_code == 204
    # Someone picks a new primary before the restore happens.
    await client.patch(f"/api/tables/{table_uuid}", json={"primary_column_uuid": other})

    restored = await client.post(f"/api/tables/{table_uuid}/columns/{column}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["table_has_no_primary_column"] is False


async def test_a_restored_column_is_not_re_nested(client, table_uuid):
    """Restore clears `deleted_at` and nothing else. The nesting the delete cleared on
    dependents is not reported either — the pairing was wiped on *them*, so nothing
    records that it ever pointed here."""
    column = await _create(client, table_uuid, "columns")
    assert (
        await client.delete(f"/api/tables/{table_uuid}/columns/{column}")
    ).status_code == 204

    restored = await client.post(f"/api/tables/{table_uuid}/columns/{column}/restore")
    assert restored.status_code == 200, restored.text
    # The response speaks to the table's primary and nothing else.
    assert "table_has_no_primary_column" in restored.json()
