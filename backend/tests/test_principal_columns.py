"""The `user` and `group` column types.

They are `lookup` with a fixed target: the cell stores a uuid, and the label is
resolved from the users / user_groups table. The three things worth pinning are
that unknown principals are rejected, that deleting a user or group scrubs the
now-dangling references, and that export writes names (not uuids) while import
resolves them back — so a file round-trips.
"""
import pytest
import pytest_asyncio

from src.auth.users.schemas import UserCreate, UserGroupCreate
from src.auth.users.services import UserGroupService, UserService


@pytest_asyncio.fixture
async def alice(session_factory):
    async with session_factory() as db:
        user = await UserService.create(
            db, UserCreate(username="alice", email="alice@example.com", password="pw")
        )
        return {"uuid": str(user.uuid), "username": user.username}


@pytest_asyncio.fixture
async def editors(session_factory, alice):
    async with session_factory() as db:
        group = await UserGroupService.create(
            db, UserGroupCreate(name="editors", display_name="Editors", user_uuids=[alice["uuid"]])
        )
        return {"uuid": str(group.uuid), "name": group.name}


async def _add(client, list_uuid, type_, **config) -> str:
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "assignee",
            "display_name": "Assignee",
            "type": type_,
            "config": config,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def test_stores_a_user_by_uuid(client, list_uuid, alice):
    col = await _add(client, list_uuid, "user")
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: alice["uuid"]}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == alice["uuid"]


async def test_a_username_is_normalized_to_its_uuid(client, list_uuid, alice):
    """Accepted for import's sake; a name is mutable, so a uuid is what's stored."""
    col = await _add(client, list_uuid, "user")
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "alice"}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == alice["uuid"]


async def test_group_column_accepts_a_group(client, list_uuid, editors):
    col = await _add(client, list_uuid, "group")
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: editors["uuid"]}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == editors["uuid"]


@pytest.mark.parametrize("value", ["nobody", "019f0000-0000-0000-0000-000000000000"])
async def test_rejects_an_unknown_principal(client, list_uuid, value):
    col = await _add(client, list_uuid, "user")
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
    )
    assert resp.status_code == 400, resp.text


async def test_a_group_uuid_is_not_a_valid_user(client, list_uuid, editors):
    """The two types resolve against different tables and must not cross over."""
    col = await _add(client, list_uuid, "user")
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: editors["uuid"]}}
    )
    assert resp.status_code == 400, resp.text


async def test_a_principal_column_cannot_have_a_default(client, list_uuid, alice):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "owner",
            "display_name": "Owner",
            "type": "user",
            "config": {"default": alice["uuid"]},
        },
    )
    assert resp.status_code == 400, resp.text


async def test_deleting_a_user_keeps_the_reference(
    client, session_factory, list_uuid, alice
):
    """A soft delete must be undoable, so it destroys nothing — including the cells
    pointing at the account. They keep the uuid and render as deleted; the reference
    is only cleared when the account is purged, by which time it is unrecoverable
    anyway. Scrubbing here is what used to make restore impossible."""
    single = await _add(client, list_uuid, "user")
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "reviewers",
            "display_name": "Reviewers",
            "type": "user",
            "config": {"multiple": True},
        },
    )
    assert resp.status_code == 201, resp.text
    multi = resp.json()["uuid"]

    created = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {single: alice["uuid"], multi: [alice["uuid"]]}},
    )
    assert created.status_code == 201, created.text
    item = created.json()["uuid"]

    async with session_factory() as db:
        assert await UserService.delete(db, alice["uuid"]) is True

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert items[0]["data"][single] == alice["uuid"]  # still there
    assert items[0]["data"][multi] == [alice["uuid"]]

    # The account resolves by uuid, flagged deleted, so the cell can say so.
    found = (
        await client.get(f"/api/auth/users/?uuid={alice['uuid']}")
    ).json()
    assert [(u["username"], u["is_deleted"]) for u in found] == [("alice", True)]
    # But it is not offered to anyone picking a new value.
    assert (await client.get("/api/auth/users/?q=alice")).json() == []

    # And the record is still saveable while holding the dead reference.
    saved = await client.patch(
        f"/api/lists/{list_uuid}/items/{item}",
        json={"data": {single: alice["uuid"]}},
    )
    assert saved.status_code == 200, saved.text


async def test_a_new_reference_to_a_deleted_user_is_still_refused(
    client, session_factory, list_uuid, alice
):
    """Keeping what is already there is not the same as allowing more of it."""
    col = await _add(client, list_uuid, "user")
    async with session_factory() as db:
        await UserService.delete(db, alice["uuid"])

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: alice["uuid"]}}
    )
    assert resp.status_code == 400, resp.text


async def test_deleting_a_group_keeps_the_reference(
    client, session_factory, list_uuid, editors
):
    col = await _add(client, list_uuid, "group")
    await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: editors["uuid"]}}
    )

    async with session_factory() as db:
        assert await UserGroupService.delete(db, editors["uuid"]) is True

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert items[0]["data"][col] == editors["uuid"]


async def test_export_writes_names_and_import_reads_them_back(
    client, list_uuid, alice
):
    """A file full of uuids is useless to a human, so export resolves the name —
    and import resolves it back, so the round-trip is lossless."""
    col = await _add(client, list_uuid, "user")
    await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: alice["uuid"]}}
    )

    export = await client.post(
        f"/api/lists/{list_uuid}/export", json={"format": "csv"}
    )
    assert export.status_code == 200, export.text
    csv = export.text
    assert "alice" in csv
    assert alice["uuid"] not in csv  # the uuid must not leak into the file

    reimport = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("rows.csv", csv, "text/csv")},
    )
    assert reimport.status_code == 200, reimport.text
    assert reimport.json()["created"] == 1

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert [i["data"][col] for i in items] == [alice["uuid"], alice["uuid"]]


async def test_a_group_cell_exports_the_slug_and_imports_back(
    client, list_uuid, session_factory
):
    """The slug, not the display name. An exported file has to re-import, and only
    the slug is unique — plus it is what the header row and a `user` cell already
    use, so the file stays one format rather than two."""
    async with session_factory() as db:
        group = await UserGroupService.create(
            db,
            UserGroupCreate(name="registrar", display_name="Registrar's Office"),
        )
        group_uuid = str(group.uuid)

    col = await _add(client, list_uuid, "group")
    await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: group_uuid}}
    )

    export = await client.post(
        f"/api/lists/{list_uuid}/export", json={"format": "csv"}
    )
    assert export.status_code == 200, export.text
    csv = export.text
    assert "registrar" in csv
    assert "Registrar's Office" not in csv
    assert group_uuid not in csv

    reimport = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("rows.csv", csv, "text/csv")},
    )
    assert reimport.status_code == 200, reimport.text
    assert reimport.json()["created"] == 1

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert [i["data"][col] for i in items] == [group_uuid, group_uuid]


async def test_a_group_cell_is_searchable_by_either_name(
    client, list_uuid, session_factory
):
    """The grid renders the display name, so searching for what is on screen has to
    work — without breaking search for the slug an export taught someone."""
    async with session_factory() as db:
        group = await UserGroupService.create(
            db, UserGroupCreate(name="registrar", display_name="Registrar's Office")
        )
        group_uuid = str(group.uuid)

    col = await _add(client, list_uuid, "group")
    await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: group_uuid}}
    )

    for term in ("regist", "Office"):
        found = (
            await client.get(f"/api/lists/{list_uuid}/items/?q={term}")
        ).json()
        assert [i["data"][col] for i in found] == [group_uuid], term


async def test_a_blank_value_clears_the_cell(client, list_uuid, alice):
    """An empty string means "no reference". Treating it as a name to match would
    silently link the record to whichever row/account has a blank label."""
    col = await _add(client, list_uuid, "user")
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: alice["uuid"]}}
    )
    item = created.json()["uuid"]

    cleared = await client.patch(
        f"/api/lists/{list_uuid}/items/{item}", json={"data": {col: "   "}}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"][col] is None



async def _current_user_uuid(session_factory) -> str:
    """The account requests act as under DISABLE_AUTH: the first (seeded) user."""
    async with session_factory() as db:
        user = await UserService.get_by_username(db, "tester")
        return str(user.uuid)


async def test_user_default_is_the_current_user(client, session_factory, list_uuid):
    """A user column may default to whoever is writing the record — a dynamic token,
    resolved to their uuid per record, not a stored account id that could rot."""
    me = await _current_user_uuid(session_factory)
    col = await _add(client, list_uuid, "user", default="current_user")
    resp = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == me


async def test_user_onupdate_stamps_the_current_user(
    client, session_factory, list_uuid
):
    me = await _current_user_uuid(session_factory)
    col = await _add(client, list_uuid, "user", onupdate="current_user")
    created = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
    assert created.status_code == 201, created.text
    assert created.json()["data"][col] == me


async def test_group_cannot_have_a_default(client, list_uuid):
    # No dynamic token stands in for "the current group", so a group default (even
    # the user token) is refused, as before.
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "team", "display_name": "Team", "type": "group",
            "config": {"default": "current_user"},
        },
    )
    assert resp.status_code == 400, resp.text
