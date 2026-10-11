"""Server-side filtering and sorting of records via a view's config."""
import json


async def _view_uuid(client, table_uuid):
    views = (await client.get(f"/api/tables/{table_uuid}/views/")).json()
    return views[0]["uuid"]


async def test_view_filter_and_sort(client, table_uuid, add_column):
    name = await add_column(table_uuid, name="name", display_name="Name", type="text")
    score = await add_column(
        table_uuid, name="score", display_name="Score", type="number"
    )

    async def add(n, s):
        await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {name: n, score: s}}
        )

    await add("alpha", 5)
    await add("beta", 20)
    await add("gamma", 15)

    view = await _view_uuid(client, table_uuid)
    await client.patch(
        f"/api/tables/{table_uuid}/views/{view}",
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
        await client.get(f"/api/tables/{table_uuid}/records/?view_uuid={view}")
    ).json()
    assert [(r["data"][name], r["data"][score]) for r in rows] == [
        ("beta", 20),
        ("gamma", 15),
    ]


async def test_view_contains_filter(client, table_uuid, add_column):
    name = await add_column(table_uuid, name="name", display_name="Name", type="text")
    for n in ["apple", "banana", "apricot"]:
        await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {name: n}})

    view = await _view_uuid(client, table_uuid)
    await client.patch(
        f"/api/tables/{table_uuid}/views/{view}",
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
        await client.get(f"/api/tables/{table_uuid}/records/?view_uuid={view}")
    ).json()
    assert sorted(r["data"][name] for r in rows) == ["apple", "apricot"]


async def test_a_boolean_matches_a_checkbox_cell(client, table_uuid, add_column):
    """A JSON `true` in a condition must match a ticked checkbox.

    JSONB spells its booleans `true`/`false`, so `data ->> key` answers `"true"` —
    while Python's `str(True)` is `"True"`, which matches nothing. The filter used
    to send the second, so a checkbox filter written by any client that sends a
    real boolean (rather than the string, which is what the web editor's free-text
    box happened to produce) silently returned no rows at all.
    """
    done = await add_column(table_uuid, name="done", display_name="Done", type="checkbox")
    label = await add_column(table_uuid, name="label", display_name="Label", type="text")
    for text, flag in [("packed", True), ("pending", False)]:
        await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {label: text, done: flag}}
        )

    async def matching(value):
        filters = {
            "conjunction": "and",
            "conditions": [{"column_uuid": done, "operator": "is", "value": value}],
        }
        resp = await client.get(
            f"/api/tables/{table_uuid}/records/", params={"filters": json.dumps(filters)}
        )
        assert resp.status_code == 200, resp.text
        return sorted(r["data"][label] for r in resp.json())

    assert await matching(True) == ["packed"]
    assert await matching(False) == ["pending"]
    # The string spelling keeps working — it is what `data ->> key` literally holds,
    # and clients that already send it must not break.
    assert await matching("true") == ["packed"]


async def test_contains_treats_a_wildcard_as_a_character(client, table_uuid, add_column):
    """`%` and `_` are LIKE wildcards, and a filter value is not a pattern.

    Unescaped, a lone `%` matched every non-null cell and `a_c` matched `abc` — so
    a condition could quietly select far more than it named, and a saved view kept
    doing it. Every other ILIKE in the app already escaped; this one did not.
    """
    name = await add_column(table_uuid, name="name", display_name="Name", type="text")
    for n in ["100%", "abc", "a_c", "plain"]:
        await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {name: n}})

    async def matching(value):
        filters = {
            "conjunction": "and",
            "conditions": [
                {"column_uuid": name, "operator": "contains", "value": value}
            ],
        }
        resp = await client.get(
            f"/api/tables/{table_uuid}/records/", params={"filters": json.dumps(filters)}
        )
        assert resp.status_code == 200, resp.text
        return sorted(r["data"][name] for r in resp.json())

    # A wildcard is a character being searched for, not a pattern being run.
    assert await matching("%") == ["100%"]
    assert await matching("a_c") == ["a_c"]


async def test_an_unknown_operator_is_refused_not_ignored(client, table_uuid, add_column):
    """A filter that cannot be understood must not answer with the whole table.

    `_condition` returns None for an operator it does not recognise and
    `apply_filters` drops the condition — which for the *only* condition means no
    filter at all. A typo therefore used to read as "everything matched", which
    looks like data rather than a mistake, and a view saved with one carried it
    silently. It is a 422 at the schema now, on both the ad-hoc and the saved path.
    """
    name = await add_column(table_uuid, name="name", display_name="Name", type="text")
    for n in ["apple", "banana"]:
        await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {name: n}})

    filters = {
        "conjunction": "and",
        "conditions": [
            {"column_uuid": name, "operator": "kontains", "value": "ap"}
        ],
    }
    resp = await client.get(
        f"/api/tables/{table_uuid}/records/", params={"filters": json.dumps(filters)}
    )
    assert resp.status_code == 422, resp.text

    # And it cannot be stored, so no saved view can hold one either.
    view = await _view_uuid(client, table_uuid)
    saved = await client.patch(
        f"/api/tables/{table_uuid}/views/{view}",
        json={"config": {"filters": filters}},
    )
    assert saved.status_code == 422, saved.text


async def test_number_sort_is_numeric_not_lexicographic(client, table_uuid, add_column):
    score = await add_column(
        table_uuid, name="score", display_name="Score", type="number"
    )
    for s in [9, 10, 100, 2]:
        await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {score: s}}
        )

    view = await _view_uuid(client, table_uuid)
    await client.patch(
        f"/api/tables/{table_uuid}/views/{view}",
        json={"config": {"sorts": [{"column_uuid": score, "direction": "asc"}]}},
    )

    rows = (
        await client.get(f"/api/tables/{table_uuid}/records/?view_uuid={view}")
    ).json()
    # Numeric, not text ordering (which would be 10, 100, 2, 9).
    assert [r["data"][score] for r in rows] == [2, 9, 10, 100]


async def test_no_view_returns_all_in_insertion_order(client, table_uuid, add_column):
    name = await add_column(table_uuid, name="name", display_name="Name", type="text")
    for n in ["a", "b", "c"]:
        await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {name: n}})
    rows = (await client.get(f"/api/tables/{table_uuid}/records/")).json()
    assert [r["data"][name] for r in rows] == ["a", "b", "c"]


async def _pin_utc(client):
    """Put the acting user in UTC, so "today" here and on the server are one day.

    `resolve_filter_token` reads the clock where the *user* is, which is the whole
    point of it -- and which makes a test that seeds records against the machine's
    local calendar fail for eight hours a day on a developer machine east of
    Greenwich. Pinning the account is how the two are made to agree; that the zone
    is honoured at all is what `test_filter_tokens.py` pins, without a clock to race.
    """
    await client.patch("/api/auth/users/me/settings", json={"timezone": "UTC"})


async def test_today_filter_follows_the_clock(client, table_uuid, add_column):
    """A `date` condition written as "today" means the day the query runs.

    The point of the token, and what a stored ISO string cannot do: the same saved
    view answers a different question tomorrow. Records are seeded relative to the
    clock so the test does not go stale either.
    """
    from datetime import datetime, timedelta, timezone

    await _pin_utc(client)
    due = await add_column(table_uuid, name="due", display_name="Due", type="date")
    today = datetime.now(timezone.utc).date()
    for offset in (-1, 0, 1):
        await client.post(
            f"/api/tables/{table_uuid}/records/",
            json={"data": {due: (today + timedelta(days=offset)).isoformat()}},
        )

    view = await _view_uuid(client, table_uuid)
    await client.patch(
        f"/api/tables/{table_uuid}/views/{view}",
        json={
            "config": {
                "filters": {
                    "conjunction": "and",
                    "conditions": [
                        {"column_uuid": due, "operator": ">=", "value": "today"}
                    ],
                }
            }
        },
    )

    rows = (
        await client.get(f"/api/tables/{table_uuid}/records/?view_uuid={view}")
    ).json()
    assert sorted(r["data"][due] for r in rows) == [
        today.isoformat(),
        (today + timedelta(days=1)).isoformat(),
    ]


async def test_now_and_today_on_a_datetime_column(client, table_uuid, add_column):
    """A `datetime` takes both words, and "today" is that day's midnight.

    Which is what makes `>= today` "the rest of the day" rather than a synonym for
    `>= now`: the 00:05 reading is today and is already past.
    """
    from datetime import datetime, timedelta, timezone

    await _pin_utc(client)
    at = await add_column(table_uuid, name="at", display_name="At", type="datetime")
    # Naive, because these columns hold a wall-clock reading rather than an instant
    # -- the same convention `_wall_clock_now` writes and every cell is stored in.
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    midnight = now.replace(hour=0, minute=0, second=0)
    stamps = [
        midnight - timedelta(minutes=5),   # yesterday, just
        midnight + timedelta(minutes=5),   # today, and already past
        now + timedelta(hours=1),          # still to come
    ]
    for stamp in stamps:
        await client.post(
            f"/api/tables/{table_uuid}/records/",
            json={"data": {at: stamp.isoformat()}},
        )

    view = await _view_uuid(client, table_uuid)

    async def matching(value):
        await client.patch(
            f"/api/tables/{table_uuid}/views/{view}",
            json={
                "config": {
                    "filters": {
                        "conjunction": "and",
                        "conditions": [
                            {"column_uuid": at, "operator": ">=", "value": value}
                        ],
                    }
                }
            },
        )
        rows = (
            await client.get(f"/api/tables/{table_uuid}/records/?view_uuid={view}")
        ).json()
        return sorted(r["data"][at] for r in rows)

    # Skipped in the first five minutes of the day, when "just before midnight" and
    # "just after" fall on either side of a date boundary the test did not intend.
    if midnight + timedelta(minutes=5) < now:
        assert await matching("today") == [
            stamps[1].isoformat(),
            stamps[2].isoformat(),
        ]
    assert await matching("now") == [stamps[2].isoformat()]


async def test_a_token_the_column_does_not_know_stays_a_literal(
    client, table_uuid, add_column
):
    """Only the words that column type accepts are resolved; the rest compare as text.

    A `text` cell really holding "today" is the case this protects, and the reason
    the substitution is keyed by column type rather than by the value alone.
    """
    note = await add_column(table_uuid, name="note", display_name="Note", type="text")
    for n in ["today", "tomorrow"]:
        await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {note: n}})

    view = await _view_uuid(client, table_uuid)
    await client.patch(
        f"/api/tables/{table_uuid}/views/{view}",
        json={
            "config": {
                "filters": {
                    "conjunction": "and",
                    "conditions": [
                        {"column_uuid": note, "operator": "is", "value": "today"}
                    ],
                }
            }
        },
    )

    rows = (
        await client.get(f"/api/tables/{table_uuid}/records/?view_uuid={view}")
    ).json()
    assert [r["data"][note] for r in rows] == ["today"]
