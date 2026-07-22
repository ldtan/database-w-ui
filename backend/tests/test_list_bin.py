"""The lists bin: a workspace's soft-deleted lists can be listed and restored, and
restoring a list brings back its columns and records.

Runs as the seeded 'tester' user (DISABLE_AUTH), who owns the workspace, so the
workspace-level `delete` the bin is gated on resolves via the owner short-circuit.
"""


async def test_list_bin_lists_and_restores(
    client, workspace_uuid, list_uuid, add_column
):
    col = await add_column(
        list_uuid, name="title", display_name="Title", type="text"
    )
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "keep me"}}
    )
    assert created.status_code == 201, created.text
    item_uuid = created.json()["uuid"]

    # Soft-delete the list: it leaves the workspace listing and lands in the bin.
    assert (await client.delete(f"/api/lists/{list_uuid}")).status_code == 204
    live = await client.get(f"/api/lists/?workspace_uuid={workspace_uuid}")
    assert all(row["uuid"] != list_uuid for row in live.json())

    binned = await client.get(f"/api/lists/deleted?workspace_uuid={workspace_uuid}")
    assert binned.status_code == 200, binned.text
    assert binned.headers["X-Total-Count"] == "1"
    assert binned.json()[0]["uuid"] == list_uuid
    assert binned.json()[0]["deleted_at"] is not None

    # Restore: the list, its column, and its record all come back together.
    restored = await client.post(f"/api/lists/{list_uuid}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["uuid"] == list_uuid

    assert (await client.get(f"/api/lists/{list_uuid}")).status_code == 200
    cols = await client.get(f"/api/lists/{list_uuid}/columns/")
    assert any(c["uuid"] == col for c in cols.json())
    items = await client.get(f"/api/lists/{list_uuid}/items/")
    assert any(i["uuid"] == item_uuid for i in items.json())

    binned = await client.get(f"/api/lists/deleted?workspace_uuid={workspace_uuid}")
    assert binned.headers["X-Total-Count"] == "0"


async def test_restore_missing_list_is_404(client):
    resp = await client.post(
        "/api/lists/00000000-0000-0000-0000-000000000000/restore"
    )
    assert resp.status_code == 404
