"""Purging soft-deleted views/forms/metrics after the retention window.

The job that finally reclaims these rows. Before it existed a view deleted on its own
was kept forever, so the interesting assertions here are as much about what it leaves
alone as what it destroys — above all the children of a deleted list, which belong to
`purge_deleted_lists` and must survive this job while their list is still restorable.
"""
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import func, select, update

from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.lists.columns.models import Column
from src.lists.forms.models import Form
from src.lists.items.models import ListItem
from src.lists.metrics.models import Metric
from src.lists.models import List
from src.lists.views.models import View
from src.maintenance.purge_deleted_list_config import purge
from src.permissions.models import PermissionGrant

PAYLOADS = {
    "views": {"name": "by_owner", "display_name": "By owner", "config": {}},
    "forms": {
        "name": "intake",
        "display_name": "Intake",
        "type": "create",
        "config": {},
    },
    "metrics": {
        "name": "row_count",
        "display_name": "Row count",
        "config": {"formula": "COUNT()"},
    },
    "columns": {
        "name": "notes",
        "display_name": "Notes",
        "type": "text",
        "config": {},
    },
}
MODELS = {"views": View, "forms": Form, "metrics": Metric, "columns": Column}


@pytest_asyncio.fixture
async def alice(session_factory):
    async with session_factory() as db:
        u = await UserService.create(
            db, UserCreate(username="alice", email="a@example.com", password="pw")
        )
        return str(u.uuid)


async def _create(client, list_uuid, kind) -> str:
    resp = await client.post(
        f"/api/lists/{list_uuid}/{kind}/", json=dict(PAYLOADS[kind])
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def _age(session_factory, model, uuid: str, days: int) -> None:
    when = datetime.now(timezone.utc) - timedelta(days=days)
    async with session_factory() as db:
        await db.execute(
            update(model).where(model.uuid == uuid).values(deleted_at=when)
        )
        await db.commit()


async def _count(session_factory, model) -> int:
    async with session_factory() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def test_only_rows_past_the_window_are_destroyed(
    client, session_factory, list_uuid
):
    fresh, old = {}, {}
    for kind in PAYLOADS:
        fresh[kind] = await _create(client, list_uuid, kind)
        assert (
            await client.delete(f"/api/lists/{list_uuid}/{kind}/{fresh[kind]}")
        ).status_code == 204
        # A second row of the same kind, backdated past the window. The name is free
        # again once the first is binned, which is what lets this be created at all.
        old[kind] = await _create(client, list_uuid, kind)
        assert (
            await client.delete(f"/api/lists/{list_uuid}/{kind}/{old[kind]}")
        ).status_code == 204
        await _age(session_factory, MODELS[kind], old[kind], days=45)

    planned = await purge(days=30, dry_run=True)
    assert planned == 4  # one of each kind

    assert await purge(days=30) == 4

    # The freshly deleted ones are still in their bins, restorable.
    for kind in PAYLOADS:
        binned = await client.get(f"/api/lists/{list_uuid}/{kind}/deleted")
        assert [row["uuid"] for row in binned.json()] == [fresh[kind]], kind


async def test_live_rows_are_untouched(client, session_factory, list_uuid):
    """The predicate is `deleted_at IS NOT NULL`, so a live view is never a
    candidate however old it is."""
    for kind in PAYLOADS:
        await _create(client, list_uuid, kind)

    before = {k: await _count(session_factory, m) for k, m in MODELS.items()}
    assert await purge(days=0) == 0
    after = {k: await _count(session_factory, m) for k, m in MODELS.items()}
    assert after == before


async def test_children_of_a_deleted_list_are_left_to_the_list_purge(
    client, session_factory, list_uuid
):
    """A list delete stamps every child with the list's own `deleted_at`. Those rows
    are `purge_deleted_lists`' to destroy, and this job leaves them even when they are
    long past the window — which is what keeps the two jobs from answering "is this
    list still whole?" differently depending on which ran first."""
    for kind in PAYLOADS:
        await _create(client, list_uuid, kind)
    assert (await client.delete(f"/api/lists/{list_uuid}")).status_code == 204

    # Backdate the list *and* everything under it, as a real 45-day-old delete looks.
    when = datetime.now(timezone.utc) - timedelta(days=45)
    async with session_factory() as db:
        lst = (
            await db.execute(select(List).where(List.uuid == list_uuid))
        ).scalars().first()
        lst.deleted_at = when
        for model in (Column, ListItem, View, Form, Metric):
            await db.execute(
                update(model).where(model.list_id == lst.id).values(deleted_at=when)
            )
        await db.commit()

    assert await purge(days=30, dry_run=True) == 0
    assert await purge(days=30) == 0
    for kind, model in MODELS.items():
        assert await _count(session_factory, model) >= 1, kind


async def test_a_restorable_list_keeps_its_config_through_an_aggressive_run(
    client, session_factory, list_uuid
):
    """The hazard the `JOIN lists` exists for. This job takes a `--days` of its own,
    so nothing stops it being run at 0 while a list sits freshly deleted and fully
    restorable — and it must still come back in one piece."""
    for kind in PAYLOADS:
        await _create(client, list_uuid, kind)
    assert (await client.delete(f"/api/lists/{list_uuid}")).status_code == 204

    assert await purge(days=0) == 0

    restored = await client.post(f"/api/lists/{list_uuid}/restore")
    assert restored.status_code == 200, restored.text
    for kind in PAYLOADS:
        live = await client.get(f"/api/lists/{list_uuid}/{kind}/")
        assert any(
            row["name"] == PAYLOADS[kind]["name"] for row in live.json()
        ), kind


async def test_grants_go_with_the_views_and_forms_it_destroys(
    client, session_factory, list_uuid, alice
):
    """`permission_grants` has no foreign key to its resource, so hard-deleting the
    row is exactly what would orphan its grants. Metrics are never a grant target and
    so have none to clear."""
    for kind, resource_type in (("views", "view"), ("forms", "form")):
        uuid = await _create(client, list_uuid, kind)
        granted = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": resource_type,
                "resource_uuid": uuid,
                "user_uuid": alice,
                "actions": ["view"],
            },
        )
        assert granted.status_code == 201, granted.text
        assert (
            await client.delete(f"/api/lists/{list_uuid}/{kind}/{uuid}")
        ).status_code == 204
        await _age(session_factory, MODELS[kind], uuid, days=45)

    async with session_factory() as db:
        before = await db.scalar(select(func.count()).select_from(PermissionGrant))
    assert before == 2

    assert await purge(days=30) == 2

    async with session_factory() as db:
        after = await db.scalar(select(func.count()).select_from(PermissionGrant))
    assert after == 0


async def test_a_zero_day_window_purges_everything_deleted(
    client, session_factory, list_uuid
):
    for kind in PAYLOADS:
        uuid = await _create(client, list_uuid, kind)
        assert (
            await client.delete(f"/api/lists/{list_uuid}/{kind}/{uuid}")
        ).status_code == 204

    assert await purge(days=0) == 4
    for kind in PAYLOADS:
        assert (await client.get(f"/api/lists/{list_uuid}/{kind}/deleted")).json() == []


async def test_purging_a_column_strips_its_values_from_every_record(
    client, session_factory, list_uuid
):
    """The job's one data-touching step, and the case a naive join gets wrong.

    Two doomed columns of the *same* list land in one batch. `UPDATE ... FROM columns`
    joined per-column would pick a single join partner per record and leave one of the
    two keys behind, so the statement groups by list and removes an array of keys.
    """
    first = await _create(client, list_uuid, "columns")
    second = (
        await client.post(
            f"/api/lists/{list_uuid}/columns/",
            json={
                "name": "second",
                "display_name": "Second",
                "type": "text",
                "config": {},
            },
        )
    ).json()["uuid"]
    keeper = (
        await client.post(
            f"/api/lists/{list_uuid}/columns/",
            json={
                "name": "keeper",
                "display_name": "Keeper",
                "type": "text",
                "config": {},
            },
        )
    ).json()["uuid"]

    item_uuid = (
        await client.post(
            f"/api/lists/{list_uuid}/items/",
            json={"data": {first: "a", second: "b", keeper: "c"}},
        )
    ).json()["uuid"]

    for column in (first, second):
        assert (
            await client.delete(f"/api/lists/{list_uuid}/columns/{column}")
        ).status_code == 204
        await _age(session_factory, Column, column, days=45)

    assert await purge(days=30) == 2

    data = (
        await client.get(f"/api/lists/{list_uuid}/items/{item_uuid}")
    ).json()["data"]
    assert first not in data
    assert second not in data  # both, not whichever the planner happened to pick
    assert data[keeper] == "c"


async def test_a_columns_values_survive_while_it_is_still_restorable(
    client, session_factory, list_uuid
):
    """Stripping is the *purge*'s job, not the delete's. While a column sits in the
    bin its values stay on the row, which is what makes the restore a real undo.

    Checked against storage rather than the API: `shape_values` drops keys with no live
    column, so a deleted column's values are invisible over HTTP whether they are still
    there or not. Only the row can answer this one.
    """
    column = await _create(client, list_uuid, "columns")
    item_uuid = (
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {column: "still here"}}
        )
    ).json()["uuid"]
    assert (
        await client.delete(f"/api/lists/{list_uuid}/columns/{column}")
    ).status_code == 204

    # Inside the window, so a normal run leaves it entirely alone.
    assert await purge(days=30) == 0
    async with session_factory() as db:
        row = (
            await db.execute(select(ListItem).where(ListItem.uuid == item_uuid))
        ).scalars().first()
        assert row.data[column] == "still here"

    # And the restore is a real undo, values included and served again.
    restored = await client.post(f"/api/lists/{list_uuid}/columns/{column}/restore")
    assert restored.status_code == 200, restored.text
    data = (
        await client.get(f"/api/lists/{list_uuid}/items/{item_uuid}")
    ).json()["data"]
    assert data[column] == "still here"
