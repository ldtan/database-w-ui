"""End-to-end tests over the HTTP API: workspaces, lists, columns, records."""
from datetime import datetime, timezone
from uuid import UUID, uuid7
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from src.auth.users.models import User
from src.core.config import settings
from src.lists.columns.models import Column
from src.lists.items.schemas import BulkDeleteRequest
from src.lists.forms.models import Form
from src.lists.items.models import ListItem
from src.lists.models import List
from src.lists.views.models import View


async def _child_counts(session_factory, list_uuid: str):
    """Return {child: (total, active)} row counts for a list's descendants."""
    async with session_factory() as db:
        list_id = await db.scalar(
            select(List.id).where(List.uuid == UUID(list_uuid))
        )
        counts = {}
        for name, model in [
            ("columns", Column),
            ("items", ListItem),
            ("views", View),
            ("forms", Form),
        ]:
            total = await db.scalar(
                select(func.count()).select_from(model).where(model.list_id == list_id)
            )
            active = await db.scalar(
                select(func.count())
                .select_from(model)
                .where(model.list_id == list_id, model.deleted_at.is_(None))
            )
            counts[name] = (total, active)
        return counts


async def test_delete_list_cascades_soft_delete(
    client, session_factory, list_uuid, add_column
):
    col = await add_column(list_uuid, name="n", display_name="N", type="text")
    await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "x"}})

    before = await _child_counts(session_factory, list_uuid)
    assert all(total > 0 and active > 0 for total, active in before.values())

    assert (await client.delete(f"/api/lists/{list_uuid}")).status_code == 204

    after = await _child_counts(session_factory, list_uuid)
    for name, (total, active) in after.items():
        assert total > 0, f"{name} rows were hard-deleted"  # soft delete keeps rows
        assert active == 0, f"{name} not soft-deleted on cascade"


async def test_delete_list_scrubs_cross_list_lookup_refs(
    client, workspace_uuid, add_column
):
    async def make_list(name):
        resp = await client.post(
            "/api/lists/",
            json={"name": name, "display_name": name, "workspace_uuid": workspace_uuid},
        )
        return resp.json()["uuid"]

    authors = await make_list("authors")
    aname = await add_column(authors, name="full_name", display_name="Name", type="text")
    a1 = (
        await client.post(
            f"/api/lists/{authors}/items/", json={"data": {aname: "Ursula"}}
        )
    ).json()["uuid"]

    books = await make_list("books")
    author_col = await add_column(
        books,
        name="author",
        display_name="Author",
        type="lookup",
        config={"target_list_uuid": authors},
    )
    book = (
        await client.post(
            f"/api/lists/{books}/items/", json={"data": {author_col: a1}}
        )
    ).json()
    assert book["data"][author_col] == a1

    # Deleting the target list must scrub references held by the surviving list.
    assert (await client.delete(f"/api/lists/{authors}")).status_code == 204

    items = (await client.get(f"/api/lists/{books}/items/")).json()
    assert len(items) == 1
    assert author_col not in items[0]["data"]  # dangling reference removed


async def test_delete_workspace_cascades_to_lists_and_descendants(
    client, session_factory, workspace_uuid, list_uuid, add_column
):
    await add_column(list_uuid, name="n", display_name="N", type="text")

    assert (await client.delete(f"/api/workspaces/{workspace_uuid}")).status_code == 204

    # The list itself is soft-deleted...
    async with session_factory() as db:
        deleted_at = await db.scalar(
            select(List.deleted_at).where(List.uuid == UUID(list_uuid))
        )
    assert deleted_at is not None

    # ...and so are its descendants.
    after = await _child_counts(session_factory, list_uuid)
    for name, (total, active) in after.items():
        assert active == 0, f"{name} not soft-deleted when workspace deleted"


async def test_new_list_autoprovisions_audit_columns(client, workspace_uuid):
    resp = await client.post(
        "/api/lists/",
        json={"name": "audit", "display_name": "Audit", "workspace_uuid": workspace_uuid},
    )
    assert resp.status_code == 201, resp.text
    list_uuid = resp.json()["uuid"]

    columns = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    names = {col["name"] for col in columns}
    assert {"created_at", "updated_at", "created_by", "updated_by"} <= names

    created_at = next(col for col in columns if col["name"] == "created_at")
    updated_at = next(col for col in columns if col["name"] == "updated_at")
    created_by = next(col for col in columns if col["name"] == "created_by")
    updated_by = next(col for col in columns if col["name"] == "updated_by")

    assert created_at["type"] == "datetime"
    assert created_at["config"]["default"] == "now"
    assert created_at["config"]["read_only"] is True

    assert updated_at["type"] == "datetime"
    assert updated_at["config"]["onupdate"] == "now"
    assert updated_at["config"]["read_only"] is True

    assert created_by["type"] == "user"
    assert created_by["config"]["default"] == "current_user"
    assert created_by["config"]["read_only"] is True

    assert updated_by["type"] == "user"
    assert updated_by["config"]["onupdate"] == "current_user"
    assert updated_by["config"]["read_only"] is True


async def test_list_autoprovisions_view_and_forms(client, list_uuid):
    views = (await client.get(f"/api/lists/{list_uuid}/views/")).json()
    forms = (await client.get(f"/api/lists/{list_uuid}/forms/")).json()
    assert [v["name"] for v in views] == ["grid"]
    assert sorted(f["name"] for f in forms) == ["create", "update", "view"]
    assert all(f["is_default"] for f in forms)


async def test_view_form_starts_showing_every_column(client, list_uuid):
    """The display-only form seeds empty, which is the "all columns" convention.

    Create and update instead name the audit columns to keep them *off* the form —
    nobody types "created by", but it is exactly what someone opens a record to
    read. An empty layout also picks up calculated columns, which no input form
    may contain.
    """
    forms = (await client.get(f"/api/lists/{list_uuid}/forms/")).json()
    view_form = next(f for f in forms if f["name"] == "view")
    assert view_form["type"] == "view"
    assert view_form["is_default"] is True
    assert view_form["config"]["fields"] == []

    for name in ("create", "update"):
        form = next(f for f in forms if f["name"] == name)
        assert form["type"] == name
        # Audit columns are named-and-excluded, under the key FormConfig accepts:
        # `extra="forbid"` means a misspelling here 422s the first PATCH back.
        assert form["config"]["fields"]
        assert all(
            field["included"] is False and field["enabled"] is False
            for field in form["config"]["fields"]
        )


async def test_list_scoped_to_workspace(client, workspace_uuid, list_uuid):
    resp = await client.get("/api/lists/", params={"workspace_uuid": workspace_uuid})
    assert resp.status_code == 200
    assert [lst["uuid"] for lst in resp.json()] == [list_uuid]


async def test_column_name_must_be_slug(client, list_uuid):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={"name": "Not A Slug", "display_name": "X", "type": "text"},
    )
    assert resp.status_code == 422  # NameSlug pattern rejects spaces/caps


async def test_add_column_and_item_roundtrip(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="title", display_name="Title", type="text")
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "hello"}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == "hello"


async def test_bulk_delete_items(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="n", display_name="N", type="text")

    async def add(value):
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
        )
        return resp.json()["uuid"]

    u1, u2 = await add("a"), await add("b")
    await add("c")

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-delete",
        json={"uuids": [u1, u2, "00000000-0000-0000-0000-000000000000"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 2  # bogus uuid skipped

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert [i["data"][col] for i in items] == ["c"]


async def test_bulk_delete_counts_a_repeated_uuid_once(client, list_uuid, add_column):
    """The count is what the UI reports back, so it has to be rows removed rather
    than uuids handled. Naming the same record twice used to add two to the total
    while deleting one."""
    col = await add_column(list_uuid, name="n", display_name="N", type="text")
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "a"}}
    )
    uuid = resp.json()["uuid"]

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-delete", json={"uuids": [uuid, uuid]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 1


async def test_bulk_delete_ignores_a_uuid_from_another_list(
    client, workspace_uuid, list_uuid, add_column
):
    """Permissions are now resolved from the uuid alone, without reference to which
    list the record is on, so the delete itself is what confines the batch. A record
    the caller owns elsewhere must not go just because it was named here."""
    resp = await client.post(
        "/api/lists/",
        json={"name": "l2", "display_name": "List 2", "workspace_uuid": workspace_uuid},
    )
    other_list = resp.json()["uuid"]
    other_col = await add_column(other_list, name="n", display_name="N", type="text")
    resp = await client.post(
        f"/api/lists/{other_list}/items/", json={"data": {other_col: "keep"}}
    )
    stranger = resp.json()["uuid"]

    col = await add_column(list_uuid, name="n", display_name="N", type="text")
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "go"}}
    )
    mine = resp.json()["uuid"]

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-delete", json={"uuids": [mine, stranger]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 1

    survivors = (await client.get(f"/api/lists/{other_list}/items/")).json()
    assert [i["uuid"] for i in survivors] == [stranger]


def test_the_bulk_delete_cap_comes_from_config():
    """`max_length` is read once when the class is defined, so unlike the other two
    limits this one cannot be monkeypatched and the tests below use its default
    directly. Assert the wiring instead, or a future edit could quietly put the
    literal back and nothing would notice."""
    caps = [
        getattr(meta, "max_length", None)
        for meta in BulkDeleteRequest.model_fields["uuids"].metadata
    ]
    assert settings.BULK_DELETE_MAX in caps


async def test_bulk_delete_refuses_an_oversized_batch(client, list_uuid):
    """The cap is the only thing bounding this endpoint's work — nothing else on the
    request says how many uuids may arrive."""
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-delete",
        json={"uuids": [str(uuid7()) for _ in range(501)]},
    )
    assert resp.status_code == 422, resp.text

    # And the boundary itself is allowed through (none of them exist, so nothing
    # is deleted — the point is that it is accepted at all).
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-delete",
        json={"uuids": [str(uuid7()) for _ in range(500)]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 0


async def test_required_rejected_when_missing(client, list_uuid, add_column):
    await add_column(
        list_uuid, name="title", display_name="Title", type="text",
        config={"required": True},
    )
    resp = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
    assert resp.status_code == 400
    assert "required" in resp.json()["detail"].lower()


async def test_text_max_length_enforced(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="code", display_name="Code", type="text",
        config={"max_length": 3},
    )
    ok = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "abc"}})
    assert ok.status_code == 201
    bad = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "abcd"}})
    assert bad.status_code == 400


async def test_number_range_and_integer_only(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="qty", display_name="Qty", type="number",
        config={"min": 0, "max": 10, "integer_only": True},
    )
    assert (await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: 5}})).status_code == 201
    assert (await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: 50}})).status_code == 400
    assert (await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: 5.5}})).status_code == 400


async def test_choice_shorthand_expands_and_validates(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="status", display_name="Status", type="choice",
        config={"choices": "Todo;Doing;Done"},
    )
    column = (await client.get(f"/api/lists/{list_uuid}/columns/{col}")).json()
    assert column["config"]["options"] == ["Todo", "Doing", "Done"]
    # A cell stores the option itself — the visible label, exactly.
    assert (await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "Todo"}})).status_code == 201
    assert (await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "todo"}})).status_code == 400
    assert (await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "nope"}})).status_code == 400


async def test_choice_duplicate_options_rejected(client, list_uuid):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "status", "display_name": "Status", "type": "choice",
            "config": {"choices": "Todo;Todo"},
        },
    )
    assert resp.status_code == 400
    assert "unique" in resp.json()["detail"].lower()


async def test_choice_default_must_be_an_option(client, list_uuid):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "status", "display_name": "Status", "type": "choice",
            "config": {"choices": "Todo;Done", "default": "Doing"},
        },
    )
    assert resp.status_code == 400
    assert "not one of" in resp.json()["detail"].lower()


async def test_choice_config_patch_keeps_options(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="status", display_name="Status", type="choice",
        config={"choices": "Todo;Done"},
    )
    # A config PATCH that doesn't resend options must not wipe the set.
    resp = await client.patch(
        f"/api/lists/{list_uuid}/columns/{col}",
        json={"config": {"required": True}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["options"] == ["Todo", "Done"]


async def test_dynamic_default_today(client, list_uuid, add_column):
    """With no zone recorded for the user, the stamp falls back to UTC — not to the
    server process's local time, which would make the value depend on where the box
    happens to be configured."""
    col = await add_column(
        list_uuid, name="due", display_name="Due", type="date",
        config={"default": "today"},
    )
    resp = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == datetime.now(timezone.utc).date().isoformat()


async def test_dynamic_default_today_uses_the_users_timezone(
    client, list_uuid, add_column, session_factory, user_id
):
    """The stamp follows the user's calendar rather than the server's clock.

    Two zones 25 hours apart, so their local dates differ no matter what time the
    suite runs: were the setting ignored, both records would carry the same date.
    Compared against each zone's own `today` rather than a fixed string, because the
    right answer moves with the clock.
    """
    col = await add_column(
        list_uuid, name="due", display_name="Due", type="date",
        config={"default": "today"},
    )

    stamped = {}
    for zone in ("Pacific/Kiritimati", "Pacific/Niue"):  # UTC+14 and UTC-11
        async with session_factory() as db:
            user = await db.get(User, user_id)
            # A new dict, not a mutation: JSONB is not mutation-tracked.
            user.settings = {**(user.settings or {}), "timezone": zone}
            await db.commit()

        resp = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
        assert resp.status_code == 201, resp.text
        stamped[zone] = resp.json()["data"][col]
        assert stamped[zone] == datetime.now(ZoneInfo(zone)).date().isoformat()

    assert stamped["Pacific/Kiritimati"] != stamped["Pacific/Niue"]


async def test_dynamic_now_is_stored_without_an_offset(
    client, list_uuid, add_column, session_factory, user_id
):
    """A stamped datetime has to be indistinguishable from a typed one.

    These columns hold a wall-clock reading, so resolving `now` in the user's zone
    must not leak that zone into the value: a stored offset would sort against the
    naive values beside it wrongly, and min/max comparison raises outright on the
    mixed convention (see validation.validate_scalar).
    """
    async with session_factory() as db:
        user = await db.get(User, user_id)
        user.settings = {**(user.settings or {}), "timezone": "Asia/Manila"}
        await db.commit()

    col = await add_column(
        list_uuid, name="seen", display_name="Seen", type="datetime",
        config={"default": "now"},
    )
    resp = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
    assert resp.status_code == 201, resp.text

    value = resp.json()["data"][col]
    stamped = datetime.fromisoformat(value)
    assert stamped.tzinfo is None, value
    # And it reads as Manila's wall clock, not UTC's. As a delta rather than a
    # prefix match, so an hour boundary crossed mid-test cannot fail it; UTC would
    # be eight hours out, far outside the window.
    manila = datetime.now(ZoneInfo("Asia/Manila")).replace(tzinfo=None)
    assert abs((stamped - manila).total_seconds()) < 60, value


async def test_static_default_applied_when_empty(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="qty", display_name="Qty", type="number",
        config={"default": 7},
    )
    # Absent -> the default is filled in.
    empty = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
    assert empty.status_code == 201, empty.text
    assert empty.json()["data"][col] == 7
    # A provided value wins over the default.
    given = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: 3}})
    assert given.json()["data"][col] == 3


async def test_static_default_must_be_valid(client, list_uuid):
    # A default that is not itself a valid value for the column is rejected up front.
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "qty", "display_name": "Qty", "type": "number",
            "config": {"default": 5, "max": 3},
        },
    )
    assert resp.status_code == 400, resp.text


async def test_onupdate_stamps_on_create_and_edit(client, list_uuid, add_column):
    stamped = await add_column(
        list_uuid, name="touched", display_name="Touched", type="datetime",
        config={"onupdate": "now"},
    )
    other = await add_column(
        list_uuid, name="note", display_name="Note", type="text",
    )
    # Stamped at create even though nothing was sent for it.
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {other: "a"}}
    )
    assert created.status_code == 201, created.text
    first = created.json()["data"][stamped]
    assert first  # a concrete datetime string

    item_uuid = created.json()["uuid"]
    # Editing any field re-stamps it, and a value the client tries to set is ignored.
    edited = await client.patch(
        f"/api/lists/{list_uuid}/items/{item_uuid}",
        json={"data": {other: "b", stamped: "1999-01-01T00:00:00"}},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"][stamped] != "1999-01-01T00:00:00"
    assert edited.json()["data"][stamped] >= first


async def test_onupdate_rejected_on_unsupported_type(client, list_uuid):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "note", "display_name": "Note", "type": "text",
            "config": {"onupdate": "now"},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "on-update" in resp.json()["detail"].lower()


async def test_unique_sequential_conflict(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="email", display_name="Email", type="text",
        config={"unique": True},
    )
    first = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "a@b.co"}})
    assert first.status_code == 201
    second = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "a@b.co"}})
    assert second.status_code == 409


async def test_config_patch_merges(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="qty", display_name="Qty", type="number",
        config={"required": True, "min": 0, "max": 100},
    )
    resp = await client.patch(
        f"/api/lists/{list_uuid}/columns/{col}", json={"config": {"max": 50}}
    )
    assert resp.status_code == 200, resp.text
    cfg = resp.json()["config"]
    # max updated, but required/min preserved (merge, not replace)
    assert cfg["max"] == 50
    assert cfg.get("required") is True
    assert cfg.get("min") == 0


async def test_lookup_default_rejected(client, list_uuid, workspace_uuid):
    target = (
        await client.post(
            "/api/lists/",
            json={"name": "target", "display_name": "Target", "workspace_uuid": workspace_uuid},
        )
    ).json()["uuid"]
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "link", "display_name": "Link", "type": "lookup",
            "config": {"target_list_uuid": target, "default": "x"},
        },
    )
    assert resp.status_code == 400
    assert "default" in resp.json()["detail"].lower()


async def test_deleting_a_row_keeps_the_lookup_reference(client, list_uuid, workspace_uuid, add_column):
    # Target list with one row.
    target = (
        await client.post(
            "/api/lists/",
            json={"name": "people", "display_name": "People", "workspace_uuid": workspace_uuid},
        )
    ).json()["uuid"]
    target_row = (
        await client.post(f"/api/lists/{target}/items/", json={"data": {}})
    ).json()["uuid"]

    link = await add_column(
        list_uuid, name="owner", display_name="Owner", type="lookup",
        config={"target_list_uuid": target},
    )
    row = (
        await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {link: target_row}})
    ).json()
    assert row["data"][link] == target_row

    # Delete the target row: the reference *survives*, because a soft delete has to be
    # undoable and destroying the pointer is not. It is cleared when the row is purged
    # (see tests/test_soft_delete_is_reversible.py).
    assert (await client.delete(f"/api/lists/{target}/items/{target_row}")).status_code == 204
    refreshed = (await client.get(f"/api/lists/{list_uuid}/items/{row['uuid']}")).json()
    assert refreshed["data"][link] == target_row


async def test_blank_means_empty_for_every_column_type(client, list_uuid, add_column):
    """Decided once, centrally: a whitespace-only value clears the cell whatever the
    column is. Per-type handling had "" clear a reference cell but 400 a link cell,
    so the same payload meant different things depending on where it landed."""
    cols = {
        "text": await add_column(list_uuid, name="t", display_name="T", type="text"),
        "link": await add_column(list_uuid, name="l", display_name="L", type="link"),
        "date": await add_column(list_uuid, name="d", display_name="D", type="date"),
    }
    created = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={
            "data": {
                cols["text"]: "hello",
                cols["link"]: "https://example.com",
                cols["date"]: "2026-07-01",
            }
        },
    )
    assert created.status_code == 201, created.text
    item = created.json()["uuid"]

    cleared = await client.patch(
        f"/api/lists/{list_uuid}/items/{item}",
        json={"data": {uuid: "   " for uuid in cols.values()}},
    )
    assert cleared.status_code == 200, cleared.text
    assert all(cleared.json()["data"][uuid] is None for uuid in cols.values())


async def test_records_can_be_searched(client, list_uuid, add_column):
    """Server-side, like every other search here: filtering a fetched page in the
    browser would silently fail to find records past it."""
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    note = await add_column(list_uuid, name="note", display_name="Note", type="text")
    for n, t in [("Ann", "likes cats"), ("Bo", "likes dogs"), ("Cy", "no pets")]:
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {name: n, note: t}}
        )

    # Any column matches, case-insensitively.
    found = (await client.get(f"/api/lists/{list_uuid}/items/?q=ann")).json()
    assert [i["data"][name] for i in found] == ["Ann"]
    found = (await client.get(f"/api/lists/{list_uuid}/items/?q=likes")).json()
    assert sorted(i["data"][name] for i in found) == ["Ann", "Bo"]
    assert (await client.get(f"/api/lists/{list_uuid}/items/?q=zebra")).json() == []

    # A LIKE wildcard is matched literally, not as a wildcard.
    assert (await client.get(f"/api/lists/{list_uuid}/items/?q=%25")).json() == []

    # Blank searches are no-ops rather than matching nothing.
    assert len((await client.get(f"/api/lists/{list_uuid}/items/?q=  ")).json()) == 3


async def test_search_matches_lookup_and_user_labels(
    client, session_factory, workspace_uuid, list_uuid, add_column
):
    """A reference cell stores a uuid, but what a person types is the label. Search
    resolves the label — the linked row's, the account's — and matches on that."""
    from src.auth.users.schemas import UserCreate
    from src.auth.users.services import UserService

    async with session_factory() as db:
        alice = await UserService.create(
            db, UserCreate(username="alice", email="alice@example.com", password="pw")
        )
        alice_uuid = str(alice.uuid)

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
    school_name = await add_column(
        target, name="name", display_name="Name", type="text"
    )
    springfield = (
        await client.post(
            f"/api/lists/{target}/items/",
            json={"data": {school_name: "Springfield High"}},
        )
    ).json()["uuid"]
    shelbyville = (
        await client.post(
            f"/api/lists/{target}/items/",
            json={"data": {school_name: "Shelbyville High"}},
        )
    ).json()["uuid"]

    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    school = await add_column(
        list_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_list_uuid": target},
    )
    owner = await add_column(list_uuid, name="owner", display_name="Owner", type="user")
    reviewers = await add_column(
        list_uuid,
        name="reviewers",
        display_name="Reviewers",
        type="user",
        config={"multiple": True},
    )

    await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={
            "data": {
                name: "Ann",
                school: springfield,
                owner: alice_uuid,
                reviewers: [alice_uuid],
            }
        },
    )
    await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {name: "Bo", school: shelbyville}},
    )

    async def search(q):
        found = (await client.get(f"/api/lists/{list_uuid}/items/?q={q}")).json()
        return sorted(i["data"][name] for i in found)

    # The lookup's label, not its uuid.
    assert await search("springfield") == ["Ann"]
    assert await search("high") == ["Ann", "Bo"]  # both schools match
    # A username, on a single-valued and a multi-valued cell alike.
    assert await search("alice") == ["Ann"]
    # And plain text still works.
    assert await search("Bo") == ["Bo"]


async def test_deleted_records_are_listed_and_restorable(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="n", display_name="N", type="text")
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "keep me"}}
    )
    uuid = created.json()["uuid"]

    assert (await client.delete(f"/api/lists/{list_uuid}/items/{uuid}")).status_code == 204
    # Gone from the live listing...
    assert (await client.get(f"/api/lists/{list_uuid}/items/")).json() == []

    # ...but in the bin, with its deletion time and a total-count header.
    binned = await client.get(f"/api/lists/{list_uuid}/items/deleted")
    assert binned.status_code == 200, binned.text
    assert binned.headers["X-Total-Count"] == "1"
    rows = binned.json()
    assert [r["uuid"] for r in rows] == [uuid]
    assert rows[0]["deleted_at"] is not None
    assert rows[0]["data"][col] == "keep me"

    # Restore brings it back to the live listing and empties the bin.
    restored = await client.post(f"/api/lists/{list_uuid}/items/{uuid}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"][col] == "keep me"
    assert [i["uuid"] for i in (await client.get(f"/api/lists/{list_uuid}/items/")).json()] == [uuid]
    assert (await client.get(f"/api/lists/{list_uuid}/items/deleted")).json() == []


async def test_restore_rejected_when_it_would_duplicate_a_unique_value(
    client, list_uuid, add_column
):
    col = await add_column(
        list_uuid, name="code", display_name="Code", type="text",
        config={"unique": True},
    )

    async def add(value):
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["uuid"]

    first = await add("X")
    # Bin the first, then let a new record take the value it held.
    assert (await client.delete(f"/api/lists/{list_uuid}/items/{first}")).status_code == 204
    await add("X")

    # Restoring the first would now collide on the unique column.
    resp = await client.post(f"/api/lists/{list_uuid}/items/{first}/restore")
    assert resp.status_code == 409, resp.text
    assert "unique" in resp.json()["detail"].lower()


async def test_restore_of_a_live_record_is_a_404(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="n", display_name="N", type="text")
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "a"}}
    )
    uuid = created.json()["uuid"]
    # It is not in the bin, so there is nothing to restore.
    assert (await client.post(f"/api/lists/{list_uuid}/items/{uuid}/restore")).status_code == 404
