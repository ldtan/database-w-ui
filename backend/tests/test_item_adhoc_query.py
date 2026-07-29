"""Filtering and grouping a list for one request, without saving a view.

Filtering used to be reachable only by writing the conditions onto a view, so
"show me just the overdue ones for a minute" was a permanent change to a shared
object that needed `configure` to make. These cover the parameters that replaced
that: that they narrow the read without touching anything, that they *replace*
what the view saved rather than stacking on top of it, and that the distinction
between omitting one and passing it empty is real — the first defers to the view,
the second overrules it with nothing.
"""
import json


def _filters(*conditions, conjunction="and"):
    """A filter group as the query parameter carries it."""
    return json.dumps({"conjunction": conjunction, "conditions": list(conditions)})


def _cond(column, operator, value=None):
    return {"column_uuid": column, "operator": operator, "value": value}


async def _view_uuid(client, list_uuid):
    views = (await client.get(f"/api/lists/{list_uuid}/views/")).json()
    return views[0]["uuid"]


async def _filtered_view(client, list_uuid, column, operator, value, name="narrow"):
    """A view whose saved filter the request's own filter has to displace."""
    resp = await client.post(
        f"/api/lists/{list_uuid}/views/",
        json={
            "name": name,
            "display_name": name.title(),
            "config": {
                "filters": {
                    "conjunction": "and",
                    "conditions": [_cond(column, operator, value)],
                }
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def _seed_scores(client, list_uuid, column, count):
    for value in range(count):
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {column: value}}
        )


# -- filtering ------------------------------------------------------------


async def test_filters_narrow_the_list_without_a_view(client, list_uuid, add_column):
    """The point of the whole change: a filter, and no view anywhere in the request."""
    score = await add_column(
        list_uuid, name="score", display_name="Score", type="number"
    )
    await _seed_scores(client, list_uuid, score, 20)

    res = await client.get(
        f"/api/lists/{list_uuid}/items/",
        params={"filters": _filters(_cond(score, ">=", 15))},
    )

    assert res.status_code == 200, res.text
    assert [r["data"][score] for r in res.json()] == [15, 16, 17, 18, 19]
    # The total describes the filtered list, or the paginator offers pages the
    # filter has already emptied.
    assert res.headers["X-Total-Count"] == "5"


async def test_ad_hoc_filters_replace_the_views_filter(client, list_uuid, add_column):
    """Replace, not narrow — the same rule `sort_by` follows.

    The alternative is that the two are ANDed, which cannot express "show me
    something the view hides" and would make the filter dialog (seeded from the
    view's own conditions) silently intersect a set with itself.
    """
    score = await add_column(
        list_uuid, name="score", display_name="Score", type="number"
    )
    await _seed_scores(client, list_uuid, score, 20)
    view = await _filtered_view(client, list_uuid, score, ">=", 15)

    res = await client.get(
        f"/api/lists/{list_uuid}/items/",
        params={"view_uuid": view, "filters": _filters(_cond(score, "<", 5))},
    )

    # ANDing the two would match nothing at all.
    assert [r["data"][score] for r in res.json()] == [0, 1, 2, 3, 4]
    assert res.headers["X-Total-Count"] == "5"


async def test_an_empty_filter_group_reads_the_whole_list(client, list_uuid, add_column):
    """How a caller asks a filtered view for everything.

    Only expressible because empty and absent mean different things here; if they
    were the same, a view that filters could never be looked past.
    """
    score = await add_column(
        list_uuid, name="score", display_name="Score", type="number"
    )
    await _seed_scores(client, list_uuid, score, 20)
    view = await _filtered_view(client, list_uuid, score, ">=", 15)

    res = await client.get(
        f"/api/lists/{list_uuid}/items/",
        params={"view_uuid": view, "filters": _filters()},
    )

    assert res.headers["X-Total-Count"] == "20"


async def test_omitting_filters_still_uses_the_view(client, list_uuid, add_column):
    """The other half of that distinction, and the compatibility guarantee: a
    caller that knows nothing about these parameters reads exactly what it did."""
    score = await add_column(
        list_uuid, name="score", display_name="Score", type="number"
    )
    await _seed_scores(client, list_uuid, score, 20)
    view = await _filtered_view(client, list_uuid, score, ">=", 15)

    res = await client.get(
        f"/api/lists/{list_uuid}/items/", params={"view_uuid": view}
    )

    assert res.headers["X-Total-Count"] == "5"


async def test_the_search_narrows_the_filter_rather_than_replacing_it(
    client, list_uuid, add_column
):
    """`q` is not an override. It is the one thing here that composes."""
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    score = await add_column(
        list_uuid, name="score", display_name="Score", type="number"
    )
    for n, s in [("apple", 1), ("apricot", 9), ("banana", 9), ("cherry", 1)]:
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {name: n, score: s}}
        )

    res = await client.get(
        f"/api/lists/{list_uuid}/items/",
        params={"q": "ap", "filters": _filters(_cond(score, ">=", 5))},
    )

    # "ap" alone matches two; score >= 5 alone matches two; together, one.
    assert [r["data"][name] for r in res.json()] == ["apricot"]
    assert res.headers["X-Total-Count"] == "1"


async def test_filtering_does_not_touch_the_view(client, list_uuid, add_column):
    """The reason this exists rather than another PATCH: asking a question of a
    list must not change the list for everyone else looking at it."""
    score = await add_column(
        list_uuid, name="score", display_name="Score", type="number"
    )
    await _seed_scores(client, list_uuid, score, 20)
    view = await _view_uuid(client, list_uuid)
    before = (await client.get(f"/api/lists/{list_uuid}/views/{view}")).json()["config"]

    await client.get(
        f"/api/lists/{list_uuid}/items/",
        params={"view_uuid": view, "filters": _filters(_cond(score, ">=", 15))},
    )

    after = (await client.get(f"/api/lists/{list_uuid}/views/{view}")).json()["config"]
    assert after == before


async def test_an_unknown_column_in_a_filter_is_ignored(client, list_uuid, add_column):
    """Same rule a saved view already follows (`apply_filters`): a condition
    naming a column that has since been deleted drops out rather than 400ing a
    read, so deleting a column cannot break every open grid."""
    score = await add_column(
        list_uuid, name="score", display_name="Score", type="number"
    )
    await _seed_scores(client, list_uuid, score, 5)
    stale = "0e37a3a4-0000-4000-8000-000000000000"

    res = await client.get(
        f"/api/lists/{list_uuid}/items/",
        params={"filters": _filters(_cond(stale, "is", "anything"))},
    )

    assert res.status_code == 200
    assert res.headers["X-Total-Count"] == "5"


async def test_malformed_filters_are_rejected(client, list_uuid):
    """A bad parameter is a 422, like any other — not a silently unfiltered list,
    which would hand back more records than the caller asked to see."""
    not_json = await client.get(
        f"/api/lists/{list_uuid}/items/", params={"filters": "score > 5"}
    )
    assert not_json.status_code == 422

    wrong_shape = await client.get(
        f"/api/lists/{list_uuid}/items/",
        params={"filters": json.dumps({"conjunction": "xor", "conditions": []})},
    )
    assert wrong_shape.status_code == 422


# -- grouping and ordering ------------------------------------------------


async def test_group_by_makes_a_groups_records_adjacent(client, list_uuid, add_column):
    """Grouping leads the order, and the sort survives *inside* each group.

    The page used to send its grouping as `sort_by`, which kept groups together
    but threw the sort away; ordering by the group column first and the caller's
    sort second is what the parameter buys.
    """
    team = await add_column(list_uuid, name="team", display_name="Team", type="text")
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    for t, n in [("b", "1"), ("a", "3"), ("b", "2"), ("a", "1"), ("b", "3"), ("a", "2")]:
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {team: t, name: n}}
        )

    res = await client.get(
        f"/api/lists/{list_uuid}/items/",
        params={"group_by": team, "sort_by": name, "sort_dir": "desc"},
    )

    assert [(r["data"][team], r["data"][name]) for r in res.json()] == [
        ("a", "3"),
        ("a", "2"),
        ("a", "1"),
        ("b", "3"),
        ("b", "2"),
        ("b", "1"),
    ]


async def test_group_by_replaces_the_views_grouping(client, list_uuid, add_column):
    team = await add_column(list_uuid, name="team", display_name="Team", type="text")
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    for t, n in [("b", "1"), ("a", "2"), ("b", "3"), ("a", "4")]:
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {team: t, name: n}}
        )
    view = await _view_uuid(client, list_uuid)
    await client.patch(
        f"/api/lists/{list_uuid}/views/{view}",
        json={"config": {"group_by": {"column_uuid": team}}},
    )

    by_view = await client.get(
        f"/api/lists/{list_uuid}/items/", params={"view_uuid": view}
    )
    assert [r["data"][team] for r in by_view.json()] == ["a", "a", "b", "b"]

    by_caller = await client.get(
        f"/api/lists/{list_uuid}/items/",
        params={"view_uuid": view, "group_by": name},
    )
    assert [r["data"][name] for r in by_caller.json()] == ["1", "2", "3", "4"]


async def test_empty_group_by_means_no_grouping(client, list_uuid, add_column):
    """Clearing the grouping on a view that saves one. Without a spelling for
    "none", the view's grouping could be changed but never removed."""
    team = await add_column(list_uuid, name="team", display_name="Team", type="text")
    for t in ["b", "a", "b", "a"]:
        await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {team: t}})
    view = await _view_uuid(client, list_uuid)
    await client.patch(
        f"/api/lists/{list_uuid}/views/{view}",
        json={"config": {"group_by": {"column_uuid": team}}},
    )

    res = await client.get(
        f"/api/lists/{list_uuid}/items/",
        params={"view_uuid": view, "group_by": ""},
    )

    assert [r["data"][team] for r in res.json()] == ["b", "a", "b", "a"]  # insertion


async def test_empty_sort_by_means_no_order(client, list_uuid, add_column):
    """Same spelling, same meaning, for the parameter that had the behaviour first."""
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    for n in ["c", "a", "b"]:
        await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {name: n}})
    view = await _view_uuid(client, list_uuid)
    await client.patch(
        f"/api/lists/{list_uuid}/views/{view}",
        json={"config": {"sorts": [{"column_uuid": name, "direction": "asc"}]}},
    )

    res = await client.get(
        f"/api/lists/{list_uuid}/items/", params={"view_uuid": view, "sort_by": ""}
    )

    assert [r["data"][name] for r in res.json()] == ["c", "a", "b"]  # insertion


# -- the numbers above the grid -------------------------------------------


async def test_metric_values_take_the_same_filter(client, list_uuid, add_column):
    """A total sitting above a filtered grid has to be a total of that grid."""
    score = await add_column(
        list_uuid, name="score", display_name="Score", type="number"
    )
    await _seed_scores(client, list_uuid, score, 10)  # 0..9, summing to 45
    await client.post(
        f"/api/lists/{list_uuid}/metrics/",
        json={
            "name": "total",
            "display_name": "Total",
            "config": {"formula": "SUM(score)"},
        },
    )

    whole = (await client.get(f"/api/lists/{list_uuid}/metrics/values")).json()
    assert whole["values"][0]["value"] == 45

    filtered = await client.get(
        f"/api/lists/{list_uuid}/metrics/values",
        params={"filters": _filters(_cond(score, ">=", 8))},
    )
    body = filtered.json()
    assert body["row_count"] == 2
    assert body["values"][0]["value"] == 17  # 8 + 9


async def test_there_is_one_read_path_for_records(client, list_uuid):
    """`POST /items/query` is gone.

    It was an unreachable second answer to "which records is this request about",
    built off its own permission filter rather than `visible_stmt` — the exact
    shape of drift that let the listing and its count disagree once before.
    """
    res = await client.post(f"/api/lists/{list_uuid}/items/query", json={})
    assert res.status_code in (404, 405)
