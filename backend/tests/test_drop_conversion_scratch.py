"""The one-off that clears what the old staged conversion left behind.

Column conversion used to build a **hidden** target column and backfill it before a
person decided anything. A dialog closed at the wrong moment, or a backfill that
raised after the column was already committed, left that column in the database with
its values in every record — invisible to every reader, because `Column.in_shape()`
excludes hidden rows, and unreachable by any code path.

Nothing sets `hidden` any more, so those rows can only shrink in number and can never
be finished. This job removes them and the record keys they own. It is run once after
the atomic conversion deploys; these tests pin the two properties that make it safe to
run at all — it takes the values with the columns, and it does nothing on a database
that has none.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select

from src.lists.columns.models import Column
from src.lists.items.models import ListItem
from src.maintenance import drop_conversion_scratch
from src.maintenance.drop_conversion_scratch import drop_scratch

ROWS = 5


@pytest.fixture(autouse=True)
def tiny_chunks(monkeypatch):
    """Two per batch over five columns, so the loop runs several times and the last
    batch has to land — an unterminated or single-pass loop is what would leave rows
    behind, which is the shape `test_maintenance_batching` pins for the other jobs."""
    monkeypatch.setattr(drop_conversion_scratch, "CHUNK", 2)


@pytest_asyncio.fixture
async def stranded(client, session_factory, list_uuid, add_column):
    """A live column with a value, plus five hidden columns with values of their own.

    Written directly, because the API can no longer produce this state — which is the
    whole reason the cleanup is a one-off rather than a recurring job.
    """
    live = await add_column(list_uuid, name="qty", display_name="Qty", type="text")
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {live: "5"}}
    )
    assert created.status_code == 201, created.text

    async with session_factory() as db:
        list_id = (
            await db.execute(select(Column.list_id).where(Column.uuid == live))
        ).scalars().one()
        item = (await db.execute(select(ListItem))).scalars().one()
        data = dict(item.data)
        hidden_keys = []
        for i in range(ROWS):
            scratch = Column(
                list_id=list_id,
                name=f"qty__converting_{i}",
                display_name=f"Qty (converting {i})",
                type="number",
                hidden=True,
                config={},
            )
            db.add(scratch)
            await db.flush()
            hidden_keys.append(str(scratch.uuid))
            data[str(scratch.uuid)] = i
        item.data = data
        await db.commit()
    return {"live": live, "hidden": hidden_keys}


async def test_it_destroys_every_hidden_column_and_its_values(
    session_factory, stranded
):
    destroyed = await drop_scratch()
    assert destroyed == ROWS

    async with session_factory() as db:
        assert (
            await db.execute(select(Column).where(Column.hidden.is_(True)))
        ).scalars().first() is None
        data = (await db.execute(select(ListItem.data))).scalars().one()

    # The values went with the columns. Leaving them would strand bytes in every
    # record with nothing left to name them — the reason this cannot be a plain
    # DELETE over the columns table.
    for key in stranded["hidden"]:
        assert key not in data
    # And the live column is untouched, which is the half that would be a disaster.
    assert data[stranded["live"]] == "5"


async def test_the_live_column_survives_and_still_reads(client, list_uuid, stranded):
    before = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    await drop_scratch()

    # The list's shape is exactly what it was. The hidden columns were never in this
    # listing to begin with — that is what made them invisible — so what this pins is
    # that removing them took nothing else with them.
    after = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    assert [c["uuid"] for c in after] == [c["uuid"] for c in before]
    assert stranded["live"] in [c["uuid"] for c in after]

    rows = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert rows[0]["data"][stranded["live"]] == "5"


async def test_a_dry_run_counts_without_touching_anything(session_factory, stranded):
    assert await drop_scratch(dry_run=True) == ROWS
    async with session_factory() as db:
        assert len(
            (
                await db.execute(select(Column).where(Column.hidden.is_(True)))
            ).scalars().all()
        ) == ROWS


async def test_it_is_idempotent_and_silent_on_a_clean_database(
    session_factory, stranded
):
    """Safe to run twice, and safe to leave in a runbook. A second pass finds nothing
    and writes nothing, which is what makes it harmless if somebody runs it again
    later wondering whether it was ever run at all."""
    assert await drop_scratch() == ROWS
    assert await drop_scratch() == 0
    assert await drop_scratch(dry_run=True) == 0
