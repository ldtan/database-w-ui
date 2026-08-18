"""The `color` column type, over the HTTP API.

A colour has two spellings of the same value — `#abc` and `#aabbcc` — and the
column stores only the long one. That is not tidiness: `unique`, filtering and
grouping all compare the stored string, so admitting both would make one colour
look like two everywhere they are used.
"""
import pytest


async def _add_color_column(client, list_uuid: str, name="tag", **config) -> str:
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": name,
            "display_name": name.title(),
            "type": "color",
            "config": config,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def test_accepts_a_hex_colour(client, list_uuid):
    col = await _add_color_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "#1d4ed8"}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == "#1d4ed8"


@pytest.mark.parametrize(
    "raw,stored",
    [
        ("#ABC", "#aabbcc"),  # shorthand expands
        ("#AABBCC", "#aabbcc"),  # case folds
        ("  #1D4ED8  ", "#1d4ed8"),  # padding is trimmed
    ],
)
async def test_a_colour_is_stored_canonically(client, list_uuid, raw, stored):
    col = await _add_color_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: raw}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == stored


@pytest.mark.parametrize(
    "value",
    [
        "1d4ed8",  # no hash
        "#1d4ed",  # five digits
        "#1d4ed88",  # seven
        "#ggggkk",  # not hex
        "rebeccapurple",  # named colours are not stored
        "rgb(29, 78, 216)",
    ],
)
async def test_rejects_anything_that_is_not_rrggbb(client, list_uuid, value):
    col = await _add_color_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
    )
    assert resp.status_code == 400, f"{value!r} was accepted: {resp.text}"


async def test_unique_sees_the_two_spellings_as_one_colour(client, list_uuid):
    col = await _add_color_column(client, list_uuid, unique=True)
    first = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "#aabbcc"}}
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "#ABC"}}
    )
    assert second.status_code == 409, second.text


async def test_swatches_are_validated_and_stored_canonically(client, list_uuid):
    bad = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "tag",
            "display_name": "Tag",
            "type": "color",
            "config": {"swatches": ["#1d4ed8", "octarine"]},
        },
    )
    assert bad.status_code == 400, bad.text

    good = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "tag",
            "display_name": "Tag",
            "type": "color",
            "config": {"swatches": ["#ABC", "  #1D4ED8  "]},
        },
    )
    assert good.status_code == 201, good.text
    assert good.json()["config"]["swatches"] == ["#aabbcc", "#1d4ed8"]


async def test_swatches_offer_rather_than_restrict(client, list_uuid):
    """A palette is a shortcut, not an option set — the point of a colour field
    is picking one, so a colour outside the swatches is still a valid answer."""
    col = await _add_color_column(client, list_uuid, swatches=["#1d4ed8"])
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "#dc2626"}}
    )
    assert resp.status_code == 201, resp.text


async def test_a_columns_default_is_validated_and_normalized(client, list_uuid):
    bad = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "tag",
            "display_name": "Tag",
            "type": "color",
            "config": {"default": "nope"},
        },
    )
    assert bad.status_code == 400, bad.text

    good = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "tag",
            "display_name": "Tag",
            "type": "color",
            "config": {"default": "#ABC"},
        },
    )
    assert good.status_code == 201, good.text
    assert good.json()["config"]["default"] == "#aabbcc"
