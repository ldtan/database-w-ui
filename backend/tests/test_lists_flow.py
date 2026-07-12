"""End-to-end tests over the HTTP API: workspaces, lists, columns, records."""
from datetime import date
from uuid import UUID

from sqlalchemy import func, select

from src.lists.columns.models import Column
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


async def test_list_autoprovisions_view_and_forms(client, list_uuid):
    views = (await client.get(f"/api/lists/{list_uuid}/views/")).json()
    forms = (await client.get(f"/api/lists/{list_uuid}/forms/")).json()
    assert [v["name"] for v in views] == ["grid"]
    assert sorted(f["name"] for f in forms) == ["create", "update"]
    assert all(f["is_default"] for f in forms)


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
    assert [o["id"] for o in column["config"]["options"]] == ["todo", "doing", "done"]
    assert (await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "todo"}})).status_code == 201
    assert (await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "nope"}})).status_code == 400


async def test_dynamic_default_today(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="due", display_name="Due", type="date",
        config={"default": "today"},
    )
    resp = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == date.today().isoformat()


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


async def test_deleting_row_scrubs_lookup_reference(client, list_uuid, workspace_uuid, add_column):
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

    # Delete the target row; the referencing record should be scrubbed.
    assert (await client.delete(f"/api/lists/{target}/items/{target_row}")).status_code == 204
    refreshed = (await client.get(f"/api/lists/{list_uuid}/items/{row['uuid']}")).json()
    assert refreshed["data"][link] is None


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
