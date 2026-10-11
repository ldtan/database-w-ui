"""Records outlive the `multiple` setting that shaped them.

Toggling a column's `multiple` does not rewrite its records — on a large table that
is a multi-minute UPDATE holding locks on every row, inside a web request. So the
service tolerates both shapes at its edges, the scrub stops caring about shape at
all, and a scheduled job converges the data afterwards. These tests pin all three.

They also pin the fourth edge, which was missed for a long time: **filtering and
sorting happen in Postgres**, where `coerce_shape` cannot reach, so `_field` has to
reproduce its rule in SQL. Until it did, a toggled column silently dropped every
un-converged record from a filtered view.
"""
import pytest_asyncio
from sqlalchemy import select

from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.tables.records.models import TableRecord
from src.maintenance.reconcile_shapes import reconcile


@pytest_asyncio.fixture
async def two_users(session_factory):
    async with session_factory() as db:
        a = await UserService.create(
            db, UserCreate(username="alice", email="a@example.com", password="pw")
        )
        b = await UserService.create(
            db, UserCreate(username="bob", email="b@example.com", password="pw")
        )
        return str(a.uuid), str(b.uuid)


async def _multi_column(client, table_uuid) -> str:
    resp = await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={
            "name": "reviewers",
            "display_name": "Reviewers",
            "type": "user",
            "config": {"multiple": True},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def _multi_choice_column(client, table_uuid, options) -> str:
    resp = await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={
            "name": "building",
            "display_name": "Building",
            "type": "choice",
            "config": {"multiple": True, "options": options},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def _view_with(client, table_uuid, config) -> str:
    views = (await client.get(f"/api/tables/{table_uuid}/views/")).json()
    view = views[0]["uuid"]
    resp = await client.patch(
        f"/api/tables/{table_uuid}/views/{view}", json={"config": config}
    )
    assert resp.status_code == 200, resp.text
    return view


async def _stored(session_factory, col: str):
    """The raw JSONB, bypassing the API's shape coercion."""
    async with session_factory() as db:
        row = (await db.execute(select(TableRecord.data))).scalars().first()
    return row.get(col)


async def test_toggling_leaves_the_records_alone(
    client, session_factory, table_uuid, two_users
):
    """The point of the whole design: the toggle is O(1). Whatever the table's size,
    it changes one config row and touches no records."""
    alice, bob = two_users
    col = await _multi_column(client, table_uuid)
    await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: [alice, bob]}}
    )

    await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}", json={"config": {"multiple": False}}
    )

    assert await _stored(session_factory, col) == [alice, bob]  # untouched on disk


async def test_reads_come_back_in_the_columns_shape(
    client, session_factory, table_uuid, two_users
):
    """Every reader sees the declared shape — coerced once, in the service, so no
    consumer has to know the record is a shape behind."""
    alice, bob = two_users
    col = await _multi_column(client, table_uuid)
    created = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: [alice, bob]}}
    )
    record = created.json()["uuid"]

    await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}", json={"config": {"multiple": False}}
    )

    listed = (await client.get(f"/api/tables/{table_uuid}/records/")).json()
    assert listed[0]["data"][col] == alice  # the first value, not ["alice", "bob"]

    one = (await client.get(f"/api/tables/{table_uuid}/records/{record}")).json()
    assert one["data"][col] == alice

    csv = (
        await client.post(f"/api/tables/{table_uuid}/export", json={"format": "csv"})
    ).text
    assert "alice" in csv and "bob" not in csv  # export coerces too

    # And the other direction: a bare value on a now-multiple column reads as a list.
    await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}", json={"config": {"multiple": True}}
    )
    listed = (await client.get(f"/api/tables/{table_uuid}/records/")).json()
    assert listed[0]["data"][col] == [alice, bob]  # still both — nothing was destroyed


async def test_writing_back_a_legacy_value_collapses_it(
    client, session_factory, table_uuid, two_users
):
    """A client that echoes the record it was given must not be told its own data is
    invalid. The write edge coerces, so the array becomes the first value."""
    alice, bob = two_users
    col = await _multi_column(client, table_uuid)
    created = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: [alice, bob]}}
    )
    record = created.json()["uuid"]
    await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}", json={"config": {"multiple": False}}
    )

    echoed = await client.patch(
        f"/api/tables/{table_uuid}/records/{record}", json={"data": {col: [alice, bob]}}
    )
    assert echoed.status_code == 200, echoed.text
    assert echoed.json()["data"][col] == alice
    assert await _stored(session_factory, col) == alice  # written in the new shape


async def test_clearing_a_reference_no_longer_cares_about_shape(
    client, session_factory, table_uuid, two_users
):
    """The bug this design could have reintroduced: with records a shape behind, SQL
    that picks its branch from the column's current `multiple` matches nothing and
    leaves the dead uuid behind. The purge-time clear no longer asks."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from src.auth.users.models import User
    from src.maintenance.purge_deleted_principals import purge_principals

    alice, bob = two_users
    col = await _multi_column(client, table_uuid)
    await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: [alice, bob]}}
    )
    await client.patch(  # records now hold an array; the column says single
        f"/api/tables/{table_uuid}/columns/{col}", json={"config": {"multiple": False}}
    )

    async with session_factory() as db:
        assert await UserService.delete(db, alice) is True
        # Past the retention window, so the purge finalizes the account.
        await db.execute(
            update(User)
            .where(User.uuid == alice)
            .values(deleted_at=datetime.now(timezone.utc) - timedelta(days=45))
        )
        await db.commit()

    await purge_principals(days=30)

    # Cleared out of the array on disk, even though the column now says single.
    assert await _stored(session_factory, col) == [bob]


async def test_a_filtered_view_finds_a_record_still_a_shape_behind(
    client, session_factory, table_uuid
):
    """The gap this closes. Filtering compiles to SQL over the raw JSONB, and `->>` on
    an array yields its *serialized* text — `["Annex"]`, never `Annex` — so a column
    toggled to single used to drop every un-converged record from a filtered view.
    Silently, and until the nightly reconcile caught up."""
    col = await _multi_choice_column(client, table_uuid, ["Annex", "Main"])
    await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {col: ["Annex"]}})
    await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}", json={"config": {"multiple": False}}
    )
    assert await _stored(session_factory, col) == ["Annex"]  # still an array on disk

    view = await _view_with(
        client,
        table_uuid,
        {
            "filters": {
                "conjunction": "and",
                "conditions": [
                    {"column_uuid": col, "operator": "is", "value": "Annex"}
                ],
            }
        },
    )
    rows = (
        await client.get(f"/api/tables/{table_uuid}/records/?view_uuid={view}")
    ).json()
    assert [r["data"][col] for r in rows] == ["Annex"]  # found, in the declared shape


async def test_an_empty_array_reads_as_empty_to_a_filter_too(
    client, session_factory, table_uuid
):
    """`coerce_shape` turns `[]` on a single column into `None`, so `is_empty` has to
    agree. Raw `->>` yields the string `[]` — neither NULL nor `''` — so the cell the
    grid shows as blank used to be invisible to the one filter that looks for blanks."""
    col = await _multi_choice_column(client, table_uuid, ["Annex", "Main"])
    await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {col: []}})
    await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}", json={"config": {"multiple": False}}
    )
    assert await _stored(session_factory, col) == []  # an empty array on disk
    listed = (await client.get(f"/api/tables/{table_uuid}/records/")).json()
    assert listed[0]["data"][col] is None  # which every reader already calls empty

    view = await _view_with(
        client,
        table_uuid,
        {
            "filters": {
                "conjunction": "and",
                "conditions": [{"column_uuid": col, "operator": "is_empty"}],
            }
        },
    )
    rows = (
        await client.get(f"/api/tables/{table_uuid}/records/?view_uuid={view}")
    ).json()
    assert len(rows) == 1


async def test_a_sort_orders_mixed_shapes_by_their_values(client, table_uuid):
    """Sorting has the same hole: ordering by raw text ranks the leading `[` rather
    than the value behind it. `["Zulu"]` and `"Alpha"` pin which rule is in force —
    by value the scalar leads, by raw text the array does, because this database
    collates punctuation ahead of letters.

    That last part is collation-dependent (`English_United States.1252` here; a glibc
    `en_US.UTF-8` server largely ignores punctuation and would mask it), which is why
    the empty-array case above carries the collation-independent half of this.
    """
    col = await _multi_choice_column(client, table_uuid, ["Alpha", "Zulu"])
    await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {col: ["Zulu"]}})
    await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}", json={"config": {"multiple": False}}
    )
    # Written after the toggle, so this one lands in the new shape.
    await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {col: "Alpha"}})

    view = await _view_with(
        client, table_uuid, {"sorts": [{"column_uuid": col, "direction": "asc"}]}
    )
    rows = (
        await client.get(f"/api/tables/{table_uuid}/records/?view_uuid={view}")
    ).json()
    assert [r["data"][col] for r in rows] == ["Alpha", "Zulu"]


async def test_a_multi_valued_column_still_matches_on_any_of_its_values(
    client, table_uuid
):
    """The fix must not overreach. A column that really is `multiple` holds an array
    legitimately, and `contains` searching the serialized array is the only element-wise
    matching the filter language has — narrowing it to the first element would be a
    regression dressed as a bug fix."""
    col = await _multi_choice_column(client, table_uuid, ["Annex", "Main"])
    await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: ["Main", "Annex"]}}
    )

    view = await _view_with(
        client,
        table_uuid,
        {
            "filters": {
                "conjunction": "and",
                "conditions": [
                    {"column_uuid": col, "operator": "contains", "value": "Annex"}
                ],
            }
        },
    )
    rows = (
        await client.get(f"/api/tables/{table_uuid}/records/?view_uuid={view}")
    ).json()
    assert len(rows) == 1  # matched on the second value, not only the first


async def test_reconcile_converges_the_stored_shape(
    client, session_factory, table_uuid, two_users
):
    alice, bob = two_users
    col = await _multi_column(client, table_uuid)
    await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: [alice, bob]}}
    )
    await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}", json={"config": {"multiple": False}}
    )

    planned = await reconcile(dry_run=True)
    assert sum(planned.values()) == 1  # one record out of shape
    assert await _stored(session_factory, col) == [alice, bob]  # dry run changes nothing

    changed = await reconcile()
    assert sum(changed.values()) == 1
    assert await _stored(session_factory, col) == alice  # converged, extras dropped

    assert await reconcile() == {}  # idempotent: nothing left to do
