"""A `multiple` choice column's **default**, which is the one part of the setting
nothing else pinned.

The rest of `multiple` on a choice column is already covered and deliberately not
repeated here: `test_shape_tolerance.py` owns the toggle itself (records are not
rewritten, reads coerce, filters and sorts see through the mismatch, the reconcile
converges it), `test_unique_multiple.py` owns the `unique` refusal — both using
choice columns throughout — and `test_choice_other.py` owns a cell that mixes
options with a write-in.

What is left is the shape of `config.default`, and it matters because it is the one
place where turning `multiple` on **invalidates a setting the column already had**:
a default is validated as a cell value, so a multi-valued column wants a list and a
single-valued one wants a string. A config PATCH merges, so a caller that toggles
the flag without resending the default gets a 400 — which is why the column dialog
carries the default across the two shapes rather than leaving it behind.
"""
import pytest


async def _column(client, table_uuid: str, name: str = "status", **config):
    """Create a choice column, returning the response rather than the uuid: half
    of these expect a refusal."""
    return await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={
            "name": name,
            "display_name": name.title(),
            "type": "choice",
            "config": {"choices": "Todo;Doing;Done", **config},
        },
    )


async def test_a_list_default_lands_on_a_record_left_blank(client, table_uuid):
    resp = await _column(client, table_uuid, multiple=True, default=["Todo", "Doing"])
    assert resp.status_code == 201, resp.text
    col = resp.json()["uuid"]

    created = await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {}})
    assert created.status_code == 201, created.text
    assert created.json()["data"][col] == ["Todo", "Doing"]


async def test_a_bare_string_default_is_refused_on_a_multiple_column(
    client, table_uuid
):
    """The reason the dialog holds a separate piece of state for this shape.

    A default is judged by the same validator a cell is, so on a `multiple` column
    it has to be a list — even though the write path would have coerced it.
    """
    resp = await _column(client, table_uuid, multiple=True, default="Todo")
    assert resp.status_code == 400, resp.text
    assert "list of options" in resp.json()["detail"]


async def test_a_list_default_is_refused_on_a_single_column(client, table_uuid):
    """The mirror, so neither direction is left to chance."""
    resp = await _column(client, table_uuid, default=["Todo"])
    assert resp.status_code == 400, resp.text


async def test_every_entry_in_the_default_has_to_be_an_option(client, table_uuid):
    resp = await _column(client, table_uuid, multiple=True, default=["Todo", "Nope"])
    assert resp.status_code == 400, resp.text
    assert "not a valid option" in resp.json()["detail"]


async def test_an_off_list_default_is_allowed_with_allow_other(client, table_uuid):
    """A write-in default is a *server* possibility on a multi-valued column.

    Pinned because the column dialog does not offer it — a MultiSelect takes no
    free text — and that is a UI narrowing rather than a rule. Anything reaching
    the API another way is still entitled to it, and a column configured this way
    must keep working if the dialog is later given a control that can express it.
    """
    resp = await _column(
        client,
        table_uuid,
        multiple=True,
        allow_other=True,
        default=["Todo", "Waiting on legal"],
    )
    assert resp.status_code == 201, resp.text


async def test_toggling_multiple_on_must_reshape_the_default_with_it(
    client, table_uuid
):
    """The trap the dialog steers around, stated as a test.

    A config PATCH merges onto the stored config, so a caller sending only
    `multiple` leaves the old string default in place — and the merged result is
    a multi-valued column with a single-valued default, which is refused. Sending
    both together is the fix, and is what the dialog does.
    """
    resp = await _column(client, table_uuid, default="Todo")
    assert resp.status_code == 201, resp.text
    col = resp.json()["uuid"]

    alone = await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}", json={"config": {"multiple": True}}
    )
    assert alone.status_code == 400, alone.text
    assert "list of options" in alone.json()["detail"]

    together = await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}",
        json={"config": {"multiple": True, "default": ["Todo"]}},
    )
    assert together.status_code == 200, together.text
    assert together.json()["config"]["default"] == ["Todo"]


@pytest.mark.parametrize(
    "value", [["Todo"], ["Todo", "Done"], []]
)
async def test_a_cell_holds_the_list_it_was_given(client, table_uuid, value):
    """Verbatim, including the empty one.

    `[]` is **not** collapsed to `None` the way `""` is — an empty list is how a
    multi-valued cell spells empty, and storing it as itself is what keeps the
    column's declared shape and its records agreeing. Nothing downstream has to
    care, because `_is_empty` counts `[]` alongside `None` (see the required test
    below) and `coerce_shape` reads it back unchanged.
    """
    resp = await _column(client, table_uuid, multiple=True)
    assert resp.status_code == 201, resp.text
    col = resp.json()["uuid"]

    created = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: value}}
    )
    assert created.status_code == 201, created.text
    assert created.json()["data"][col] == value


@pytest.mark.parametrize("value", [[], None])
async def test_an_empty_list_does_not_satisfy_required(client, table_uuid, value):
    """The other half of storing `[]` as itself: it still reads as blank.

    Both spellings have to be refused — the UI sends `null` for an emptied picker
    (`toStored`), while an import or a direct API call is at liberty to send `[]`.
    """
    resp = await _column(client, table_uuid, multiple=True, required=True)
    assert resp.status_code == 201, resp.text
    col = resp.json()["uuid"]

    created = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: value}}
    )
    assert created.status_code == 400, created.text
    assert "required" in created.json()["detail"]
