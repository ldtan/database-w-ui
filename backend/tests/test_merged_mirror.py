"""Mirrors: copies of a linked record kept beside the record that links to it.

A merged form borrows columns from another list, and those values live over there —
where Postgres, which is what filters and sorts, cannot see them. So each borrowed
value is also written into the referring record under a key of its own.

**The stored copy is a query index, not the answer**, exactly as a `calculated` cell is
(known-gaps §5). Every read recomputes it and overwrites what is stored, so a stale
copy can misplace a row in a sorted view and can never put a wrong value on a screen or
in an export. `test_a_hand_broken_mirror_never_reaches_a_reader` is that property
stated as a test, and it is the one to keep if any of these have to go.
"""
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.infrastructure import database as db_module
from src.lists.items.models import ListItem
from src.lists.models import List as ListModel
from src.lists.items.mirror import mirror_key


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


async def _grant(client, resource_uuid, user_uuid, actions):
    resp = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "list",
            "resource_uuid": resource_uuid,
            "user_uuid": user_uuid,
            "actions": actions,
        },
    )
    assert resp.status_code == 201, resp.text


async def _pair(client, workspace_uuid, add_column, list_uuid, *, allow_shared=True):
    """People(name, age) <- Enrolments(status, person), merged on both forms."""
    people = (
        await client.post(
            "/api/lists/",
            json={
                "name": "people",
                "display_name": "People",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    name = await add_column(people, name="full_name", display_name="Name", type="text")
    age = await add_column(people, name="age", display_name="Age", type="number")
    await client.patch(f"/api/lists/{people}", json={"primary_column_uuid": name})

    status = await add_column(
        list_uuid, name="status", display_name="Status", type="text"
    )
    link = await add_column(
        list_uuid,
        name="person",
        display_name="Person",
        type="lookup",
        config={"target_list_uuid": people},
    )
    made = await client.post(
        f"/api/lists/{list_uuid}/forms/",
        json={
            "name": "merged",
            "display_name": "Merged",
            "type": "create",
            "config": {
                "fields": [],
                "merge": {
                    "link_column_uuid": link,
                    "fields": [
                        {"column_uuid": name, "included": True, "enabled": True, "order": 0},
                        {"column_uuid": age, "included": True, "enabled": True, "order": 1},
                    ],
                    "allow_shared": allow_shared,
                },
            },
        },
    )
    assert made.status_code == 201, made.text
    return {
        "people": people,
        "name": name,
        "age": age,
        "status": status,
        "link": link,
        "form": made.json()["uuid"],
        "k_name": mirror_key(link, name),
        "k_age": mirror_key(link, age),
    }


@pytest.fixture
def pair(client, workspace_uuid, list_uuid, add_column):
    async def _build(**kw):
        return await _pair(client, workspace_uuid, add_column, list_uuid, **kw)

    return _build


async def _stored(session_factory, uuid):
    """The record's `data` as it actually sits in the table — no read-time shaping."""
    async with session_factory() as db:
        return await db.scalar(select(ListItem.data).where(ListItem.uuid == uuid))


async def test_a_merged_create_writes_the_mirror(
    client, list_uuid, pair, session_factory
):
    s = await pair()
    made = (
        await client.post(
            f"/api/lists/{list_uuid}/items/merged",
            json={
                "form_uuid": s["form"],
                "data": {s["status"]: "active"},
                "linked_data": {s["name"]: "Ana", s["age"]: 19},
            },
        )
    ).json()

    stored = await _stored(session_factory, made["item"]["uuid"])
    assert stored[s["k_name"]] == "Ana"
    assert stored[s["k_age"]] == 19


async def test_setting_the_link_on_an_ordinary_write_mirrors_too(
    client, list_uuid, pair, session_factory
):
    """The mirror is not a property of the merged route. Any write that moves the link
    cell has to bring the copy with it, or an inline edit would leave the two
    disagreeing."""
    s = await pair()
    person = (
        await client.post(
            f"/api/lists/{s['people']}/items/",
            json={"data": {s["name"]: "Ben", s["age"]: 22}},
        )
    ).json()["uuid"]

    row = (
        await client.post(
            f"/api/lists/{list_uuid}/items/",
            json={"data": {s["status"]: "active", s["link"]: person}},
        )
    ).json()["uuid"]

    stored = await _stored(session_factory, row)
    assert stored[s["k_name"]] == "Ben"
    assert stored[s["k_age"]] == 22


async def test_editing_the_linked_record_fans_out_to_every_referring_row(
    client, list_uuid, pair, session_factory
):
    """The half a calculated column never needs: this record changed in *another* list,
    on a write that knows nothing about the rows watching it."""
    s = await pair()
    person = (
        await client.post(
            f"/api/lists/{s['people']}/items/",
            json={"data": {s["name"]: "Ana", s["age"]: 19}},
        )
    ).json()["uuid"]
    rows = [
        (
            await client.post(
                f"/api/lists/{list_uuid}/items/",
                json={"data": {s["status"]: f"row{i}", s["link"]: person}},
            )
        ).json()["uuid"]
        for i in range(3)
    ]

    patched = await client.patch(
        f"/api/lists/{s['people']}/items/{person}", json={"data": {s["age"]: 20}}
    )
    assert patched.status_code == 200, patched.text

    for row in rows:
        stored = await _stored(session_factory, row)
        assert stored[s["k_age"]] == 20, "every referring row follows, not just one"
        assert stored[s["k_name"]] == "Ana"


async def test_a_row_with_no_link_holds_blank_mirrors(
    client, list_uuid, pair, session_factory
):
    """Written as null rather than left absent: "not linked" and "not yet mirrored"
    must not be the same state to a filter."""
    s = await pair()
    row = (
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {s["status"]: "active"}}
        )
    ).json()["uuid"]

    stored = await _stored(session_factory, row)
    assert stored[s["k_name"]] is None
    assert stored[s["k_age"]] is None


async def test_clearing_the_link_blanks_the_mirror(
    client, list_uuid, pair, session_factory
):
    s = await pair()
    person = (
        await client.post(
            f"/api/lists/{s['people']}/items/", json={"data": {s["name"]: "Ana"}}
        )
    ).json()["uuid"]
    row = (
        await client.post(
            f"/api/lists/{list_uuid}/items/",
            json={"data": {s["status"]: "active", s["link"]: person}},
        )
    ).json()["uuid"]
    assert (await _stored(session_factory, row))[s["k_name"]] == "Ana"

    await client.patch(
        f"/api/lists/{list_uuid}/items/{row}", json={"data": {s["link"]: None}}
    )
    assert (await _stored(session_factory, row))[s["k_name"]] is None


async def test_a_client_cannot_write_a_mirror_key(client, list_uuid, pair):
    """One writer, like a calculated cell. Refused with a message about what the key
    *is*, since it appears in this list's own payloads and a client echoing a record
    back would otherwise be told its own data is invalid."""
    s = await pair()
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {s["status"]: "active", s["k_name"]: "Forged"}},
    )
    assert resp.status_code == 400, resp.text
    assert "copy of a linked record" in resp.text


async def test_a_hand_broken_mirror_never_reaches_a_reader(
    client, list_uuid, pair, session_factory
):
    """**The safety property, and the reason the stored copy is only an index.**

    Corrupt the stored mirror behind the app's back — which is what a missed
    invalidation looks like — and every read still answers correctly, because
    `shape_values` recomputes and overwrites before serialising.
    """
    s = await pair()
    person = (
        await client.post(
            f"/api/lists/{s['people']}/items/",
            json={"data": {s["name"]: "Ana", s["age"]: 19}},
        )
    ).json()["uuid"]
    row = (
        await client.post(
            f"/api/lists/{list_uuid}/items/",
            json={"data": {s["status"]: "active", s["link"]: person}},
        )
    ).json()["uuid"]

    async with session_factory() as db:
        await db.execute(
            update(ListItem)
            .where(ListItem.uuid == row)
            .values(data={s["status"]: "active", s["link"]: person,
                          s["k_name"]: "WRONG", s["k_age"]: -1})
        )
        await db.commit()
    assert (await _stored(session_factory, row))[s["k_name"]] == "WRONG"

    single = (await client.get(f"/api/lists/{list_uuid}/items/{row}")).json()
    listed = (await client.get(f"/api/lists/{list_uuid}/items/")).json()[0]
    for payload, where in ((single, "the record route"), (listed, "the listing")):
        assert payload["data"][s["k_name"]] == "Ana", where
        assert payload["data"][s["k_age"]] == 19, where


async def test_a_list_with_no_merged_form_stores_no_mirrors(
    client, list_uuid, workspace_uuid, add_column, session_factory
):
    """The guard that keeps this off every other list's write path."""
    people = (
        await client.post(
            "/api/lists/",
            json={"name": "p2", "display_name": "P2", "workspace_uuid": workspace_uuid},
        )
    ).json()["uuid"]
    name = await add_column(people, name="n", display_name="N", type="text")
    await client.patch(f"/api/lists/{people}", json={"primary_column_uuid": name})
    link = await add_column(
        list_uuid,
        name="person",
        display_name="Person",
        type="lookup",
        config={"target_list_uuid": people},
    )
    person = (
        await client.post(f"/api/lists/{people}/items/", json={"data": {name: "Ana"}})
    ).json()["uuid"]

    row = (
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {link: person}}
        )
    ).json()["uuid"]

    stored = await _stored(session_factory, row)
    # Audit columns are in here too, so the assertion is about the *shape* of the
    # keys: a mirror key is the only kind that is not a bare column uuid.
    assert [k for k in stored if ":" in k] == []


# --- filtering and sorting by a borrowed value -------------------------------
#
# The point of storing the copy at all. These run through the ordinary items
# endpoint, so what they prove is that `filtering.py` needed no changes: a mirror
# key reaches it as a stand-in whose uuid *is* the key.


async def _three(client, list_uuid, s):
    """Three rows linked to three people of different ages."""
    made = []
    for who, age in (("Cara", 31), ("Ana", 19), ("Ben", 25)):
        person = (
            await client.post(
                f"/api/lists/{s['people']}/items/",
                json={"data": {s["name"]: who, s["age"]: age}},
            )
        ).json()["uuid"]
        row = (
            await client.post(
                f"/api/lists/{list_uuid}/items/",
                json={"data": {s["status"]: who.lower(), s["link"]: person}},
            )
        ).json()["uuid"]
        made.append((who, age, row))
    return made


async def test_sorting_by_a_borrowed_number_is_numeric(client, list_uuid, pair):
    """Numeric, not lexical — the borrowed column's own type decides, because the
    stand-in carries it. Text ordering would put 19 after romanised 25/31."""
    s = await pair()
    await _three(client, list_uuid, s)

    rows = (
        await client.get(
            f"/api/lists/{list_uuid}/items/?sort_by={s['k_age']}&sort_dir=asc"
        )
    ).json()
    assert [r["data"][s["k_age"]] for r in rows] == [19, 25, 31]

    rows = (
        await client.get(
            f"/api/lists/{list_uuid}/items/?sort_by={s['k_age']}&sort_dir=desc"
        )
    ).json()
    assert [r["data"][s["k_age"]] for r in rows] == [31, 25, 19]


async def test_sorting_by_a_borrowed_text_is_alphabetical(client, list_uuid, pair):
    s = await pair()
    await _three(client, list_uuid, s)

    rows = (
        await client.get(f"/api/lists/{list_uuid}/items/?sort_by={s['k_name']}")
    ).json()
    assert [r["data"][s["k_name"]] for r in rows] == ["Ana", "Ben", "Cara"]


def _filter_param(key, operator, value):
    """`?filters=` takes URL-encoded JSON, so it has to be quoted like one."""
    import json as _json
    from urllib.parse import quote

    return quote(
        _json.dumps(
            {
                "conjunction": "and",
                "conditions": [
                    {"column_uuid": key, "operator": operator, "value": value}
                ],
            }
        )
    )


async def test_filtering_by_a_borrowed_value(client, list_uuid, pair):
    s = await pair()
    await _three(client, list_uuid, s)

    resp = await client.get(
        f"/api/lists/{list_uuid}/items/?filters={_filter_param(s['k_age'], '>', 20)}"
    )
    assert resp.status_code == 200, resp.text
    assert sorted(r["data"][s["k_name"]] for r in resp.json()) == ["Ben", "Cara"]


async def test_a_search_does_not_match_a_borrowed_value(client, list_uuid, pair):
    """Documented rather than fixed. `_apply_search` iterates live columns, so mirror
    keys are outside it — searching a linked *label* already works through
    `_reference_matches`, which asks the target's permissions per caller. Widening
    search to the raw copies would route around that check."""
    s = await pair()
    await _three(client, list_uuid, s)

    rows = (await client.get(f"/api/lists/{list_uuid}/items/?q=Cara")).json()
    assert [r["data"][s["status"]] for r in rows] == ["cara"], (
        "matched by the lookup label, not by the mirrored copy"
    )


# --- the permission gate -----------------------------------------------------


async def test_a_reader_without_access_to_the_linked_list_sees_blank_mirrors(
    client, make_client, session_factory, list_uuid, pair
):
    """The copy sits in *this* list's rows, so nothing stops it being served to
    someone who may read this list. The read path asks the linked list per caller,
    and an unreadable record mirrors as blank — the degradation `LookupCell` already
    makes on a 403, rather than a refusal that would make the row unreadable."""
    s = await pair()
    await _three(client, list_uuid, s)

    other, other_uuid = await _user(session_factory, "outsider")
    await _grant(client, list_uuid, other_uuid, ["view"])
    # ...and deliberately nothing on the linked list.

    async with make_client(other) as outsider:
        rows = (await outsider.get(f"/api/lists/{list_uuid}/items/")).json()
        assert len(rows) == 3, "the rows themselves are still theirs to read"
        assert all(r["data"][s["k_name"]] is None for r in rows)
        assert all(r["data"][s["k_age"]] is None for r in rows)


async def test_lookup_on_the_linked_list_is_not_enough_to_see_mirrors(
    client, make_client, session_factory, list_uuid, pair
):
    """`lookup` discloses a display column so a picker can work. A mirror is arbitrary
    columns, so it needs `view` — the same line the merged form is configured under."""
    s = await pair()
    await _three(client, list_uuid, s)

    other, other_uuid = await _user(session_factory, "peeker")
    await _grant(client, list_uuid, other_uuid, ["view"])
    await _grant(client, s["people"], other_uuid, ["lookup"])

    async with make_client(other) as peeker:
        rows = (await peeker.get(f"/api/lists/{list_uuid}/items/")).json()
        assert all(r["data"][s["k_name"]] is None for r in rows)


async def test_filtering_by_a_mirror_is_refused_without_access(
    client, make_client, session_factory, list_uuid, pair
):
    """**The disclosure this gate exists for.** Blanking the *displayed* value is not
    enough: a filter is answered by Postgres against the stored copy, so without this
    someone could binary-search another list's contents through this one's filters.

    Refused rather than ignored — a filter silently not applied returns rows the
    caller believes were excluded, and they cannot tell."""
    s = await pair()
    await _three(client, list_uuid, s)

    other, other_uuid = await _user(session_factory, "prober")
    await _grant(client, list_uuid, other_uuid, ["view"])

    async with make_client(other) as prober:
        resp = await prober.get(
            f"/api/lists/{list_uuid}/items/?filters={_filter_param(s['k_age'], '>', 20)}"
        )
        assert resp.status_code == 400, resp.text
        assert "do not have access" in resp.text

        sorted_resp = await prober.get(
            f"/api/lists/{list_uuid}/items/?sort_by={s['k_age']}"
        )
        assert sorted_resp.status_code == 400, sorted_resp.text


async def test_view_on_the_linked_list_is_enough(
    client, make_client, session_factory, list_uuid, pair
):
    s = await pair()
    await _three(client, list_uuid, s)

    other, other_uuid = await _user(session_factory, "reader")
    await _grant(client, list_uuid, other_uuid, ["view"])
    await _grant(client, s["people"], other_uuid, ["view"])

    async with make_client(other) as reader:
        rows = (await reader.get(f"/api/lists/{list_uuid}/items/")).json()
        assert sorted(r["data"][s["k_name"]] for r in rows) == ["Ana", "Ben", "Cara"]
        resp = await reader.get(
            f"/api/lists/{list_uuid}/items/?filters={_filter_param(s['k_age'], '>', 20)}"
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()) == 2


# --- clone and the nightly backstop ------------------------------------------


async def test_a_clone_drops_mirror_keys_rather_than_carrying_them(
    client, list_uuid, pair, session_factory
):
    """A mirror key names this list's link column, which the clone re-created under a
    new uuid — so a carried-across key names a link that no longer exists. Dropped and
    rebuilt rather than remapped, which would need the key format understood in SQL
    too."""
    s = await pair()
    await _three(client, list_uuid, s)

    resp = await client.post(f"/api/lists/{list_uuid}/clone", json={"with_records": True})
    assert resp.status_code == 201, resp.text
    clone = resp.json()["uuid"]

    rows = (await client.get(f"/api/lists/{clone}/items/")).json()
    assert len(rows) == 3
    async with session_factory() as db:
        clone_id = await db.scalar(select(ListModel.id).where(ListModel.uuid == clone))
        stored = (
            await db.execute(
                select(ListItem.data).where(ListItem.list_id == clone_id)
            )
        ).scalars().all()
    assert all(":" not in k for d in stored for k in (d or {})), (
        "no orphaned mirror keys survive the clone"
    )


async def test_the_nightly_sweep_repairs_a_broken_mirror(
    client, list_uuid, pair, session_factory
):
    """The backstop for a fan-out that never finished — a redeploy mid-transaction, or
    a write that went around the service layer."""
    from src.maintenance import reconcile_mirrors
    from src.infrastructure import database as db_module

    s = await pair()
    made = await _three(client, list_uuid, s)
    row = made[0][2]

    async with session_factory() as db:
        data = await db.scalar(select(ListItem.data).where(ListItem.uuid == row))
        await db.execute(
            update(ListItem)
            .where(ListItem.uuid == row)
            .values(data={**data, s["k_name"]: "STALE", s["k_age"]: -1})
        )
        await db.commit()

    orig = db_module.SessionLocal
    db_module.SessionLocal = session_factory
    try:
        dry = await reconcile_mirrors.reconcile(dry_run=True)
        assert sum(dry.values()) == 1, "exactly the one broken row is reported"
        # ...and a dry run changes nothing.
        assert (await _stored(session_factory, row))[s["k_name"]] == "STALE"

        changed = await reconcile_mirrors.reconcile()
        assert sum(changed.values()) == 1
        # Idempotent: a second pass finds nothing left to do.
        assert await reconcile_mirrors.reconcile() == {}
    finally:
        db_module.SessionLocal = orig

    stored = await _stored(session_factory, row)
    assert stored[s["k_name"]] == "Cara"
    assert stored[s["k_age"]] == 31
