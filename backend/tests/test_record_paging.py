"""Paging, ordering and the total count of the live records listing.

The listing used to return a page while saying nothing about how many records the
caller had matched, and the browser paged and sorted over whatever had arrived —
so a table quietly ended at the endpoint's default 100 rows and a sort ordered only
the rows inside that cap. These cover the endpoint that replaced it: that a page is
a page *of the table*, that the total describes the whole match and not the page,
and that the order is imposed before the page is cut rather than after.
"""


async def _view_uuid(client, table_uuid):
    views = (await client.get(f"/api/tables/{table_uuid}/views/")).json()
    return views[0]["uuid"]


async def _seed(client, table_uuid, column, count):
    """`count` records, named so that lexical order is also insertion order."""
    for i in range(count):
        await client.post(
            f"/api/tables/{table_uuid}/records/",
            json={"data": {column: f"row-{i:03d}"}},
        )


async def test_total_count_counts_the_table_not_the_page(client, table_uuid, add_column):
    name = await add_column(table_uuid, name="name", display_name="Name", type="text")
    await _seed(client, table_uuid, name, 12)

    res = await client.get(f"/api/tables/{table_uuid}/records/?skip=0&limit=5")

    assert len(res.json()) == 5
    assert res.headers["X-Total-Count"] == "12"


async def test_paging_reaches_past_the_default_limit(client, table_uuid, add_column):
    """The bug itself: more records than the endpoint's default `limit`.

    Kept just over 100 rather than at demo scale — this is about the boundary the
    default sits on, and every extra row is another round trip through the API.
    """
    name = await add_column(table_uuid, name="name", display_name="Name", type="text")
    await _seed(client, table_uuid, name, 105)

    first = await client.get(f"/api/tables/{table_uuid}/records/")
    assert len(first.json()) == 100  # the default page
    assert first.headers["X-Total-Count"] == "105"  # but it says there are more

    last = await client.get(f"/api/tables/{table_uuid}/records/?skip=100&limit=100")
    assert [r["data"][name] for r in last.json()] == [
        f"row-{i:03d}" for i in range(100, 105)
    ]


async def test_pages_do_not_overlap_or_skip(client, table_uuid, add_column):
    name = await add_column(table_uuid, name="name", display_name="Name", type="text")
    await _seed(client, table_uuid, name, 25)

    seen = []
    for skip in (0, 10, 20):
        page = await client.get(f"/api/tables/{table_uuid}/records/?skip={skip}&limit=10")
        seen.extend(r["uuid"] for r in page.json())

    assert len(seen) == 25
    assert len(set(seen)) == 25  # every record once, none twice


async def test_sort_orders_the_table_before_the_page_is_cut(
    client, table_uuid, add_column
):
    """The reason ordering had to move server-side.

    Sorting descending must make the *last* record the first row of page one. A
    sort applied to an already-cut page could only reorder the page, so page one
    would still hold the lowest values.
    """
    name = await add_column(table_uuid, name="name", display_name="Name", type="text")
    await _seed(client, table_uuid, name, 30)

    page = await client.get(
        f"/api/tables/{table_uuid}/records/?limit=3&sort_by={name}&sort_dir=desc"
    )

    assert [r["data"][name] for r in page.json()] == ["row-029", "row-028", "row-027"]


async def test_number_sort_is_numeric_not_lexical(client, table_uuid, add_column):
    score = await add_column(
        table_uuid, name="score", display_name="Score", type="number"
    )
    for value in (2, 10, 9):
        await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {score: value}}
        )

    page = await client.get(
        f"/api/tables/{table_uuid}/records/?sort_by={score}&sort_dir=asc"
    )

    # Lexically "10" sorts before "2"; the column is cast to numeric so it does not.
    assert [r["data"][score] for r in page.json()] == [2, 9, 10]


async def test_caller_sort_replaces_the_views_sort(client, table_uuid, add_column):
    """Clicking a header means "order by this", not "then by this"."""
    name = await add_column(table_uuid, name="name", display_name="Name", type="text")
    score = await add_column(
        table_uuid, name="score", display_name="Score", type="number"
    )
    for n, s in [("alpha", 3), ("beta", 1), ("gamma", 2)]:
        await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {name: n, score: s}}
        )

    view = await _view_uuid(client, table_uuid)
    await client.patch(
        f"/api/tables/{table_uuid}/views/{view}",
        json={"config": {"sorts": [{"column_uuid": score, "direction": "asc"}]}},
    )

    by_view = await client.get(f"/api/tables/{table_uuid}/records/?view_uuid={view}")
    assert [r["data"][name] for r in by_view.json()] == ["beta", "gamma", "alpha"]

    by_caller = await client.get(
        f"/api/tables/{table_uuid}/records/?view_uuid={view}&sort_by={name}&sort_dir=desc"
    )
    assert [r["data"][name] for r in by_caller.json()] == ["gamma", "beta", "alpha"]


async def test_total_respects_the_views_filter(client, table_uuid, add_column):
    """The count and the page must answer the same question, or the paginator
    offers pages the filter has already emptied."""
    score = await add_column(
        table_uuid, name="score", display_name="Score", type="number"
    )
    for value in range(20):
        await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {score: value}}
        )

    view = await _view_uuid(client, table_uuid)
    await client.patch(
        f"/api/tables/{table_uuid}/views/{view}",
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

    res = await client.get(f"/api/tables/{table_uuid}/records/?view_uuid={view}&limit=2")

    assert len(res.json()) == 2
    assert res.headers["X-Total-Count"] == "5"


async def test_total_respects_the_search(client, table_uuid, add_column):
    name = await add_column(table_uuid, name="name", display_name="Name", type="text")
    for n in ["apple", "banana", "apricot", "cherry"]:
        await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {name: n}})

    res = await client.get(f"/api/tables/{table_uuid}/records/?q=ap&limit=1")

    assert len(res.json()) == 1
    assert res.headers["X-Total-Count"] == "2"


async def test_sort_by_calculated_column_orders_by_the_computed_value(
    client, table_uuid, add_column
):
    """A calculated column is stored, so ordering by it is an ordinary `data ->> key`.

    This used to degrade to the stable default order — the term was dropped because
    there was no field to order by — and the grid did not offer the header at all.
    """
    score = await add_column(
        table_uuid, name="score", display_name="Score", type="number"
    )
    doubled = await add_column(
        table_uuid,
        name="doubled",
        display_name="Doubled",
        type="calculated",
        config={"formula": "score * 2"},
    )
    for value in (1, 2, 3):
        await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {score: value}}
        )

    res = await client.get(
        f"/api/tables/{table_uuid}/records/?sort_by={doubled}&sort_dir=desc"
    )

    assert res.status_code == 200
    assert [r["data"][doubled] for r in res.json()] == [6, 4, 2]
    assert [r["data"][score] for r in res.json()] == [3, 2, 1]

    asc = await client.get(
        f"/api/tables/{table_uuid}/records/?sort_by={doubled}&sort_dir=asc"
    )
    assert [r["data"][doubled] for r in asc.json()] == [2, 4, 6]


async def test_a_numeric_formula_sorts_numerically_and_a_text_one_alphabetically(
    client, table_uuid, add_column
):
    """One table, two formulas, no declared output type.

    The language decides the type per formula, so `_order_terms` emits a
    `jsonb_typeof`-guarded numeric reading with the text reading behind it. Numbers
    must not sort as text (`100` before `9`), and text must not all collapse to NULL.
    """
    score = await add_column(
        table_uuid, name="score", display_name="Score", type="number"
    )
    label = await add_column(
        table_uuid, name="label", display_name="Label", type="text"
    )
    scaled = await add_column(
        table_uuid,
        name="scaled",
        display_name="Scaled",
        type="calculated",
        config={"formula": "score * 1"},
    )
    shout = await add_column(
        table_uuid,
        name="shout",
        display_name="Shout",
        type="calculated",
        config={"formula": "UPPER(label)"},
    )
    for value, text in ((9, "beta"), (100, "alpha"), (10, "gamma")):
        await client.post(
            f"/api/tables/{table_uuid}/records/",
            json={"data": {score: value, label: text}},
        )

    numeric = await client.get(
        f"/api/tables/{table_uuid}/records/?sort_by={scaled}&sort_dir=asc"
    )
    assert [r["data"][scaled] for r in numeric.json()] == [9, 10, 100]

    textual = await client.get(
        f"/api/tables/{table_uuid}/records/?sort_by={shout}&sort_dir=asc"
    )
    assert [r["data"][shout] for r in textual.json()] == ["ALPHA", "BETA", "GAMMA"]


async def test_sort_dir_is_validated(client, table_uuid, add_column):
    name = await add_column(table_uuid, name="name", display_name="Name", type="text")

    res = await client.get(f"/api/tables/{table_uuid}/records/?sort_by={name}&sort_dir=up")

    assert res.status_code == 422
