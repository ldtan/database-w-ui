"""End-to-end tests over the HTTP API: workspaces, lists, columns, records."""
from datetime import date


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
