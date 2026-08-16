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


async def test_a_lookup_at_the_deleted_list_itself_is_not_scrubbed(
    client, workspace_uuid, list_uuid, add_column
):
    """A restore must bring the list back configured, not blank.

    The delete scrub clears `target_list_uuid` on every live lookup naming the list
    being deleted, and a self-referential lookup names it too. What keeps it out of
    the scrub is ordering: `_cascade_descendants` soft-deletes the list's own columns
    first, and the scrub only looks at live ones. That ordering used to be invisible —
    the scrub touched values, and this column's values are on rows that are being
    deleted anyway — but now that it clears config as well, flipping it would make a
    restore silently return the column unconfigured, with nothing recording what it
    had pointed at. Pin it here.
    """
    title = await add_column(
        list_uuid, name="title", display_name="Title", type="text"
    )
    parent = await add_column(
        list_uuid,
        name="parent",
        display_name="Parent",
        type="lookup",
        config={"target_list_uuid": list_uuid, "display_column_uuid": title},
    )

    assert (await client.delete(f"/api/lists/{list_uuid}")).status_code == 204
    assert (await client.post(f"/api/lists/{list_uuid}/restore")).status_code == 200

    cols = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    restored = next(c for c in cols if c["uuid"] == parent)
    assert restored["config"]["target_list_uuid"] == list_uuid
    assert restored["config"]["display_column_uuid"] == title
    # And it still resolves, rather than answering "nothing to offer".
    options = await client.get(f"/api/lists/{list_uuid}/columns/{parent}/options")
    assert options.status_code == 200, options.text


async def test_restore_missing_list_is_404(client):
    resp = await client.post(
        "/api/lists/00000000-0000-0000-0000-000000000000/restore"
    )
    assert resp.status_code == 404
