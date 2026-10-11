"""The `lookup` permission action: sharing a table *as a lookup target only*.

`view` on a table hands over the whole table — it opens, it tables, it lands in
"Shared with me". A reference table ("Schools", "Departments", "Countries") usually
wants less than that: other people's lookup columns should resolve its row labels
without the table itself becoming theirs to browse. That is `lookup`.

The invariant these tests pin, from both sides:

- `lookup` resolves labels — the picker, search by linked label, and the export
  formatter all accept it, because all three read a label and nothing else.
- `lookup` opens nothing — the table stays out of `GET /tables/`, its workspace stays
  out of `GET /workspaces/`, and `GET /tables/{it}` is refused. The one exception is
  deliberate and narrow: `?lookup_targets=true`, which the target picker needs to be
  able to name a table before a column can point at it.

`view` confers `lookup` (see `_IMPLIES`), so every grant written before this action
existed keeps resolving labels exactly as it did — `test_view_still_resolves_labels`
is the regression guard for that.
"""
from contextlib import asynccontextmanager

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.infrastructure import database as db_module


@pytest_asyncio.fixture
def make_client(engine, session_factory):
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


async def _target_with_rows(client, workspace_uuid, add_column, names):
    """A "Schools" table labelled by its primary column."""
    target = (
        await client.post(
            "/api/tables/",
            json={
                "name": "schools",
                "display_name": "Schools",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    label_col = await add_column(target, name="name", display_name="Name", type="text")
    await client.patch(f"/api/tables/{target}", json={"primary_column_uuid": label_col})

    rows = {}
    for name in names:
        created = await client.post(
            f"/api/tables/{target}/records/", json={"data": {label_col: name}}
        )
        rows[name] = created.json()["uuid"]
    return target, rows


async def _user(session_factory, name):
    async with session_factory() as db:
        created = await UserService.create(
            db, UserCreate(username=name, email=f"{name}@example.com", password="pw")
        )
        return created, str(created.uuid)


async def _grant(client, resource_type, resource_uuid, user_uuid, actions):
    resp = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": resource_type,
            "resource_uuid": resource_uuid,
            "user_uuid": user_uuid,
            "actions": actions,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _shared_and_lookup_target_scenario(
    client, session_factory, workspace_uuid, table_uuid, add_column, name
):
    """`name` gets `view` on the host table and `lookup` on its target — the shape
    the whole feature exists for. Returns the user, the target, its rows and the
    lookup column."""
    target, rows = await _target_with_rows(
        client, workspace_uuid, add_column, ["Springfield High", "Shelbyville High"]
    )
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target},
    )
    user, user_uuid = await _user(session_factory, name)
    await _grant(client, "table", table_uuid, user_uuid, ["view"])
    await _grant(client, "table", target, user_uuid, ["lookup"])
    return user, target, rows, lookup


async def test_lookup_resolves_labels_without_sharing_the_table(
    client, make_client, session_factory, workspace_uuid, table_uuid, add_column
):
    """The whole point, in one test: the picker works, and the table it reads is
    nowhere the grantee can reach it."""
    user, target, _, lookup = await _shared_and_lookup_target_scenario(
        client, session_factory, workspace_uuid, table_uuid, add_column, "linker"
    )

    async with make_client(user) as linker:
        options = await linker.get(f"/api/tables/{table_uuid}/columns/{lookup}/options")
        assert options.status_code == 200, options.text
        assert sorted(o["label"] for o in options.json()) == [
            "Shelbyville High",
            "Springfield High",
        ]

        # ...and yet the target is not theirs. Not in "Shared with me",
        shared = await linker.get("/api/tables/?shared=true&limit=1000")
        assert target not in [row["uuid"] for row in shared.json()]
        # not counted in the total that pages it,
        assert shared.headers["X-Total-Count"] == "1"  # the host table, and only it
        # not openable,
        assert (await linker.get(f"/api/tables/{target}")).status_code == 403
        # and empty when read row by row. (Record routes resolve the table without a
        # table-level check — access to a record can be granted on its own — so this
        # answers 200 with nothing in it, not 403.)
        rows = await linker.get(f"/api/tables/{target}/records/")
        assert rows.status_code == 200, rows.text
        assert rows.json() == []


async def test_view_still_resolves_labels(
    client, make_client, session_factory, workspace_uuid, table_uuid, add_column
):
    """`view` confers `lookup`, so a grant written before this action existed keeps
    working — and keeps showing the table, which is what `view` is for."""
    target, _ = await _target_with_rows(
        client, workspace_uuid, add_column, ["Springfield High"]
    )
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target},
    )
    user, user_uuid = await _user(session_factory, "viewer")
    await _grant(client, "table", table_uuid, user_uuid, ["view"])
    await _grant(client, "table", target, user_uuid, ["view"])

    async with make_client(user) as viewer:
        options = await viewer.get(f"/api/tables/{table_uuid}/columns/{lookup}/options")
        assert [o["label"] for o in options.json()] == ["Springfield High"]

        shared = await viewer.get("/api/tables/?shared=true&limit=1000")
        assert target in [row["uuid"] for row in shared.json()]
        assert (await viewer.get(f"/api/tables/{target}")).status_code == 200


async def test_lookup_only_tables_are_offered_to_the_target_picker(
    client, make_client, session_factory, workspace_uuid, add_column
):
    """A target you cannot name is a target you cannot point a column at. The
    picker's query — and only it — asks for the wider set."""
    target, _ = await _target_with_rows(client, workspace_uuid, add_column, ["Alpha"])
    user, user_uuid = await _user(session_factory, "picker")
    await _grant(client, "table", target, user_uuid, ["lookup"])

    async with make_client(user) as picker:
        plain = await picker.get("/api/tables/?shared=true&limit=1000")
        assert [row["uuid"] for row in plain.json()] == []
        assert plain.headers["X-Total-Count"] == "0"

        offered = await picker.get(
            "/api/tables/?shared=true&lookup_targets=true&limit=1000"
        )
        assert [row["uuid"] for row in offered.json()] == [target]
        assert offered.headers["X-Total-Count"] == "1"

        # And the column can then actually be built on it.
        ws = (
            await picker.post(
                "/api/workspaces/", json={"name": "ow", "display_name": "Own"}
            )
        ).json()["uuid"]
        mine = (
            await picker.post(
                "/api/tables/",
                json={"name": "mine", "display_name": "Mine", "workspace_uuid": ws},
            )
        ).json()["uuid"]
        created = await picker.post(
            f"/api/tables/{mine}/columns/",
            json={
                "name": "school",
                "display_name": "School",
                "type": "lookup",
                "config": {"target_table_uuid": target},
            },
        )
        assert created.status_code == 201, created.text


async def test_a_cross_owner_lookup_accepts_a_write(
    client, make_client, session_factory, workspace_uuid, add_column
):
    """The counterpart to the test above: a column built on someone else's table is
    not merely creatable, it is *writable*.

    It used not to be. `ColumnService._validate_config` was relaxed to "any table you
    can `lookup`", but `TableRecordService._lookup_target_id` still resolved the target
    scoped to the owner of the table holding the column — so the column was created
    happily and every cell write failed with "points at a missing target table".
    """
    target, rows = await _target_with_rows(
        client, workspace_uuid, add_column, ["Springfield High", "Shelbyville High"]
    )
    user, user_uuid = await _user(session_factory, "writer")
    await _grant(client, "table", target, user_uuid, ["lookup"])

    async with make_client(user) as writer:
        ws = (
            await writer.post(
                "/api/workspaces/", json={"name": "ow", "display_name": "Own"}
            )
        ).json()["uuid"]
        mine = (
            await writer.post(
                "/api/tables/",
                json={"name": "mine", "display_name": "Mine", "workspace_uuid": ws},
            )
        ).json()["uuid"]
        lookup = (
            await writer.post(
                f"/api/tables/{mine}/columns/",
                json={
                    "name": "school",
                    "display_name": "School",
                    "type": "lookup",
                    "config": {"target_table_uuid": target},
                },
            )
        ).json()["uuid"]

        # By uuid, the way the UI sends it.
        by_uuid = await writer.post(
            f"/api/tables/{mine}/records/",
            json={"data": {lookup: rows["Springfield High"]}},
        )
        assert by_uuid.status_code == 201, by_uuid.text
        assert by_uuid.json()["data"][lookup] == rows["Springfield High"]

        # And by label, the way an imported file carries it.
        by_label = await writer.post(
            f"/api/tables/{mine}/records/", json={"data": {lookup: "Shelbyville High"}}
        )
        assert by_label.status_code == 201, by_label.text
        assert by_label.json()["data"][lookup] == rows["Shelbyville High"]


async def test_writing_a_lookup_takes_lookup_on_the_target(
    client, make_client, session_factory, workspace_uuid, table_uuid, add_column
):
    """Dropping the owner scope above removed what was, in effect, the write path's
    access control — owning both tables made a leak impossible. This is the check
    that replaces it, and it is not redundant with the read path: resolution matches
    a row by its **label**, so an unguarded write is a way to probe the contents of a
    table you cannot read by guessing at them.

    The grantee here can create records on the host table and has no grant at all on
    the table its lookup column points at.
    """
    target, rows = await _target_with_rows(
        client, workspace_uuid, add_column, ["Springfield High"]
    )
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target},
    )
    user, user_uuid = await _user(session_factory, "stranger")
    await _grant(client, "table", table_uuid, user_uuid, ["create"])

    async with make_client(user) as stranger:
        refused = await stranger.post(
            f"/api/tables/{table_uuid}/records/",
            json={"data": {lookup: rows["Springfield High"]}},
        )
        assert refused.status_code == 403, refused.text

        # A guessed label is refused identically, and before it is resolved — so the
        # response cannot be read as "that row exists" or "it does not".
        probe = await stranger.post(
            f"/api/tables/{table_uuid}/records/",
            json={"data": {lookup: "Springfield High"}},
        )
        assert probe.status_code == 403, probe.text
        assert (
            probe.json()["detail"]
            == (
                await stranger.post(
                    f"/api/tables/{table_uuid}/records/",
                    json={"data": {lookup: "No Such School"}},
                )
            ).json()["detail"]
        )

        # The record itself was creatable; only the lookup cell was the problem.
        allowed = await stranger.post(f"/api/tables/{table_uuid}/records/", json={"data": {}})
        assert allowed.status_code == 201, allowed.text


async def test_lookup_does_not_expose_the_targets_schema(
    client, make_client, session_factory, workspace_uuid, add_column
):
    """`lookup` hands over row labels, not the shape of the table they come from.
    The column dialog degrades to the server's default label column for this."""
    target, _ = await _target_with_rows(client, workspace_uuid, add_column, ["Alpha"])
    user, user_uuid = await _user(session_factory, "prober")
    await _grant(client, "table", target, user_uuid, ["lookup"])

    async with make_client(user) as prober:
        assert (await prober.get(f"/api/tables/{target}/columns/")).status_code == 403


async def test_lookup_still_obeys_per_record_grants(
    client, make_client, session_factory, workspace_uuid, table_uuid, add_column
):
    """`lookup` relaxes the table-level gate, not row-level ones. A record taken out
    of the table's umbrella stays out of the picker."""
    target, rows = await _target_with_rows(
        client, workspace_uuid, add_column, ["Public", "Secret"]
    )
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target},
    )
    secret = rows["Secret"]
    await client.patch(
        f"/api/tables/{target}/records/{secret}", json={"permissions_inherit": False}
    )

    user, user_uuid = await _user(session_factory, "nosy")
    await _grant(client, "table", table_uuid, user_uuid, ["view"])
    await _grant(client, "table", target, user_uuid, ["lookup"])

    async with make_client(user) as nosy:
        offered = await nosy.get(f"/api/tables/{table_uuid}/columns/{lookup}/options")
        assert [o["label"] for o in offered.json()] == ["Public"]
        # Nor by asking for the row directly.
        direct = await nosy.get(
            f"/api/tables/{table_uuid}/columns/{lookup}/options?uuid={secret}"
        )
        assert direct.json() == []


async def test_lookup_resolves_labels_in_an_export(
    client, make_client, session_factory, workspace_uuid, table_uuid, add_column
):
    """Every surface that resolves a label takes the same action, or the feature
    would work in the grid and not in the file it exports."""
    user, target, rows, lookup = await _shared_and_lookup_target_scenario(
        client, session_factory, workspace_uuid, table_uuid, add_column, "exporter"
    )
    await client.post(
        f"/api/tables/{table_uuid}/records/",
        json={"data": {lookup: rows["Springfield High"]}},
    )

    async with make_client(user) as exporter:
        export = await exporter.post(
            f"/api/tables/{table_uuid}/export", json={"format": "csv"}
        )
        assert export.status_code == 200, export.text
        assert "Springfield High" in export.text


async def test_lookup_resolves_labels_when_searching_by_them(
    client, make_client, session_factory, workspace_uuid, table_uuid, add_column
):
    """Searching a linked row's label reads the target table, so it takes `lookup`
    too — otherwise a label the grid shows would match nothing in the search box."""
    user, target, rows, lookup = await _shared_and_lookup_target_scenario(
        client, session_factory, workspace_uuid, table_uuid, add_column, "searcher"
    )
    name = await add_column(table_uuid, name="name", display_name="Name", type="text")
    await client.post(
        f"/api/tables/{table_uuid}/records/",
        json={"data": {name: "Ann", lookup: rows["Springfield High"]}},
    )

    async with make_client(user) as searcher:
        found = await searcher.get(f"/api/tables/{table_uuid}/records/?q=springfield")
        assert len(found.json()) == 1


async def test_allow_create_round_trips_and_survives_a_merge_patch(
    client, workspace_uuid, table_uuid, add_column
):
    """The toggle is stored config like any other, and a config PATCH *merges* —
    so a later edit that says nothing about it must not switch it off."""
    target, _ = await _target_with_rows(client, workspace_uuid, add_column, ["Alpha"])
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target, "allow_create": True},
    )

    stored = (await client.get(f"/api/tables/{table_uuid}/columns/")).json()
    column = next(c for c in stored if c["uuid"] == lookup)
    assert column["config"]["allow_create"] is True

    # An unrelated edit, of the kind the dialog makes constantly.
    patched = await client.patch(
        f"/api/tables/{table_uuid}/columns/{lookup}",
        json={"config": {"target_table_uuid": target, "multiple": True}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["config"]["allow_create"] is True


async def test_the_toggle_does_not_let_a_lookup_only_grantee_add_rows(
    client, make_client, session_factory, workspace_uuid, table_uuid, add_column
):
    """The load-bearing one. `allow_create` is an affordance stored on the
    *referring* column, and a column's config cannot be a grant on somebody
    else's table — least of all this one, since `lookup` is the action that
    deliberately hands over labels without opening the table.

    The grantee here has exactly what the picker needs and nothing more.
    """
    user, target, rows, lookup = await _shared_and_lookup_target_scenario(
        client, session_factory, workspace_uuid, table_uuid, add_column, "hopeful"
    )
    await client.patch(
        f"/api/tables/{table_uuid}/columns/{lookup}",
        json={"config": {"target_table_uuid": target, "allow_create": True}},
    )

    async with make_client(user) as hopeful:
        # The picker still works — that is what they were given.
        options = await hopeful.get(f"/api/tables/{table_uuid}/columns/{lookup}/options")
        assert options.status_code == 200, options.text

        # Adding to the target does not, and the target's schema stays refused,
        # so there is nothing to build a row out of either.
        label_col = (await client.get(f"/api/tables/{target}/columns/")).json()[0]["uuid"]
        refused = await hopeful.post(
            f"/api/tables/{target}/records/", json={"data": {label_col: "Ogdenville High"}}
        )
        assert refused.status_code == 403, refused.text
        assert (await hopeful.get(f"/api/tables/{target}/columns/")).status_code == 403


async def test_create_on_the_target_is_what_lets_a_row_be_added(
    client, make_client, session_factory, workspace_uuid, table_uuid, add_column
):
    """The other half: with `create` on the target the row goes in, and the cell
    that prompted it accepts the result.

    Note what is *not* granted — nothing extra was written for the schema read.
    `create` confers `view` (permissions/enums.py `_IMPLIES`), which is the whole
    reason this feature discloses nothing: anyone who can be offered the button
    could already have opened the target table and read its columns.
    """
    user, target, rows, lookup = await _shared_and_lookup_target_scenario(
        client, session_factory, workspace_uuid, table_uuid, add_column, "adder"
    )
    await client.patch(
        f"/api/tables/{table_uuid}/columns/{lookup}",
        json={"config": {"target_table_uuid": target, "allow_create": True}},
    )
    user_uuid = str(user.uuid)
    await _grant(client, "table", target, user_uuid, ["create"])
    await _grant(client, "table", table_uuid, user_uuid, ["create"])

    async with make_client(user) as adder:
        # The schema arrives on the `view` that `create` carries, unasked for.
        columns = await adder.get(f"/api/tables/{target}/columns/")
        assert columns.status_code == 200, columns.text
        label_col = columns.json()[0]["uuid"]

        created = await adder.post(
            f"/api/tables/{target}/records/", json={"data": {label_col: "Ogdenville High"}}
        )
        assert created.status_code == 201, created.text
        new_row = created.json()["uuid"]

        # And the cell the dialog was opened from takes it.
        linked = await adder.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {lookup: new_row}}
        )
        assert linked.status_code == 201, linked.text
        assert linked.json()["data"][lookup] == new_row


async def test_the_toggle_never_reaches_the_write_path(
    client, workspace_uuid, table_uuid, add_column
):
    """`allow_create` is honoured by the pickers and by nothing else.

    An unknown label is still an error on every write path — including the two
    that have no user in front of them to click anything. Were it otherwise, one
    mistyped column in a five-thousand-row file would silently mint five thousand
    rows in somebody else's reference table.
    """
    target, _ = await _target_with_rows(client, workspace_uuid, add_column, ["Alpha"])
    lookup = await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target, "allow_create": True},
    )

    direct = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {lookup: "Nowhere High"}}
    )
    assert direct.status_code == 400, direct.text
    assert "unknown row" in direct.json()["detail"]

    imported = await client.post(
        f"/api/tables/{table_uuid}/import",
        files={"file": ("rows.csv", "school\nNowhere High\n", "text/csv")},
    )
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["created"] == 0
    assert len(body["errors"]) == 1

    # Nothing was added to the target on the way past.
    assert len((await client.get(f"/api/tables/{target}/records/")).json()) == 1


async def test_a_lookup_grant_on_a_workspace_hides_the_workspace_too(
    client, make_client, session_factory, workspace_uuid, add_column
):
    """`lookup` cascades to a workspace's tables like any other action, and the
    collection filters refuse it at both levels — the workspace is no more visible
    than the tables inside it."""
    target, _ = await _target_with_rows(client, workspace_uuid, add_column, ["Alpha"])
    user, user_uuid = await _user(session_factory, "cascader")
    await _grant(client, "workspace", workspace_uuid, user_uuid, ["lookup"])

    async with make_client(user) as cascader:
        # Their own personal workspace is theirs; the granted one is not.
        visible = await cascader.get("/api/workspaces/?limit=1000")
        assert workspace_uuid not in [ws["uuid"] for ws in visible.json()]
        assert (await cascader.get("/api/tables/?shared=true")).json() == []
        # But the inherited grant is enough to point a column at the table.
        offered = await cascader.get(
            "/api/tables/?shared=true&lookup_targets=true&limit=1000"
        )
        assert target in [row["uuid"] for row in offered.json()]
