"""Permanent delete: a record, list, or workspace already in the bin can be
destroyed for good, and only from the bin — a live one 404s.

Runs as the seeded 'tester' user (DISABLE_AUTH), who owns everything, so the
delete-level gate resolves via the owner short-circuit. Grant cleanup on hard delete
is covered by the maintenance-purge tests whose SQL this reuses (see
test_orphan_grants.py); here we assert the endpoint surface and irreversibility.
"""


async def test_permanent_delete_record(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="title", display_name="Title", type="text"
    )
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "gone"}}
    )
    assert created.status_code == 201, created.text
    item_uuid = created.json()["uuid"]

    # A live record cannot be purged — only one already in the bin.
    early = await client.delete(
        f"/api/lists/{list_uuid}/items/{item_uuid}/permanent"
    )
    assert early.status_code == 404

    assert (
        await client.delete(f"/api/lists/{list_uuid}/items/{item_uuid}")
    ).status_code == 204
    binned = await client.get(f"/api/lists/{list_uuid}/items/deleted")
    assert binned.headers["X-Total-Count"] == "1"

    # Purge: gone from the bin, and no longer restorable.
    assert (
        await client.delete(f"/api/lists/{list_uuid}/items/{item_uuid}/permanent")
    ).status_code == 204
    binned = await client.get(f"/api/lists/{list_uuid}/items/deleted")
    assert binned.headers["X-Total-Count"] == "0"
    assert (
        await client.post(f"/api/lists/{list_uuid}/items/{item_uuid}/restore")
    ).status_code == 404


async def test_permanent_delete_list(client, workspace_uuid, list_uuid):
    early = await client.delete(f"/api/lists/{list_uuid}/permanent")
    assert early.status_code == 404  # still live

    assert (await client.delete(f"/api/lists/{list_uuid}")).status_code == 204
    binned = await client.get(f"/api/lists/deleted?workspace_uuid={workspace_uuid}")
    assert binned.headers["X-Total-Count"] == "1"

    assert (await client.delete(f"/api/lists/{list_uuid}/permanent")).status_code == 204
    binned = await client.get(f"/api/lists/deleted?workspace_uuid={workspace_uuid}")
    assert binned.headers["X-Total-Count"] == "0"
    assert (await client.post(f"/api/lists/{list_uuid}/restore")).status_code == 404


async def test_permanent_delete_workspace(client, workspace_uuid, list_uuid):
    early = await client.delete(f"/api/workspaces/{workspace_uuid}/permanent")
    assert early.status_code == 404  # still live

    assert (await client.delete(f"/api/workspaces/{workspace_uuid}")).status_code == 204
    binned = await client.get("/api/workspaces/deleted")
    assert binned.headers["X-Total-Count"] == "1"

    assert (
        await client.delete(f"/api/workspaces/{workspace_uuid}/permanent")
    ).status_code == 204
    binned = await client.get("/api/workspaces/deleted")
    assert binned.headers["X-Total-Count"] == "0"
    assert (
        await client.post(f"/api/workspaces/{workspace_uuid}/restore")
    ).status_code == 404
    # The cascade took the workspace's list with it — it is unrecoverable too.
    assert (await client.get(f"/api/lists/{list_uuid}")).status_code == 404
