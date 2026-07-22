"""The workspaces bin: soft-deleted workspaces can be listed and restored, and
restoring one brings back exactly the lists that went down with it.

Runs as the seeded 'tester' user (DISABLE_AUTH), who owns everything it creates, so
the owner-scoped bin and the restore both resolve without extra grants.
"""


async def test_workspace_bin_lists_and_restores(client, workspace_uuid):
    # Soft-delete: it leaves the live listing but lands in the bin.
    assert (await client.delete(f"/api/workspaces/{workspace_uuid}")).status_code == 204

    live = await client.get("/api/workspaces/")
    assert all(w["uuid"] != workspace_uuid for w in live.json())

    binned = await client.get("/api/workspaces/deleted")
    assert binned.status_code == 200, binned.text
    assert binned.headers["X-Total-Count"] == "1"
    row = binned.json()[0]
    assert row["uuid"] == workspace_uuid
    assert row["deleted_at"] is not None

    # Restore: back in the live listing, gone from the bin.
    restored = await client.post(f"/api/workspaces/{workspace_uuid}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["uuid"] == workspace_uuid

    live = await client.get("/api/workspaces/")
    assert any(w["uuid"] == workspace_uuid for w in live.json())
    binned = await client.get("/api/workspaces/deleted")
    assert binned.headers["X-Total-Count"] == "0"


async def test_restoring_workspace_brings_back_its_lists(
    client, workspace_uuid, list_uuid
):
    # Deleting the workspace cascades to its list.
    assert (await client.delete(f"/api/workspaces/{workspace_uuid}")).status_code == 204
    assert (await client.get(f"/api/lists/{list_uuid}")).status_code == 404

    # Restoring the workspace un-does exactly that cascade.
    assert (
        await client.post(f"/api/workspaces/{workspace_uuid}/restore")
    ).status_code == 200

    got = await client.get(f"/api/lists/{list_uuid}")
    assert got.status_code == 200, got.text
    assert got.json()["uuid"] == list_uuid


async def test_workspace_restore_leaves_independently_binned_lists(
    client, workspace_uuid, list_uuid
):
    # A list binned on its own carries a different deleted_at than the later
    # workspace delete, so restoring the workspace must not revive it.
    assert (await client.delete(f"/api/lists/{list_uuid}")).status_code == 204
    assert (await client.delete(f"/api/workspaces/{workspace_uuid}")).status_code == 204
    assert (
        await client.post(f"/api/workspaces/{workspace_uuid}/restore")
    ).status_code == 200

    # Workspace is back; the list stays in the workspace's own bin.
    assert (await client.get(f"/api/lists/{list_uuid}")).status_code == 404
    binned = await client.get(f"/api/lists/deleted?workspace_uuid={workspace_uuid}")
    assert binned.status_code == 200, binned.text
    assert any(row["uuid"] == list_uuid for row in binned.json())


async def test_restore_missing_workspace_is_404(client):
    resp = await client.post(
        "/api/workspaces/00000000-0000-0000-0000-000000000000/restore"
    )
    assert resp.status_code == 404
