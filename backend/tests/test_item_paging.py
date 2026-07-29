"""Paging, ordering and the total count of the live records listing.

The listing used to return a page while saying nothing about how many records the
caller had matched, and the browser paged and sorted over whatever had arrived —
so a list quietly ended at the endpoint's default 100 rows and a sort ordered only
the rows inside that cap. These cover the endpoint that replaced it: that a page is
a page *of the list*, that the total describes the whole match and not the page,
and that the order is imposed before the page is cut rather than after.
"""


async def _view_uuid(client, list_uuid):
    views = (await client.get(f"/api/lists/{list_uuid}/views/")).json()
    return views[0]["uuid"]


async def _seed(client, list_uuid, column, count):
    """`count` records, named so that lexical order is also insertion order."""
    for i in range(count):
        await client.post(
            f"/api/lists/{list_uuid}/items/",
            json={"data": {column: f"row-{i:03d}"}},
        )


async def test_total_count_counts_the_list_not_the_page(client, list_uuid, add_column):
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    await _seed(client, list_uuid, name, 12)

    res = await client.get(f"/api/lists/{list_uuid}/items/?skip=0&limit=5")

    assert len(res.json()) == 5
    assert res.headers["X-Total-Count"] == "12"


async def test_paging_reaches_past_the_default_limit(client, list_uuid, add_column):
    """The bug itself: more records than the endpoint's default `limit`.

    Kept just over 100 rather than at demo scale — this is about the boundary the
    default sits on, and every extra row is another round trip through the API.
    """
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    await _seed(client, list_uuid, name, 105)

    first = await client.get(f"/api/lists/{list_uuid}/items/")
    assert len(first.json()) == 100  # the default page
    assert first.headers["X-Total-Count"] == "105"  # but it says there are more

    last = await client.get(f"/api/lists/{list_uuid}/items/?skip=100&limit=100")
    assert [r["data"][name] for r in last.json()] == [
        f"row-{i:03d}" for i in range(100, 105)
    ]


async def test_pages_do_not_overlap_or_skip(client, list_uuid, add_column):
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    await _seed(client, list_uuid, name, 25)

    seen = []
    for skip in (0, 10, 20):
        page = await client.get(f"/api/lists/{list_uuid}/items/?skip={skip}&limit=10")
        seen.extend(r["uuid"] for r in page.json())

    assert len(seen) == 25
    assert len(set(seen)) == 25  # every record once, none twice


async def test_sort_orders_the_list_before_the_page_is_cut(
    client, list_uuid, add_column
):
    """The reason ordering had to move server-side.

    Sorting descending must make the *last* record the first row of page one. A
    sort applied to an already-cut page could only reorder the page, so page one
    would still hold the lowest values.
    """
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    await _seed(client, list_uuid, name, 30)

    page = await client.get(
        f"/api/lists/{list_uuid}/items/?limit=3&sort_by={name}&sort_dir=desc"
    )

    assert [r["data"][name] for r in page.json()] == ["row-029", "row-028", "row-027"]


async def test_number_sort_is_numeric_not_lexical(client, list_uuid, add_column):
    score = await add_column(
        list_uuid, name="score", display_name="Score", type="number"
    )
    for value in (2, 10, 9):
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {score: value}}
        )

    page = await client.get(
        f"/api/lists/{list_uuid}/items/?sort_by={score}&sort_dir=asc"
    )

    # Lexically "10" sorts before "2"; the column is cast to numeric so it does not.
    assert [r["data"][score] for r in page.json()] == [2, 9, 10]


async def test_caller_sort_replaces_the_views_sort(client, list_uuid, add_column):
    """Clicking a header means "order by this", not "then by this"."""
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    score = await add_column(
        list_uuid, name="score", display_name="Score", type="number"
    )
    for n, s in [("alpha", 3), ("beta", 1), ("gamma", 2)]:
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {name: n, score: s}}
        )

    view = await _view_uuid(client, list_uuid)
    await client.patch(
        f"/api/lists/{list_uuid}/views/{view}",
        json={"config": {"sorts": [{"column_uuid": score, "direction": "asc"}]}},
    )

    by_view = await client.get(f"/api/lists/{list_uuid}/items/?view_uuid={view}")
    assert [r["data"][name] for r in by_view.json()] == ["beta", "gamma", "alpha"]

    by_caller = await client.get(
        f"/api/lists/{list_uuid}/items/?view_uuid={view}&sort_by={name}&sort_dir=desc"
    )
    assert [r["data"][name] for r in by_caller.json()] == ["gamma", "beta", "alpha"]


async def test_total_respects_the_views_filter(client, list_uuid, add_column):
    """The count and the page must answer the same question, or the paginator
    offers pages the filter has already emptied."""
    score = await add_column(
        list_uuid, name="score", display_name="Score", type="number"
    )
    for value in range(20):
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {score: value}}
        )

    view = await _view_uuid(client, list_uuid)
    await client.patch(
        f"/api/lists/{list_uuid}/views/{view}",
        json={
            "config": {
                "filters": {
                    "conjunction": "and",
                    "conditions": [
                        {"column_uuid": score, "operator": ">=", "value": 15}
                    ],
                }
            }
        },
    )

    res = await client.get(f"/api/lists/{list_uuid}/items/?view_uuid={view}&limit=2")

    assert len(res.json()) == 2
    assert res.headers["X-Total-Count"] == "5"


async def test_total_respects_the_search(client, list_uuid, add_column):
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    for n in ["apple", "banana", "apricot", "cherry"]:
        await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {name: n}})

    res = await client.get(f"/api/lists/{list_uuid}/items/?q=ap&limit=1")

    assert len(res.json()) == 1
    assert res.headers["X-Total-Count"] == "2"


async def test_sort_by_calculated_column_falls_back_rather_than_emptying(
    client, list_uuid, add_column
):
    """A calculated column stores nothing, so there is no field to order by.

    The grid does not offer the header, but the query parameter is public. It must
    degrade to the stable default order rather than ordering every row by NULL and
    handing back an arbitrary page.
    """
    score = await add_column(
        list_uuid, name="score", display_name="Score", type="number"
    )
    doubled = await add_column(
        list_uuid,
        name="doubled",
        display_name="Doubled",
        type="calculated",
        config={"formula": "score * 2"},
    )
    for value in (1, 2, 3):
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {score: value}}
        )

    res = await client.get(
        f"/api/lists/{list_uuid}/items/?sort_by={doubled}&sort_dir=desc"
    )

    assert res.status_code == 200
    assert [r["data"][score] for r in res.json()] == [1, 2, 3]  # insertion order
    assert [r["data"][doubled] for r in res.json()] == [2, 4, 6]  # still computed


async def test_sort_dir_is_validated(client, list_uuid, add_column):
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")

    res = await client.get(f"/api/lists/{list_uuid}/items/?sort_by={name}&sort_dir=up")

    assert res.status_code == 422
