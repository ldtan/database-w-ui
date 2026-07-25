"""The `lookup` permission action: sharing a list *as a lookup target only*.

`view` on a list hands over the whole list — it opens, it lists, it lands in
"Shared with me". A reference list ("Schools", "Departments", "Countries") usually
wants less than that: other people's lookup columns should resolve its row labels
without the list itself becoming theirs to browse. That is `lookup`.

The invariant these tests pin, from both sides:

- `lookup` resolves labels — the picker, search by linked label, and the export
  formatter all accept it, because all three read a label and nothing else.
- `lookup` opens nothing — the list stays out of `GET /lists/`, its workspace stays
  out of `GET /workspaces/`, and `GET /lists/{it}` is refused. The one exception is
  deliberate and narrow: `?lookup_targets=true`, which the target picker needs to be
  able to name a list before a column can point at it.

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
    """A "Schools" list labelled by its primary column."""
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
    client, session_factory, workspace_uuid, list_uuid, add_column, name
):
    """`name` gets `view` on the host list and `lookup` on its target — the shape
    the whole feature exists for. Returns the user, the target, its rows and the
    lookup column."""
    target, rows = await _target_with_rows(
        client, workspace_uuid, add_column, ["Springfield High", "Shelbyville High"]
    )
    lookup = await add_column(
        list_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_list_uuid": target},
    )
    user, user_uuid = await _user(session_factory, name)
    await _grant(client, "list", list_uuid, user_uuid, ["view"])
    await _grant(client, "list", target, user_uuid, ["lookup"])
    return user, target, rows, lookup


async def test_lookup_resolves_labels_without_sharing_the_list(
    client, make_client, session_factory, workspace_uuid, list_uuid, add_column
):
    """The whole point, in one test: the picker works, and the list it reads is
    nowhere the grantee can reach it."""
    user, target, _, lookup = await _shared_and_lookup_target_scenario(
        client, session_factory, workspace_uuid, list_uuid, add_column, "linker"
    )

    async with make_client(user) as linker:
        options = await linker.get(f"/api/lists/{list_uuid}/columns/{lookup}/options")
        assert options.status_code == 200, options.text
        assert sorted(o["label"] for o in options.json()) == [
            "Shelbyville High",
            "Springfield High",
        ]

        # ...and yet the target is not theirs. Not in "Shared with me",
        shared = await linker.get("/api/lists/?shared=true&limit=1000")
        assert target not in [row["uuid"] for row in shared.json()]
        # not counted in the total that pages it,
        assert shared.headers["X-Total-Count"] == "1"  # the host list, and only it
        # not openable,
        assert (await linker.get(f"/api/lists/{target}")).status_code == 403
        # and empty when read row by row. (Item routes resolve the list without a
        # list-level check — access to a record can be granted on its own — so this
        # answers 200 with nothing in it, not 403.)
        rows = await linker.get(f"/api/lists/{target}/items/")
        assert rows.status_code == 200, rows.text
        assert rows.json() == []


async def test_view_still_resolves_labels(
    client, make_client, session_factory, workspace_uuid, list_uuid, add_column
):
    """`view` confers `lookup`, so a grant written before this action existed keeps
    working — and keeps showing the list, which is what `view` is for."""
    target, _ = await _target_with_rows(
        client, workspace_uuid, add_column, ["Springfield High"]
    )
    lookup = await add_column(
        list_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_list_uuid": target},
    )
    user, user_uuid = await _user(session_factory, "viewer")
    await _grant(client, "list", list_uuid, user_uuid, ["view"])
    await _grant(client, "list", target, user_uuid, ["view"])

    async with make_client(user) as viewer:
        options = await viewer.get(f"/api/lists/{list_uuid}/columns/{lookup}/options")
        assert [o["label"] for o in options.json()] == ["Springfield High"]

        shared = await viewer.get("/api/lists/?shared=true&limit=1000")
        assert target in [row["uuid"] for row in shared.json()]
        assert (await viewer.get(f"/api/lists/{target}")).status_code == 200


async def test_lookup_only_lists_are_offered_to_the_target_picker(
    client, make_client, session_factory, workspace_uuid, add_column
):
    """A target you cannot name is a target you cannot point a column at. The
    picker's query — and only it — asks for the wider set."""
    target, _ = await _target_with_rows(client, workspace_uuid, add_column, ["Alpha"])
    user, user_uuid = await _user(session_factory, "picker")
    await _grant(client, "list", target, user_uuid, ["lookup"])

    async with make_client(user) as picker:
        plain = await picker.get("/api/lists/?shared=true&limit=1000")
        assert [row["uuid"] for row in plain.json()] == []
        assert plain.headers["X-Total-Count"] == "0"

        offered = await picker.get(
            "/api/lists/?shared=true&lookup_targets=true&limit=1000"
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
                "/api/lists/",
                json={"name": "mine", "display_name": "Mine", "workspace_uuid": ws},
            )
        ).json()["uuid"]
        created = await picker.post(
            f"/api/lists/{mine}/columns/",
            json={
                "name": "school",
                "display_name": "School",
                "type": "lookup",
                "config": {"target_list_uuid": target},
            },
        )
        assert created.status_code == 201, created.text


async def test_lookup_does_not_expose_the_targets_schema(
    client, make_client, session_factory, workspace_uuid, add_column
):
    """`lookup` hands over row labels, not the shape of the list they come from.
    The column dialog degrades to the server's default label column for this."""
    target, _ = await _target_with_rows(client, workspace_uuid, add_column, ["Alpha"])
    user, user_uuid = await _user(session_factory, "prober")
    await _grant(client, "list", target, user_uuid, ["lookup"])

    async with make_client(user) as prober:
        assert (await prober.get(f"/api/lists/{target}/columns/")).status_code == 403


async def test_lookup_still_obeys_per_record_grants(
    client, make_client, session_factory, workspace_uuid, list_uuid, add_column
):
    """`lookup` relaxes the list-level gate, not row-level ones. A record taken out
    of the list's umbrella stays out of the picker."""
    target, rows = await _target_with_rows(
        client, workspace_uuid, add_column, ["Public", "Secret"]
    )
    lookup = await add_column(
        list_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_list_uuid": target},
    )
    secret = rows["Secret"]
    await client.patch(
        f"/api/lists/{target}/items/{secret}", json={"permissions_inherit": False}
    )

    user, user_uuid = await _user(session_factory, "nosy")
    await _grant(client, "list", list_uuid, user_uuid, ["view"])
    await _grant(client, "list", target, user_uuid, ["lookup"])

    async with make_client(user) as nosy:
        offered = await nosy.get(f"/api/lists/{list_uuid}/columns/{lookup}/options")
        assert [o["label"] for o in offered.json()] == ["Public"]
        # Nor by asking for the row directly.
        direct = await nosy.get(
            f"/api/lists/{list_uuid}/columns/{lookup}/options?uuid={secret}"
        )
        assert direct.json() == []


async def test_lookup_resolves_labels_in_an_export(
    client, make_client, session_factory, workspace_uuid, list_uuid, add_column
):
    """Every surface that resolves a label takes the same action, or the feature
    would work in the grid and not in the file it exports."""
    user, target, rows, lookup = await _shared_and_lookup_target_scenario(
        client, session_factory, workspace_uuid, list_uuid, add_column, "exporter"
    )
    await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {lookup: rows["Springfield High"]}},
    )

    async with make_client(user) as exporter:
        export = await exporter.post(
            f"/api/lists/{list_uuid}/export", json={"format": "csv"}
        )
        assert export.status_code == 200, export.text
        assert "Springfield High" in export.text


async def test_lookup_resolves_labels_when_searching_by_them(
    client, make_client, session_factory, workspace_uuid, list_uuid, add_column
):
    """Searching a linked row's label reads the target list, so it takes `lookup`
    too — otherwise a label the grid shows would match nothing in the search box."""
    user, target, rows, lookup = await _shared_and_lookup_target_scenario(
        client, session_factory, workspace_uuid, list_uuid, add_column, "searcher"
    )
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {name: "Ann", lookup: rows["Springfield High"]}},
    )

    async with make_client(user) as searcher:
        found = await searcher.get(f"/api/lists/{list_uuid}/items/?q=springfield")
        assert len(found.json()) == 1


async def test_a_lookup_grant_on_a_workspace_hides_the_workspace_too(
    client, make_client, session_factory, workspace_uuid, add_column
):
    """`lookup` cascades to a workspace's lists like any other action, and the
    collection filters refuse it at both levels — the workspace is no more visible
    than the lists inside it."""
    target, _ = await _target_with_rows(client, workspace_uuid, add_column, ["Alpha"])
    user, user_uuid = await _user(session_factory, "cascader")
    await _grant(client, "workspace", workspace_uuid, user_uuid, ["lookup"])

    async with make_client(user) as cascader:
        # Their own personal workspace is theirs; the granted one is not.
        visible = await cascader.get("/api/workspaces/?limit=1000")
        assert workspace_uuid not in [ws["uuid"] for ws in visible.json()]
        assert (await cascader.get("/api/lists/?shared=true")).json() == []
        # But the inherited grant is enough to point a column at the list.
        offered = await cascader.get(
            "/api/lists/?shared=true&lookup_targets=true&limit=1000"
        )
        assert target in [row["uuid"] for row in offered.json()]
