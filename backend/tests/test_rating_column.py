"""The `rating` column type, over the HTTP API.

A rating is a number in a costume, and the tests that matter are the ones proving
the costume does not change how it behaves. It sorts and filters *numerically*
(10 after 9, not before it), and a metric can average it — both of which come from
`NUMERIC_TYPES` rather than from anything written for this type.
"""
import json

import pytest


async def _add_rating_column(client, list_uuid: str, name="score", **config) -> str:
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": name,
            "display_name": name.title(),
            "type": "rating",
            "config": config,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def test_accepts_a_rating_on_the_default_scale(client, list_uuid):
    col = await _add_rating_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: 4}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == 4


@pytest.mark.parametrize("value", [0, 6, -1, 2.5, "4", True])
async def test_rejects_anything_off_the_scale(client, list_uuid, value):
    """Zero included: an unrated record is an empty cell, not a nought."""
    col = await _add_rating_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
    )
    assert resp.status_code == 400, f"{value!r} was accepted: {resp.text}"


async def test_max_sets_the_top_of_the_scale(client, list_uuid):
    col = await _add_rating_column(client, list_uuid, max=10)
    ok = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: 9}}
    )
    assert ok.status_code == 201, ok.text
    nope = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: 11}}
    )
    assert nope.status_code == 400


@pytest.mark.parametrize("bad", [0, 11, 2.5, "five"])
async def test_a_scale_must_be_readable(client, list_uuid, bad):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "score",
            "display_name": "Score",
            "type": "rating",
            "config": {"max": bad},
        },
    )
    assert resp.status_code == 400, resp.text


async def test_a_rating_column_has_no_min(client, list_uuid):
    """A configurable floor would give the column two ways to say "unrated"."""
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "score",
            "display_name": "Score",
            "type": "rating",
            "config": {"min": 2},
        },
    )
    assert resp.status_code == 400, resp.text


async def test_rating_icon_is_checked(client, list_uuid):
    await _add_rating_column(client, list_uuid, rating_icon="heart")

    bad = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "other",
            "display_name": "Other",
            "type": "rating",
            "config": {"rating_icon": "thumbs"},
        },
    )
    assert bad.status_code == 400, bad.text


async def test_it_sorts_and_filters_as_a_number(client, list_uuid):
    """The whole reason `NUMERIC_TYPES` exists: as text, 10 sorts before 9."""
    col = await _add_rating_column(client, list_uuid, max=10)
    # Inserted out of order, so a real sort is distinguishable from insertion order.
    for value in (9, 10, 2):
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
        )
        assert resp.status_code == 201, resp.text

    ordered = await client.get(
        f"/api/lists/{list_uuid}/items/",
        params={"sort_by": col, "sort_dir": "asc"},
    )
    assert ordered.status_code == 200, ordered.text
    assert [r["data"][col] for r in ordered.json()] == [2, 9, 10]

    filters = json.dumps(
        {
            "conjunction": "and",
            "conditions": [{"column_uuid": col, "operator": "gt", "value": 9}],
        }
    )
    above = await client.get(
        f"/api/lists/{list_uuid}/items/", params={"filters": filters}
    )
    assert above.status_code == 200, above.text
    assert [r["data"][col] for r in above.json()] == [10]


async def test_a_metric_can_average_it(client, list_uuid):
    col = await _add_rating_column(client, list_uuid)
    for value in (2, 4):
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
        )
        assert resp.status_code == 201, resp.text

    created = await client.post(
        f"/api/lists/{list_uuid}/metrics/",
        json={
            "name": "mean_score",
            "display_name": "Mean score",
            "config": {"formula": "AVG(score)"},
        },
    )
    assert created.status_code == 201, created.text

    body = (await client.get(f"/api/lists/{list_uuid}/metrics/values")).json()
    assert next(v["value"] for v in body["values"] if v["name"] == "mean_score") == 3
