"""The one-off that repairs lookups whose target list went before the delete-path fix.

`ListService._scrub_references_to_lists` now clears the referencing column's config
along with its values, so a list deleted today leaves its dependants as unconfigured
lookups that can simply be retargeted. Columns that lost their target *before* that
still name a list that is not there — and by deliberate design that is the case the
read path refuses rather than tolerates, so those columns stay stuck: 404ing options,
a config PATCH that cannot be saved, and a ticked `required` that freezes writes to
the list holding them.

These tests pin what makes the sweep safe to point at a production database: it
repairs exactly the dangling columns, it leaves healthy ones and already-cleared ones
alone, and running it twice is the same as running it once.
"""
import pytest_asyncio
from sqlalchemy import select
from uuid import uuid7

from src.lists.columns.models import Column
from src.maintenance.clear_dangling_lookup_targets import clear_dangling, due


async def _make_list(client, workspace_uuid, name):
    resp = await client.post(
        "/api/lists/",
        json={"name": name, "display_name": name, "workspace_uuid": workspace_uuid},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


@pytest_asyncio.fixture
async def dangling(client, session_factory, workspace_uuid, add_column):
    """One lookup left naming a list that is not there, and one pointing somewhere real.

    The dangling state is written behind the service, because the delete path no
    longer produces it — which is the whole reason this is a one-off rather than a
    recurring job.
    """
    authors = await _make_list(client, workspace_uuid, "authors")
    aname = await add_column(
        authors, name="full_name", display_name="Name", type="text"
    )
    books = await _make_list(client, workspace_uuid, "books")
    broken = await add_column(
        books,
        name="author",
        display_name="Author",
        type="lookup",
        config={
            "target_list_uuid": authors,
            "display_column_uuid": aname,
            "required": True,
        },
    )
    healthy = await add_column(
        books,
        name="editor",
        display_name="Editor",
        type="lookup",
        config={"target_list_uuid": authors, "required": True},
    )

    async with session_factory() as db:
        column = await db.scalar(select(Column).where(Column.uuid == broken))
        column.config = {**column.config, "target_list_uuid": str(uuid7())}
        await db.commit()

    return {"books": books, "broken": broken, "healthy": healthy}


async def _config(session_factory, uuid):
    async with session_factory() as db:
        column = await db.scalar(select(Column).where(Column.uuid == uuid))
        return dict(column.config or {})


async def test_it_clears_the_dangling_column_and_only_that_one(
    session_factory, dangling
):
    assert await clear_dangling() == 1

    broken = await _config(session_factory, dangling["broken"])
    assert broken["target_list_uuid"] is None
    assert broken["display_column_uuid"] is None
    # `required` too. A required cell that cannot be filled refuses every write to
    # the list, and cannot be un-ticked on its own while the target is unresolvable.
    assert broken["required"] is False

    # The healthy lookup is untouched, `required` included — this sweep repairs, it
    # does not relax constraints that are still doing their job.
    healthy = await _config(session_factory, dangling["healthy"])
    assert healthy["target_list_uuid"] is not None
    assert healthy["required"] is True


async def test_the_dry_run_writes_nothing(session_factory, dangling):
    assert await clear_dangling(dry_run=True) == 1
    broken = await _config(session_factory, dangling["broken"])
    assert broken["target_list_uuid"] is not None
    assert broken["required"] is True


async def test_a_second_run_finds_nothing(session_factory, dangling):
    """Idempotent, which is what makes it safe to leave in the workflow.

    The cleared column must not come back around on the next pass: its target is now
    absent rather than unresolvable, and the query's `IS NOT NULL` guard is what
    tells those two apart.
    """
    assert await clear_dangling() == 1
    assert await clear_dangling() == 0
    assert await clear_dangling(dry_run=True) == 0


async def test_an_uppercase_target_uuid_is_left_alone(session_factory, dangling):
    """The sweep writes, so a false positive here destroys a working column.

    Postgres renders a uuid lowercase-hyphenated while `coerce_uuid` accepts uppercase
    and braces, so a config written in any other form resolves perfectly everywhere in
    the app. A case-sensitive comparison would call this column dangling and blank it.
    """
    async with session_factory() as db:
        column = await db.scalar(
            select(Column).where(Column.uuid == dangling["healthy"])
        )
        target = column.config["target_list_uuid"].upper()
        column.config = {**column.config, "target_list_uuid": target}
        await db.commit()

    assert await clear_dangling() == 1  # the genuinely broken one, and only it
    healthy = await _config(session_factory, dangling["healthy"])
    assert healthy["target_list_uuid"] is not None
    assert healthy["required"] is True


async def test_the_dry_run_names_what_it_would_clear(session_factory, dangling):
    """A count is not enough: this is the last record of what each column pointed at."""
    rows = await due()
    assert len(rows) == 1
    _, uuid, name, target = rows[0]
    assert str(uuid) == dangling["broken"]
    assert name == "author"
    assert target is not None


async def test_a_column_whose_target_is_not_even_a_uuid_is_cleared(
    session_factory, dangling
):
    """A hand-edited config must be repaired, not raise.

    The query compares the list's uuid cast to text rather than casting the config
    value to uuid — casting the other way would fail the whole sweep on one bad row.
    """
    async with session_factory() as db:
        column = await db.scalar(
            select(Column).where(Column.uuid == dangling["healthy"])
        )
        column.config = {**column.config, "target_list_uuid": "not-a-uuid"}
        await db.commit()

    assert await clear_dangling() == 2
    healthy = await _config(session_factory, dangling["healthy"])
    assert healthy["target_list_uuid"] is None
