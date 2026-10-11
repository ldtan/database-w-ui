"""Permanent delete: a record, table, or workspace already in the bin can be
destroyed for good, and only from the bin — a live one 404s.

Runs as the seeded 'tester' user (DISABLE_AUTH), who owns everything, so the
delete-level gate resolves via the owner short-circuit. Grant cleanup on hard delete
is covered by the maintenance-purge tests whose SQL this reuses (see
test_orphan_grants.py); here we assert the endpoint surface and irreversibility.
"""


async def test_permanent_delete_record(client, table_uuid, add_column):
    col = await add_column(
        table_uuid, name="title", display_name="Title", type="text"
    )
    created = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: "gone"}}
    )
    assert created.status_code == 201, created.text
    record_uuid = created.json()["uuid"]

    # A live record cannot be purged — only one already in the bin.
    early = await client.delete(
        f"/api/tables/{table_uuid}/records/{record_uuid}/permanent"
    )
    assert early.status_code == 404

    assert (
        await client.delete(f"/api/tables/{table_uuid}/records/{record_uuid}")
    ).status_code == 204
    binned = await client.get(f"/api/tables/{table_uuid}/records/deleted")
    assert binned.headers["X-Total-Count"] == "1"

    # Purge: gone from the bin, and no longer restorable.
    assert (
        await client.delete(f"/api/tables/{table_uuid}/records/{record_uuid}/permanent")
    ).status_code == 204
    binned = await client.get(f"/api/tables/{table_uuid}/records/deleted")
    assert binned.headers["X-Total-Count"] == "0"
    assert (
        await client.post(f"/api/tables/{table_uuid}/records/{record_uuid}/restore")
    ).status_code == 404


async def test_permanent_delete_table(client, workspace_uuid, table_uuid):
    # A metric on the table: it is a child with a foreign key to `tables`, so the
    # cascade has to take it or the final DELETE cannot land.
    made = await client.post(
        f"/api/tables/{table_uuid}/metrics/",
        json={
            "name": "row_count",
            "display_name": "Row count",
            "config": {"formula": "COUNT()"},
        },
    )
    assert made.status_code == 201, made.text

    early = await client.delete(f"/api/tables/{table_uuid}/permanent")
    assert early.status_code == 404  # still live

    assert (await client.delete(f"/api/tables/{table_uuid}")).status_code == 204
    binned = await client.get(f"/api/tables/deleted?workspace_uuid={workspace_uuid}")
    assert binned.headers["X-Total-Count"] == "1"

    assert (await client.delete(f"/api/tables/{table_uuid}/permanent")).status_code == 204
    binned = await client.get(f"/api/tables/deleted?workspace_uuid={workspace_uuid}")
    assert binned.headers["X-Total-Count"] == "0"
    assert (await client.post(f"/api/tables/{table_uuid}/restore")).status_code == 404


async def test_permanent_delete_workspace(client, workspace_uuid, table_uuid):
    # As above, but reached through the workspace cascade rather than the table one.
    made = await client.post(
        f"/api/tables/{table_uuid}/metrics/",
        json={
            "name": "row_count",
            "display_name": "Row count",
            "config": {"formula": "COUNT()"},
        },
    )
    assert made.status_code == 201, made.text

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
    # The cascade took the workspace's table with it — it is unrecoverable too.
    assert (await client.get(f"/api/tables/{table_uuid}")).status_code == 404
