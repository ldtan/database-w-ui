"""Ordering a listing by a reference column — by the label, not the uuid.

A `lookup` cell stores the target row's uuid and a `user`/`group` cell a principal's,
so ordering by the stored value ordered by uuid7 text: creation order scrambled by hex,
while the grid showed a name. These pin that a sort now reaches the *other* table for
the string the cell actually renders, and — just as important — that every way that
reach can fail degrades to the old uuid order instead of failing the listing.

**Every target list here is seeded in the order Zulu, Mike, Alpha.** uuid7 is
time-ordered, so that makes uuid order the exact reverse of label order and no assertion
can pass by accident on a sort that did nothing.

Assertions stay collation-independent — no punctuation, no case games, distinct first
letters. This database collates punctuation ahead of letters and a glibc server does not
(see test_shape_tolerance), and a test that disagrees with the deploy target is worse
than no test.
"""
from contextlib import asynccontextmanager

import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.schemas import UserCreate, UserGroupCreate
from src.auth.users.services import UserGroupService, UserService
from src.infrastructure import database as db_module

# Seeded in this order everywhere: uuid order, and so the id order the listing falls
# back to, is the reverse of alphabetical.
SEED = ["Zulu High", "Mike High", "Alpha High"]
ALPHABETICAL = ["Alpha High", "Mike High", "Zulu High"]


@pytest_asyncio.fixture
def make_client(engine, session_factory):
    """A client acting as `user`, against the same test database."""

    @asynccontextmanager
    async def _make(user):
        orig_engine, orig_factory = db_module.engine, db_module.SessionLocal
        db_module.engine, db_module.SessionLocal = engine, session_factory
        app = create_app()
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client:
                yield client
        finally:
            db_module.engine, db_module.SessionLocal = orig_engine, orig_factory

    return _make


async def _target(client, workspace_uuid, add_column, names=SEED):
    """A "Schools" list labelled by its primary column, seeded in `names` order."""
    target = (
        await client.post(
            "/api/lists/",
            json={
                "name": "schools",
                "display_name": "Schools",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    label_col = await add_column(target, name="name", display_name="Name", type="text")
    await client.patch(f"/api/lists/{target}", json={"primary_column_uuid": label_col})

    rows = {}
    for name in names:
        created = await client.post(
            f"/api/lists/{target}/items/", json={"data": {label_col: name}}
        )
        assert created.status_code == 201, created.text
        rows[name] = created.json()["uuid"]
    return target, label_col, rows


async def _host(client, list_uuid, add_column, target, values, **config):
    """A lookup column on the caller's list, plus one record per entry of `values`.

    A `None` entry is a record that leaves the cell unset — the empty case.
    """
    lookup = await add_column(
        list_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_list_uuid": target, **config},
    )
    for value in values:
        created = await client.post(
            f"/api/lists/{list_uuid}/items/",
            json={"data": {} if value is None else {lookup: value}},
        )
        assert created.status_code == 201, created.text
    return lookup


async def _sorted(client, list_uuid, column, direction="asc", **extra):
    query = f"sort_by={column}&sort_dir={direction}"
    for key, value in extra.items():
        query += f"&{key}={value}"
    resp = await client.get(f"/api/lists/{list_uuid}/items/?{query}")
    assert resp.status_code == 200, resp.text
    return resp


def _cells(resp, column):
    return [row["data"].get(column) for row in resp.json()]


def _labels(resp, column, rows):
    """The rendered label of each row's cell, in the order the page came back."""
    by_uuid = {uuid: name for name, uuid in rows.items()}
    return [by_uuid.get(cell) for cell in _cells(resp, column)]


async def test_a_lookup_sorts_by_its_label_not_its_uuid(
    client, list_uuid, workspace_uuid, add_column
):
    """The whole feature. Seeded so uuid order is the reverse of label order, which
    is what makes this fail loudly if the label never reaches the ORDER BY."""
    target, _, rows = await _target(client, workspace_uuid, add_column)
    lookup = await _host(client, list_uuid, add_column, target, list(rows.values()))

    asc = await _sorted(client, list_uuid, lookup)
    assert _labels(asc, lookup, rows) == ALPHABETICAL

    desc = await _sorted(client, list_uuid, lookup, "desc")
    assert _labels(desc, lookup, rows) == list(reversed(ALPHABETICAL))


async def test_an_explicit_display_column_decides_the_order(
    client, list_uuid, workspace_uuid, add_column
):
    """`display_column_uuid` is what the cell shows, so it is what the sort follows —
    even when the target's primary column would order the rows differently."""
    target, _, rows = await _target(client, workspace_uuid, add_column)
    code = await add_column(target, name="code", display_name="Code", type="text")
    # Codes deliberately rank the opposite way to the names.
    for name, letter in zip(ALPHABETICAL, ["c", "b", "a"]):
        await client.patch(
            f"/api/lists/{target}/items/{rows[name]}", json={"data": {code: letter}}
        )

    lookup = await _host(
        client,
        list_uuid,
        add_column,
        target,
        list(rows.values()),
        display_column_uuid=code,
    )

    asc = await _sorted(client, list_uuid, lookup)
    assert _labels(asc, lookup, rows) == list(reversed(ALPHABETICAL))


async def test_an_empty_cell_sorts_as_empty(
    client, list_uuid, workspace_uuid, add_column
):
    """Postgres' own NULL placement — last ascending, first descending — which is
    what every other column type in this listing already does."""
    target, _, rows = await _target(client, workspace_uuid, add_column)
    lookup = await _host(
        client, list_uuid, add_column, target, [*rows.values(), None]
    )

    asc = await _sorted(client, list_uuid, lookup)
    assert _labels(asc, lookup, rows) == [*ALPHABETICAL, None]

    desc = await _sorted(client, list_uuid, lookup, "desc")
    assert _labels(desc, lookup, rows) == [None, *reversed(ALPHABETICAL)]


async def test_a_target_row_the_caller_cannot_read_sorts_as_empty(
    client, make_client, session_factory, list_uuid, workspace_uuid, add_column
):
    """The security case, and the reason the join carries `item_view_filter`.

    A row-level grant withheld on the target must stay withheld. Ordering by the real
    label would hand it back by elimination — ask for the sorted page, bisect, read
    the label you were never given — so the row sorts as empty, exactly as it renders.
    """
    target, _, rows = await _target(client, workspace_uuid, add_column)
    # Alpha stops inheriting the list's grants and is granted to nobody.
    patched = await client.patch(
        f"/api/lists/{target}/items/{rows['Alpha High']}",
        json={"permissions_inherit": False},
    )
    assert patched.status_code == 200, patched.text

    lookup = await _host(client, list_uuid, add_column, target, list(rows.values()))

    async with session_factory() as db:
        linker = await UserService.create(
            db, UserCreate(username="linker", email="linker@example.com", password="pw")
        )
        linker_uuid = str(linker.uuid)
    for resource, uuid, actions in (
        ("list", list_uuid, ["view"]),
        ("list", target, ["lookup"]),
    ):
        grant = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": resource,
                "resource_uuid": uuid,
                "user_uuid": linker_uuid,
                "actions": actions,
            },
        )
        assert grant.status_code == 201, grant.text

    async with make_client(linker) as client_as_linker:
        asc = await _sorted(client_as_linker, list_uuid, lookup)
        # Alpha would lead alphabetically; unreadable, it sorts with the blanks.
        assert _labels(asc, lookup, rows) == ["Mike High", "Zulu High", "Alpha High"]

    # ...and the owner, who can read it, still gets the real order.
    owner = await _sorted(client, list_uuid, lookup)
    assert _labels(owner, lookup, rows) == ALPHABETICAL


async def test_without_lookup_on_the_target_the_sort_falls_back_to_uuid_order(
    client, make_client, session_factory, list_uuid, workspace_uuid, add_column
):
    """No labels to sort by, and none on screen either: every cell renders as a row
    stub built from the uuid, so uuid order *is* the order the caller sees. The one
    thing it must not do is fail."""
    target, _, rows = await _target(client, workspace_uuid, add_column)
    lookup = await _host(client, list_uuid, add_column, target, list(rows.values()))

    async with session_factory() as db:
        outsider = await UserService.create(
            db, UserCreate(username="outsider", email="out@example.com", password="pw")
        )
    grant = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "list",
            "resource_uuid": list_uuid,
            "user_uuid": str(outsider.uuid),
            "actions": ["view"],
        },
    )
    assert grant.status_code == 201, grant.text

    async with make_client(outsider) as client_as_outsider:
        asc = await _sorted(client_as_outsider, list_uuid, lookup)
        # Seeded Zulu, Mike, Alpha — so uuid order, not alphabetical.
        assert _labels(asc, lookup, rows) == SEED


async def test_a_target_that_is_named_but_missing_does_not_break_the_sort(
    client, session_factory, list_uuid, workspace_uuid, add_column
):
    """A column pointing at a list that is not live, with cells still holding uuids.

    Deleting a list no longer produces this — that path scrubs the values *and* the
    config (see clear_dangling_lookup_targets) — so it is written past the API, which
    is also how the population that sweep exists for came about. Every other read of
    such a column refuses loudly. A sort must not: ordering is a rendering concern, and
    failing the whole listing over it would take the grid down with the column.
    """
    target, _, rows = await _target(client, workspace_uuid, add_column)
    lookup = await _host(client, list_uuid, add_column, target, list(rows.values()))

    async with session_factory() as db:
        await db.execute(
            sa.text(
                "UPDATE columns SET config = jsonb_set("
                "  config, '{target_list_uuid}', to_jsonb(gen_random_uuid()::text)"
                ") WHERE uuid = :uuid"
            ),
            {"uuid": lookup},
        )
        await db.commit()

    asc = await _sorted(client, list_uuid, lookup)
    # Seeded Zulu, Mike, Alpha — uuid order, which is what the stubs on screen show.
    assert _labels(asc, lookup, rows) == SEED


async def test_a_cell_holding_something_other_than_a_uuid_does_not_fail_the_listing(
    client, session_factory, list_uuid, workspace_uuid, add_column
):
    """Written past the API on purpose — the write path normalizes to a uuid, and a
    column converted into a lookup is the way junk gets in. Casting the *cell* to uuid
    would raise InvalidTextRepresentationError and take the whole statement with it,
    which is why the cast is on `list_items.uuid` and this compares as text.
    """
    target, _, rows = await _target(client, workspace_uuid, add_column)
    lookup = await _host(client, list_uuid, add_column, target, list(rows.values()))

    listed = await client.get(f"/api/lists/{list_uuid}/items/")
    victim = listed.json()[0]["uuid"]
    async with session_factory() as db:
        await db.execute(
            sa.text(
                "UPDATE list_items SET data = data || jsonb_build_object("
                "  cast(:key AS text), 'not-a-uuid'::text"
                ") WHERE uuid = :uuid"
            ),
            {"key": lookup, "uuid": victim},
        )
        await db.commit()

    asc = await _sorted(client, list_uuid, lookup)
    assert len(asc.json()) == 3
    # Unmatchable, so it has no label and sorts with the blanks.
    assert _cells(asc, lookup)[-1] == "not-a-uuid"


async def test_a_multi_valued_lookup_sorts_by_its_first_label(
    client, list_uuid, workspace_uuid, add_column
):
    """One sortable reading of a multi-valued cell: its first link. Better than what
    the generic path gave, which ranked the serialized array by its leading bracket."""
    target, _, rows = await _target(client, workspace_uuid, add_column)
    lookup = await _host(
        client,
        list_uuid,
        add_column,
        target,
        [
            [rows["Zulu High"], rows["Alpha High"]],
            [rows["Mike High"], rows["Zulu High"]],
            [rows["Alpha High"], rows["Mike High"]],
        ],
        multiple=True,
    )

    asc = await _sorted(client, list_uuid, lookup)
    firsts = [cell[0] for cell in _cells(asc, lookup)]
    by_uuid = {uuid: name for name, uuid in rows.items()}
    assert [by_uuid[u] for u in firsts] == ALPHABETICAL


async def test_a_binned_or_archived_target_row_still_supplies_its_label(
    client, list_uuid, workspace_uuid, add_column
):
    """A held reference reads its label whatever the target row's lifecycle — only
    *search* excludes archived rows. Dropping them from the sort would order a row as
    blank while the screen shows its name."""
    target, _, rows = await _target(client, workspace_uuid, add_column)
    lookup = await _host(client, list_uuid, add_column, target, list(rows.values()))

    archived = await client.post(
        f"/api/lists/{target}/items/{rows['Alpha High']}/archive"
    )
    assert archived.status_code in (200, 204), archived.text
    assert (
        await client.delete(f"/api/lists/{target}/items/{rows['Mike High']}")
    ).status_code == 204

    asc = await _sorted(client, list_uuid, lookup)
    assert _labels(asc, lookup, rows) == ALPHABETICAL


async def test_grouping_by_a_lookup_groups_by_label(
    client, list_uuid, workspace_uuid, add_column
):
    """Grouping leads the ordering, and goes through the same terms — so the groups
    come back in label order, and a column that both groups and sorts is joined once."""
    target, _, rows = await _target(client, workspace_uuid, add_column)
    lookup = await _host(
        client,
        list_uuid,
        add_column,
        target,
        [rows["Zulu High"], rows["Alpha High"], rows["Zulu High"], rows["Mike High"]],
    )

    grouped = await _sorted(client, list_uuid, lookup, group_by=lookup)
    assert _labels(grouped, lookup, rows) == [
        "Alpha High",
        "Mike High",
        "Zulu High",
        "Zulu High",
    ]


async def test_the_join_does_not_duplicate_rows(
    client, list_uuid, workspace_uuid, add_column
):
    """A LEFT JOIN in a listing is only safe because it joins on a unique column. If
    it ever fanned out, the page would grow rows the count knows nothing about."""
    target, _, rows = await _target(client, workspace_uuid, add_column)
    lookup = await _host(client, list_uuid, add_column, target, list(rows.values()))

    unsorted = await client.get(f"/api/lists/{list_uuid}/items/")
    sorted_page = await _sorted(client, list_uuid, lookup)

    assert len(sorted_page.json()) == len(unsorted.json()) == 3
    assert sorted_page.headers["X-Total-Count"] == unsorted.headers["X-Total-Count"]
    assert {r["uuid"] for r in sorted_page.json()} == {
        r["uuid"] for r in unsorted.json()
    }


async def test_a_user_column_sorts_by_username(
    client, session_factory, list_uuid, add_column
):
    """Usernames, never the connected person's real name — the same rule the picker
    and the export follow, because person records are private."""
    async with session_factory() as db:
        people = {}
        for name in ("zeta", "mike", "alpha"):  # uuid order, reverse of alphabetical
            created = await UserService.create(
                db, UserCreate(username=name, email=f"{name}@example.com", password="pw")
            )
            people[name] = str(created.uuid)

    col = await add_column(
        list_uuid, name="assignee", display_name="Assignee", type="user"
    )
    for uuid in people.values():
        await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: uuid}})

    asc = await _sorted(client, list_uuid, col)
    by_uuid = {uuid: name for name, uuid in people.items()}
    assert [by_uuid[c] for c in _cells(asc, col)] == ["alpha", "mike", "zeta"]


async def test_a_group_column_sorts_by_display_name(
    client, session_factory, list_uuid, add_column
):
    """`display_name`, which is what the cell renders — deliberately not the `name`
    slug the export writes, because that one is a wire identifier a saved file
    resolves against and this one is what is on screen."""
    async with session_factory() as db:
        groups = {}
        # Slugs rank the opposite way to the display names, so this fails if the
        # sort follows `name`.
        for slug, display in (
            ("a_team", "Zebra"),
            ("m_team", "Mallard"),
            ("z_team", "Antelope"),
        ):
            created = await UserGroupService.create(
                db, UserGroupCreate(name=slug, display_name=display, user_uuids=[])
            )
            groups[display] = str(created.uuid)

    col = await add_column(list_uuid, name="team", display_name="Team", type="group")
    for uuid in groups.values():
        await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: uuid}})

    asc = await _sorted(client, list_uuid, col)
    by_uuid = {uuid: display for display, uuid in groups.items()}
    assert [by_uuid[c] for c in _cells(asc, col)] == ["Antelope", "Mallard", "Zebra"]
