"""Server-side filtering and sorting of records via a view's config."""


async def _view_uuid(client, list_uuid):
    views = (await client.get(f"/api/lists/{list_uuid}/views/")).json()
    return views[0]["uuid"]


async def test_view_filter_and_sort(client, list_uuid, add_column):
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    score = await add_column(
        list_uuid, name="score", display_name="Score", type="number"
    )

    async def add(n, s):
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {name: n, score: s}}
        )

    await add("alpha", 5)
    await add("beta", 20)
    await add("gamma", 15)

    view = await _view_uuid(client, list_uuid)
    await client.patch(
        f"/api/lists/{list_uuid}/views/{view}",
        json={
            "config": {
                "filters": {
                    "conjunction": "and",
                    "conditions": [
                        {"column_uuid": score, "operator": ">", "value": 10}
                    ],
                },
                "sorts": [{"column_uuid": score, "direction": "desc"}],
            }
        },
    )

    rows = (
        await client.get(f"/api/lists/{list_uuid}/items/?view_uuid={view}")
    ).json()
    assert [(r["data"][name], r["data"][score]) for r in rows] == [
        ("beta", 20),
        ("gamma", 15),
    ]


async def test_view_contains_filter(client, list_uuid, add_column):
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    for n in ["apple", "banana", "apricot"]:
        await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {name: n}})

    view = await _view_uuid(client, list_uuid)
    await client.patch(
        f"/api/lists/{list_uuid}/views/{view}",
        json={
            "config": {
                "filters": {
                    "conjunction": "and",
                    "conditions": [
                        {"column_uuid": name, "operator": "contains", "value": "ap"}
                    ],
                }
            }
        },
    )

    rows = (
        await client.get(f"/api/lists/{list_uuid}/items/?view_uuid={view}")
    ).json()
    assert sorted(r["data"][name] for r in rows) == ["apple", "apricot"]


async def test_number_sort_is_numeric_not_lexicographic(client, list_uuid, add_column):
    score = await add_column(
        list_uuid, name="score", display_name="Score", type="number"
    )
    for s in [9, 10, 100, 2]:
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {score: s}}
        )

    view = await _view_uuid(client, list_uuid)
    await client.patch(
        f"/api/lists/{list_uuid}/views/{view}",
        json={"config": {"sorts": [{"column_uuid": score, "direction": "asc"}]}},
    )

    rows = (
        await client.get(f"/api/lists/{list_uuid}/items/?view_uuid={view}")
    ).json()
    # Numeric, not text ordering (which would be 10, 100, 2, 9).
    assert [r["data"][score] for r in rows] == [2, 9, 10, 100]


async def test_no_view_returns_all_in_insertion_order(client, list_uuid, add_column):
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    for n in ["a", "b", "c"]:
        await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {name: n}})
    rows = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert [r["data"][name] for r in rows] == ["a", "b", "c"]
