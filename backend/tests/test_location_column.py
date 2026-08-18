"""The `location` column type, over the HTTP API.

**This is the first cell in the system whose value is a JSON object**, and that is
what most of these tests are about. `data ->> '<uuid>'` on one yields the serialized
blob, so every reader that treats a cell as text — filter, sort, search, export, the
cell converter — had to be taught to reach for the address instead. A reader that was
missed shows up here as a filter matching the word "address", or a CSV column full of
`{'address': ...}`.
"""
import json

import pytest


async def _add_location_column(client, list_uuid: str, name="site", **config) -> str:
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": name,
            "display_name": name.title(),
            "type": "location",
            "config": config,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def test_stores_an_address_and_a_pin(client, list_uuid):
    col = await _add_location_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={
            "data": {
                col: {"address": "12 Rizal St, Cebu City", "lat": 10.3157, "lng": 123.8854}
            }
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == {
        "address": "12 Rizal St, Cebu City",
        "lat": 10.3157,
        "lng": 123.8854,
    }


async def test_either_half_alone_is_a_location(client, list_uuid):
    col = await _add_location_column(client, list_uuid)
    address_only = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {col: {"address": "12 Rizal St, Cebu City"}}},
    )
    assert address_only.status_code == 201, address_only.text
    # Every key present, whichever half was given: the SQL readers index
    # `data -> key ->> 'address'`, and a missing key would read as NULL for reasons
    # nothing explains.
    assert address_only.json()["data"][col] == {
        "address": "12 Rizal St, Cebu City",
        "lat": None,
        "lng": None,
    }

    pin_only = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {col: {"lat": 10.3157, "lng": 123.8854}}},
    )
    assert pin_only.status_code == 201, pin_only.text
    assert pin_only.json()["data"][col]["address"] is None


@pytest.mark.parametrize(
    "value",
    [
        "12 Rizal St",  # a bare string is not a location
        {},  # neither half filled in
        {"address": "  "},  # nor is whitespace
        {"address": "x", "lat": 10.3157},  # a latitude with no longitude
        {"address": "x", "lat": 100.0, "lng": 0.0},  # off the planet
        {"address": "x", "lat": 0.0, "lng": 200.0},
        {"address": "x", "lat": "north", "lng": "east"},
        {"address": 42, "lat": None, "lng": None},
        # The key set is closed: `data` is schemaless, so this check is the only
        # thing between a location cell and a place to stash arbitrary JSON.
        {"address": "x", "note": "smuggled"},
    ],
)
async def test_rejects_what_is_not_a_location(client, list_uuid, value):
    col = await _add_location_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
    )
    assert resp.status_code == 400, f"{value!r} was accepted: {resp.text}"


async def test_require_coordinates_refuses_an_address_with_no_pin(client, list_uuid):
    col = await _add_location_column(client, list_uuid, require_coordinates=True)
    nope = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {col: {"address": "12 Rizal St"}}},
    )
    assert nope.status_code == 400, nope.text

    ok = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {col: {"address": "12 Rizal St", "lat": 10.3, "lng": 123.8}}},
    )
    assert ok.status_code == 201, ok.text


@pytest.mark.parametrize(
    "config",
    [
        {"default_center": {"lat": 10.3}},  # half a centre
        {"default_center": {"lat": 100.0, "lng": 0.0}},
        {"default_center": "Cebu"},
        {"default_zoom": 25},
        {"default_zoom": 2.5},
        {"map_link": "mapquest"},
    ],
)
async def test_a_broken_config_is_refused_at_config_time(client, list_uuid, config):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "site",
            "display_name": "Site",
            "type": "location",
            "config": config,
        },
    )
    # 422 where pydantic's own typing catches it first (a `default_center` that is
    # not an object, a fractional `default_zoom`), 400 where the service does. Both
    # are a refusal, and which layer speaks is not what this is pinning.
    assert resp.status_code in (400, 422), resp.text


async def test_filters_and_sorts_by_the_address_not_the_blob(client, list_uuid):
    """The reason `_field` has a branch: `->>` on an object is `{"address": …}`, so
    without one a filter would be asking questions of that string."""
    col = await _add_location_column(client, list_uuid)
    for address in ("Cebu City", "Angeles City", "Baguio City"):
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/",
            json={"data": {col: {"address": address, "lat": 10.0, "lng": 123.0}}},
        )
        assert resp.status_code == 201, resp.text

    ordered = await client.get(
        f"/api/lists/{list_uuid}/items/",
        params={"sort_by": col, "sort_dir": "asc"},
    )
    assert [r["data"][col]["address"] for r in ordered.json()] == [
        "Angeles City",
        "Baguio City",
        "Cebu City",
    ]

    filters = json.dumps(
        {
            "conjunction": "and",
            "conditions": [
                {"column_uuid": col, "operator": "contains", "value": "Cebu"}
            ],
        }
    )
    matching = await client.get(
        f"/api/lists/{list_uuid}/items/", params={"filters": filters}
    )
    assert [r["data"][col]["address"] for r in matching.json()] == ["Cebu City"]

    # And the negative: "address" is a key of the blob, never a value in it.
    blob = json.dumps(
        {
            "conjunction": "and",
            "conditions": [
                {"column_uuid": col, "operator": "contains", "value": "address"}
            ],
        }
    )
    none_of_them = await client.get(
        f"/api/lists/{list_uuid}/items/", params={"filters": blob}
    )
    assert none_of_them.json() == []


async def test_search_matches_the_address_not_the_blob(client, list_uuid):
    col = await _add_location_column(client, list_uuid)
    await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {col: {"address": "Cebu City", "lat": 10.3157, "lng": 123.8854}}},
    )

    found = await client.get(f"/api/lists/{list_uuid}/items/", params={"q": "cebu"})
    assert len(found.json()) == 1

    # Neither the key names nor a digit of the latitude may match.
    for term in ("address", "lat", "10.3157"):
        missed = await client.get(
            f"/api/lists/{list_uuid}/items/", params={"q": term}
        )
        assert missed.json() == [], f"{term!r} matched the serialized blob"


async def test_export_writes_one_line_and_reimports_unchanged(client, list_uuid):
    """The canonical text form is what makes a location column round-trip through a
    file — and what a conversion to `text` produces."""
    col = await _add_location_column(client, list_uuid)
    await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={
            "data": {
                col: {"address": "12 Rizal St, Cebu City", "lat": 10.3157, "lng": 123.8854}
            }
        },
    )

    exported = await client.post(
        f"/api/lists/{list_uuid}/export", json={"format": "csv"}
    )
    assert exported.status_code == 200, exported.text
    assert "12 Rizal St, Cebu City @10.31570,123.88540" in exported.text
    # Not a Python dict repr, which is what `str()` on the cell would have written.
    assert "'address'" not in exported.text

    again = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("rows.csv", exported.text, "text/csv")},
    )
    assert again.status_code == 200, again.text
    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert [r["data"][col] for r in items] == [
        {"address": "12 Rizal St, Cebu City", "lat": 10.3157, "lng": 123.8854},
    ] * 2


@pytest.mark.parametrize(
    "written,expected",
    [
        ("12 Rizal St @10.31570,123.88540", {"address": "12 Rizal St", "lat": 10.3157, "lng": 123.8854}),
        ("@10.31570,123.88540", {"address": None, "lat": 10.3157, "lng": 123.8854}),
        ("12 Rizal St", {"address": "12 Rizal St", "lat": None, "lng": None}),
        # An "@" that belongs to the address is not a coordinate suffix.
        ("Unit 3 @ The Mall", {"address": "Unit 3 @ The Mall", "lat": None, "lng": None}),
        # Split from the right, so an address containing "@" survives.
        (
            "Unit 3 @ The Mall @10.00000,123.00000",
            {"address": "Unit 3 @ The Mall", "lat": 10.0, "lng": 123.0},
        ),
    ],
)
async def test_import_reads_the_written_form(client, list_uuid, written, expected):
    col = await _add_location_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("rows.csv", f'site\n"{written}"\n', "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 1, resp.text

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert items[0]["data"][col] == expected


async def test_converting_to_text_writes_the_line_not_a_dict(client, list_uuid):
    """A conversion is the point of no return, so a dict repr baked into every
    record would be permanent."""
    col = await _add_location_column(client, list_uuid)
    await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {col: {"address": "Cebu City", "lat": 10.3157, "lng": 123.8854}}},
    )

    report = await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert",
        json={"type": "text", "config": {}},
    )
    assert report.status_code == 200, report.text
    assert report.json()["converted"] == 1
    assert report.json()["failed"] == 0

    applied = await client.post(
        f"/api/lists/{list_uuid}/columns/{col}/convert/apply",
        json={"type": "text", "config": report.json()["config"]},
    )
    assert applied.status_code == 200, applied.text

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert items[0]["data"][col] == "Cebu City @10.31570,123.88540"


async def test_converting_from_text_reads_the_line_back(client, list_uuid, add_column):
    text = await add_column(list_uuid, name="site", display_name="Site", type="text")
    await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {text: "Cebu City @10.31570,123.88540"}},
    )

    report = await client.post(
        f"/api/lists/{list_uuid}/columns/{text}/convert",
        json={"type": "location", "config": {}},
    )
    assert report.status_code == 200, report.text
    assert report.json()["converted"] == 1

    applied = await client.post(
        f"/api/lists/{list_uuid}/columns/{text}/convert/apply",
        json={"type": "location", "config": report.json()["config"]},
    )
    assert applied.status_code == 200, applied.text

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert items[0]["data"][text] == {
        "address": "Cebu City",
        "lat": 10.3157,
        "lng": 123.8854,
    }


async def test_unique_compares_the_whole_place(client, list_uuid):
    """JSONB containment compares objects natively, so `unique` needed nothing."""
    col = await _add_location_column(client, list_uuid, unique=True)
    place = {"address": "12 Rizal St", "lat": 10.3157, "lng": 123.8854}
    first = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: place}}
    )
    assert first.status_code == 201, first.text

    same = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: place}}
    )
    assert same.status_code == 409, same.text

    # A different pin at the same address is a different place.
    moved = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {col: {**place, "lat": 10.4}}},
    )
    assert moved.status_code == 201, moved.text
