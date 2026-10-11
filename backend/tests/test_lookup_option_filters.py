"""A lookup column's saved option filter: which target rows the picker offers.

A lookup offers every target row its caller may read. `option_filters` narrows
that to the ones matching a saved `FilterGroup` — the view's filter language,
borrowed rather than reinvented, asked of the *target table's* columns.

Two properties separate it from the nested pairing next door, and both are
pinned below:

- **It is stored, never sent.** `?parent_value=` is a caller's convenience
  because it can only name a row uuid the caller already holds. A general
  predicate is different in kind: `lookup` hands over a row's label without
  opening the row, so a caller who could choose the predicate could read the
  target's hidden columns by elimination — bisect `salary > n`, or walk
  `contains` a character at a time. The filter therefore comes off the column
  and nothing else, and `test_the_endpoint_takes_no_filter_from_the_caller` is
  the guard.
- **It narrows offering, not resolving.** Asking for specific uuids still
  returns their labels, and an off-filter cell still saves — the same "advisory"
  deal nesting struck (known-gaps §7), so enabling a filter over a column with
  existing data cannot make those records unsaveable.
"""
from contextlib import asynccontextmanager

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.infrastructure import database as db_module


@pytest_asyncio.fixture
def make_client(engine, session_factory):
    @asynccontextmanager
    async def _make(user):
        orig_engine, orig_factory = db_module.engine, db_module.SessionLocal
        db_module.engine, db_module.SessionLocal = engine, session_factory
        app = create_app()
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client:
                yield client
        finally:
            db_module.engine, db_module.SessionLocal = orig_engine, orig_factory

    return _make


async def _user(session_factory, name):
    async with session_factory() as db:
        created = await UserService.create(
            db, UserCreate(username=name, email=f"{name}@example.com", password="pw")
        )
        return created, str(created.uuid)


async def _grant(client, resource_type, resource_uuid, user_uuid, actions):
    resp = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": resource_type,
            "resource_uuid": resource_uuid,
            "user_uuid": user_uuid,
            "actions": actions,
        },
    )
    assert resp.status_code == 201, resp.text


async def _schools(client, workspace_uuid, add_column):
    """A Schools table: name (the label), status, and a roll number.

    Three rows, arranged so that no single condition selects the same pair —
    a filter that quietly matched everything would still fail every assertion.
    """
    target = (
        await client.post(
            "/api/tables/",
            json={
                "name": "schools",
                "display_name": "Schools",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    name_col = await add_column(target, name="name", display_name="Name", type="text")
    await client.patch(f"/api/tables/{target}", json={"primary_column_uuid": name_col})
    status_col = await add_column(
        target,
        name="status",
        display_name="Status",
        type="choice",
        config={"options": ["Active", "Closed"]},
    )
    roll_col = await add_column(target, name="roll", display_name="Roll", type="number")

    rows = {}
    for name, status, roll in [
        ("Springfield High", "Active", 900),
        ("Shelbyville High", "Closed", 400),
        ("Ogdenville Prep", "Active", 150),
    ]:
        created = await client.post(
            f"/api/tables/{target}/records/",
            json={"data": {name_col: name, status_col: status, roll_col: roll}},
        )
        assert created.status_code == 201, created.text
        rows[name] = created.json()["uuid"]
    return {
        "target": target,
        "name_col": name_col,
        "status_col": status_col,
        "roll_col": roll_col,
        "rows": rows,
    }


def _active(status_col):
    return {
        "conjunction": "and",
        "conditions": [
            {"column_uuid": status_col, "operator": "is", "value": "Active"}
        ],
    }


async def _lookup_at(add_column, table_uuid, target, **config):
    return await add_column(
        table_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_table_uuid": target, **config},
    )


async def _labels(client, table_uuid, column, query=""):
    resp = await client.get(
        f"/api/tables/{table_uuid}/columns/{column}/options{query}"
    )
    assert resp.status_code == 200, resp.text
    return sorted(o["label"] for o in resp.json())


# ---- what the picker offers ----


async def test_a_filter_narrows_the_suggestions(
    client, workspace_uuid, table_uuid, add_column
):
    s = await _schools(client, workspace_uuid, add_column)
    column = await _lookup_at(
        add_column, table_uuid, s["target"], option_filters=_active(s["status_col"])
    )

    assert await _labels(client, table_uuid, column) == [
        "Ogdenville Prep",
        "Springfield High",
    ]


async def test_a_filter_narrows_a_search_too(
    client, workspace_uuid, table_uuid, add_column
):
    """`q` and the filter compose: both are `where` clauses on one statement, so
    a search inside the filter is an intersection, not a way around it."""
    s = await _schools(client, workspace_uuid, add_column)
    column = await _lookup_at(
        add_column, table_uuid, s["target"], option_filters=_active(s["status_col"])
    )

    # "high" alone matches Springfield and Shelbyville; only one is Active.
    assert await _labels(client, table_uuid, column, "?q=high") == ["Springfield High"]


async def test_a_numeric_condition_compares_as_a_number(
    client, workspace_uuid, table_uuid, add_column
):
    """`apply_filters` carries its type rules over unchanged — text ordering
    would put "900" below "150" and quietly return the wrong schools."""
    s = await _schools(client, workspace_uuid, add_column)
    column = await _lookup_at(
        add_column,
        table_uuid,
        s["target"],
        option_filters={
            "conjunction": "and",
            "conditions": [
                {"column_uuid": s["roll_col"], "operator": ">", "value": 200}
            ],
        },
    )

    assert await _labels(client, table_uuid, column) == [
        "Shelbyville High",
        "Springfield High",
    ]


async def test_or_joins_conditions(client, workspace_uuid, table_uuid, add_column):
    s = await _schools(client, workspace_uuid, add_column)
    column = await _lookup_at(
        add_column,
        table_uuid,
        s["target"],
        option_filters={
            "conjunction": "or",
            "conditions": [
                {"column_uuid": s["status_col"], "operator": "is", "value": "Closed"},
                {"column_uuid": s["roll_col"], "operator": "<", "value": 200},
            ],
        },
    )

    assert await _labels(client, table_uuid, column) == [
        "Ogdenville Prep",
        "Shelbyville High",
    ]


async def test_no_filter_offers_everything(
    client, workspace_uuid, table_uuid, add_column
):
    """The regression guard for the feature's own absence: a lookup without the
    key must behave exactly as it did before the key existed."""
    s = await _schools(client, workspace_uuid, add_column)
    column = await _lookup_at(add_column, table_uuid, s["target"])

    assert len(await _labels(client, table_uuid, column)) == 3


# ---- resolving is not offering ----


async def test_resolving_uuids_ignores_the_filter(
    client, workspace_uuid, table_uuid, add_column
):
    """A cell pointing at a now-excluded row must still render its name. The
    alternative is a live relationship displaying as a uuid stub the moment
    somebody tightens the filter."""
    s = await _schools(client, workspace_uuid, add_column)
    column = await _lookup_at(
        add_column, table_uuid, s["target"], option_filters=_active(s["status_col"])
    )

    closed = s["rows"]["Shelbyville High"]
    assert await _labels(client, table_uuid, column, f"?uuid={closed}") == [
        "Shelbyville High"
    ]


async def test_an_off_filter_value_still_saves(
    client, workspace_uuid, table_uuid, add_column
):
    """Advisory, like the nesting pair: the filter shortens the picker, it does
    not invalidate data. Turning one on over a populated column would otherwise
    make every mismatched record unsaveable."""
    s = await _schools(client, workspace_uuid, add_column)
    column = await _lookup_at(
        add_column, table_uuid, s["target"], option_filters=_active(s["status_col"])
    )

    closed = s["rows"]["Shelbyville High"]
    resp = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {column: closed}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][column] == closed


# ---- composition with the nested pairing ----


async def test_a_filter_composes_with_nesting(
    client, workspace_uuid, table_uuid, add_column
):
    """Both narrowings apply. They are separate `where` clauses, so the result
    is the intersection — not whichever one was configured last."""
    countries = (
        await client.post(
            "/api/tables/",
            json={
                "name": "countries",
                "display_name": "Countries",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    country_name = await add_column(
        countries, name="name", display_name="Name", type="text"
    )
    await client.patch(
        f"/api/tables/{countries}", json={"primary_column_uuid": country_name}
    )
    japan = (
        await client.post(
            f"/api/tables/{countries}/records/", json={"data": {country_name: "Japan"}}
        )
    ).json()["uuid"]

    cities = (
        await client.post(
            "/api/tables/",
            json={
                "name": "cities",
                "display_name": "Cities",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    city_name = await add_column(cities, name="name", display_name="Name", type="text")
    await client.patch(f"/api/tables/{cities}", json={"primary_column_uuid": city_name})
    city_country = await add_column(
        cities,
        name="country",
        display_name="Country",
        type="lookup",
        config={"target_table_uuid": countries},
    )
    city_size = await add_column(
        cities,
        name="size",
        display_name="Size",
        type="choice",
        config={"options": ["Big", "Small"]},
    )
    for name, size in [("Tokyo", "Big"), ("Osaka", "Small")]:
        await client.post(
            f"/api/tables/{cities}/records/",
            json={"data": {city_name: name, city_country: japan, city_size: size}},
        )
    # A big city in a country the parent will not be pointing at.
    await client.post(
        f"/api/tables/{cities}/records/",
        json={"data": {city_name: "Paris", city_size: "Big"}},
    )

    country_col = await add_column(
        table_uuid,
        name="country",
        display_name="Country",
        type="lookup",
        config={"target_table_uuid": countries},
    )
    city_col = await add_column(
        table_uuid,
        name="city",
        display_name="City",
        type="lookup",
        config={
            "target_table_uuid": cities,
            "parent_column_uuid": country_col,
            "target_filter_column_uuid": city_country,
            "option_filters": {
                "conjunction": "and",
                "conditions": [
                    {"column_uuid": city_size, "operator": "is", "value": "Big"}
                ],
            },
        },
    )

    # Japan narrows to Tokyo + Osaka; "Big" narrows to Tokyo + Paris.
    assert await _labels(
        client, table_uuid, city_col, f"?parent_value={japan}"
    ) == ["Tokyo"]
    # Without a parent the endpoint stays unnested (known-gaps §7) — but the
    # stored filter is not a caller's to omit, so it still applies.
    assert await _labels(client, table_uuid, city_col) == ["Paris", "Tokyo"]


# ---- the filter is stored, not sent ----


async def test_the_endpoint_takes_no_filter_from_the_caller(
    client, workspace_uuid, table_uuid, add_column
):
    """The security property, pinned directly.

    `lookup` on a table means "you may read a row's label", not the row. A filter
    the *caller* chose would defeat that by elimination — which rows survive
    `roll > 500` is the roll. So the options endpoint must accept no predicate
    from the request under any spelling; if one is ever added, this fails.
    """
    s = await _schools(client, workspace_uuid, add_column)
    column = await _lookup_at(add_column, table_uuid, s["target"])

    baseline = await _labels(client, table_uuid, column)
    assert len(baseline) == 3

    smuggled = (
        f"?option_filters=%7B%22conditions%22%3A%5B%7B%22column_uuid%22%3A%22"
        f"{s['roll_col']}%22%2C%22operator%22%3A%22%3E%22%2C%22value%22%3A500%7D%5D%7D"
        f"&filters=%7B%22conditions%22%3A%5B%5D%7D"
    )
    assert await _labels(client, table_uuid, column, smuggled) == baseline


# ---- config-time validation ----


async def test_a_condition_on_a_foreign_column_is_refused(
    client, workspace_uuid, table_uuid, add_column
):
    """`apply_filters` drops a condition whose column it cannot place, so an
    unvalidated typo would produce a filter that reads as set and offers
    everything."""
    s = await _schools(client, workspace_uuid, add_column)
    mine = await add_column(table_uuid, name="note", display_name="Note", type="text")

    resp = await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={
            "name": "school",
            "display_name": "School",
            "type": "lookup",
            "config": {
                "target_table_uuid": s["target"],
                # A real column — of the wrong table.
                "option_filters": {
                    "conjunction": "and",
                    "conditions": [
                        {"column_uuid": mine, "operator": "is", "value": "x"}
                    ],
                },
            },
        },
    )
    # 400, the same as the `display_column_uuid` check beside it — an unknown
    # reference in a config is a bad config, not a missing endpoint.
    assert resp.status_code == 400, resp.text
    assert "option_filters" in resp.json()["detail"]


async def test_a_deleted_target_column_degrades_rather_than_erroring(
    client, workspace_uuid, table_uuid, add_column
):
    """Validation runs when the filter is written; a column can go afterwards.
    The picker then offers more rather than failing — the same way a dangling
    nesting pair falls back to a plain lookup."""
    s = await _schools(client, workspace_uuid, add_column)
    column = await _lookup_at(
        add_column, table_uuid, s["target"], option_filters=_active(s["status_col"])
    )
    assert len(await _labels(client, table_uuid, column)) == 2

    deleted = await client.delete(
        f"/api/tables/{s['target']}/columns/{s['status_col']}"
    )
    assert deleted.status_code in (200, 204), deleted.text

    assert len(await _labels(client, table_uuid, column)) == 3


async def test_clearing_the_filter_restores_every_option(
    client, workspace_uuid, table_uuid, add_column
):
    """A config PATCH merges, so clearing takes an explicit null."""
    s = await _schools(client, workspace_uuid, add_column)
    column = await _lookup_at(
        add_column, table_uuid, s["target"], option_filters=_active(s["status_col"])
    )
    assert len(await _labels(client, table_uuid, column)) == 2

    patched = await client.patch(
        f"/api/tables/{table_uuid}/columns/{column}",
        json={"config": {"option_filters": None}},
    )
    assert patched.status_code == 200, patched.text
    assert len(await _labels(client, table_uuid, column)) == 3


# ---- permissions ----


async def test_a_filter_is_accepted_over_a_target_whose_schema_is_hidden(
    client, make_client, session_factory, workspace_uuid, table_uuid, add_column
):
    """A lookup-only grantee cannot read the target's columns by design, so the
    validation above must not run for them: an error distinguishing "not a
    column of the target" from "accepted" would answer, one uuid at a time, the
    question that refusal exists to avoid. They get the silent drop instead.
    """
    s = await _schools(client, workspace_uuid, add_column)
    user, user_uuid = await _user(session_factory, "configurer")
    await _grant(client, "table", table_uuid, user_uuid, ["view", "configure"])
    await _grant(client, "table", s["target"], user_uuid, ["lookup"])

    async with make_client(user) as grantee:
        # The premise: the target's schema really is refused.
        assert (
            await grantee.get(f"/api/tables/{s['target']}/columns/")
        ).status_code == 403

        resp = await grantee.post(
            f"/api/tables/{table_uuid}/columns/",
            json={
                "name": "school",
                "display_name": "School",
                "type": "lookup",
                "config": {
                    "target_table_uuid": s["target"],
                    "option_filters": {
                        "conjunction": "and",
                        "conditions": [
                            {
                                "column_uuid": "11111111-1111-1111-1111-111111111111",
                                "operator": "is",
                                "value": "Active",
                            }
                        ],
                    },
                },
            },
        )
        assert resp.status_code == 201, resp.text
        column = resp.json()["uuid"]
        # The unplaceable condition is dropped, so the picker still works.
        assert len(await _labels(grantee, table_uuid, column)) == 3


async def test_a_filter_cannot_reach_past_the_permission_gate(
    client, make_client, session_factory, workspace_uuid, table_uuid, add_column
):
    """The filter is one more `where` on a statement `record_view_filter` already
    constrains, so it composes by intersection and can only remove rows. A
    grantee with nothing on the target sees nothing, filter or no filter."""
    s = await _schools(client, workspace_uuid, add_column)
    column = await _lookup_at(
        add_column, table_uuid, s["target"], option_filters=_active(s["status_col"])
    )
    user, user_uuid = await _user(session_factory, "outsider")
    await _grant(client, "table", table_uuid, user_uuid, ["view"])

    async with make_client(user) as outsider:
        resp = await outsider.get(
            f"/api/tables/{table_uuid}/columns/{column}/options"
        )
        assert resp.status_code == 403, resp.text
