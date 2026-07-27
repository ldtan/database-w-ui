"""`text_mode` on a text column, over the HTTP API.

One line versus several is how a text column is *edited*, not what it stores, so
it is config rather than a second column type. The point of that choice is what
`test_mode_can_change_after_creation` pins: `type` is immutable, config is not,
so a column created single-line can become multiline without the column-type
conversion machinery that does not exist yet.

`long_text` is gone; see the merge_long_text_into_text migration.
"""
import pytest


async def _add_text_column(client, list_uuid: str, **config) -> str:
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "notes",
            "display_name": "Notes",
            "type": "text",
            "config": config,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


@pytest.mark.parametrize("mode", ["single", "multiline", "rich"])
async def test_accepts_each_mode(client, list_uuid, mode):
    col = await _add_text_column(client, list_uuid, text_mode=mode)
    resp = await client.get(f"/api/lists/{list_uuid}/columns/")
    stored = next(c for c in resp.json() if c["uuid"] == col)
    assert stored["config"]["text_mode"] == mode


async def test_mode_is_optional(client, list_uuid):
    """Unset means single line, so an existing column needs no backfill."""
    col = await _add_text_column(client, list_uuid)
    resp = await client.get(f"/api/lists/{list_uuid}/columns/")
    stored = next(c for c in resp.json() if c["uuid"] == col)
    assert stored["config"].get("text_mode") is None


async def test_rejects_an_unknown_mode(client, list_uuid):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "notes",
            "display_name": "Notes",
            "type": "text",
            "config": {"text_mode": "markdown"},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "text_mode" in resp.text


async def test_long_text_is_no_longer_a_type(client, list_uuid):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={"name": "notes", "display_name": "Notes", "type": "long_text"},
    )
    assert resp.status_code == 422, resp.text


async def test_mode_can_change_after_creation(client, list_uuid):
    """The whole reason this is config: a stored value survives the switch."""
    col = await _add_text_column(client, list_uuid, text_mode="multiline")
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {col: "line one\nline two"}},
    )
    assert resp.status_code == 201, resp.text
    item = resp.json()["uuid"]

    resp = await client.patch(
        f"/api/lists/{list_uuid}/columns/{col}",
        json={"config": {"text_mode": "single"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["text_mode"] == "single"

    resp = await client.get(f"/api/lists/{list_uuid}/items/{item}")
    assert resp.json()["data"][col] == "line one\nline two"


async def test_multiline_text_is_still_referenceable_by_a_formula(
    client, list_uuid, add_column
):
    """Formulas took `text` and `long_text`; the merge must not narrow that."""
    await _add_text_column(client, list_uuid, text_mode="multiline")
    col = await add_column(
        list_uuid,
        name="shout",
        display_name="Shout",
        type="calculated",
        config={"formula": "UPPER(notes)"},
    )
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {}}
    )
    assert resp.status_code == 201, resp.text
    assert col in resp.json()["data"]
