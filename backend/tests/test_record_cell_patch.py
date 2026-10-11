"""One cell at a time: the record PATCH as an editable grid uses it.

Editing a value from the grid sends the *one* column that changed, not the
record — the frontend's "Edit cells" mode is built on that, and so is any API
client that wants to correct a single field without first reading the row and
sending it back. The merge itself is old behaviour; what is new is that a
surface now depends on it per keystroke-commit, so the guarantees it leans on
are pinned here rather than inferred from `TableRecordUpdate.data` being partial.

The neighbouring cases live with their own subjects: `onupdate` re-stamping on
any accepted edit is in test_tables_flow, refusing to set a calculated column is
in test_calculated_column, and clearing a nested lookup when its parent moves is
in test_nested_lookup.
"""
import pytest


@pytest.fixture
async def contact_table(client, table_uuid, add_column):
    """A table with three independent columns, so a write to one is visible as a
    write to exactly one."""
    name = await add_column(
        table_uuid, name="name", display_name="Name", type="text"
    )
    email = await add_column(
        table_uuid, name="email", display_name="Email", type="text"
    )
    age = await add_column(table_uuid, name="age", display_name="Age", type="number")
    return {"table": table_uuid, "name": name, "email": email, "age": age}


async def _create(client, cols, **values):
    resp = await client.post(
        f"/api/tables/{cols['table']}/records/",
        json={"data": {cols[key]: value for key, value in values.items()}},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def test_one_key_patch_leaves_every_other_cell_alone(client, contact_table):
    cols = contact_table
    uuid = await _create(
        client, cols, name="Ada", email="ada@example.com", age=36
    )

    resp = await client.patch(
        f"/api/tables/{cols['table']}/records/{uuid}",
        json={"data": {cols["email"]: "ada@lovelace.org"}},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data[cols["email"]] == "ada@lovelace.org"
    # The two columns the request never mentioned are untouched — the whole
    # reason a cell can be committed on its own.
    assert data[cols["name"]] == "Ada"
    assert data[cols["age"]] == 36

    # And on a fresh read, not just in the response body.
    got = await client.get(f"/api/tables/{cols['table']}/records/{uuid}")
    assert got.json()["data"][cols["name"]] == "Ada"


async def test_one_key_patch_clears_only_that_cell(client, contact_table):
    """Emptying a cell is `null` for that key, and says nothing about the rest.

    This is what an emptied editor sends (`isEmptyValue` -> null in the UI), and
    it must not be readable as "the record is now only this key"."""
    cols = contact_table
    uuid = await _create(
        client, cols, name="Ada", email="ada@example.com", age=36
    )

    resp = await client.patch(
        f"/api/tables/{cols['table']}/records/{uuid}",
        json={"data": {cols["email"]: None}},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data[cols["email"]] is None
    assert data[cols["name"]] == "Ada"
    assert data[cols["age"]] == 36


async def test_clearing_a_required_cell_is_refused_and_stores_nothing(
    client, table_uuid, add_column
):
    """Required is enforced over the *merged* record, so a single-cell clear is
    refused on its own — and the refusal leaves the stored value where it was,
    which is what lets the grid put the old value back and explain why."""
    name = await add_column(
        table_uuid,
        name="name",
        display_name="Name",
        type="text",
        config={"required": True},
    )
    created = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {name: "Ada"}}
    )
    assert created.status_code == 201, created.text
    uuid = created.json()["uuid"]

    resp = await client.patch(
        f"/api/tables/{table_uuid}/records/{uuid}", json={"data": {name: None}}
    )
    assert resp.status_code == 400, resp.text
    assert "required" in resp.json()["detail"].lower()

    got = await client.get(f"/api/tables/{table_uuid}/records/{uuid}")
    assert got.json()["data"][name] == "Ada"


async def test_empty_patch_is_accepted_and_changes_nothing(client, contact_table):
    """A commit with nothing in it is a no-op rather than an error — but note
    that it is still an *accepted write*, which is why the grid skips sending one
    at all when the value has not moved: an `onupdate` column would be
    re-stamped by it (see test_tables_flow)."""
    cols = contact_table
    uuid = await _create(client, cols, name="Ada", age=36)

    resp = await client.patch(
        f"/api/tables/{cols['table']}/records/{uuid}", json={"data": {}}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"][cols["name"]] == "Ada"
    assert resp.json()["data"][cols["age"]] == 36
