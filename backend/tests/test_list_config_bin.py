"""The bin for a list's views, forms and metrics.

Until this surface existed, deleting one of the three was silently permanent: the row
stayed on disk forever and nothing could bring it back. These cover the round trip,
the two edges that are real (a name taken while the row sat binned, and the retention
window), and the parts that are deliberately *not* symmetric with the live routes.

Runs as the seeded 'tester' user (DISABLE_AUTH), who owns everything, so the
configure-level gate resolves via the owner short-circuit.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from src.lists.forms.models import Form
from src.lists.metrics.models import Metric
from src.lists.views.models import View

# The three kinds, the payload that creates one, and the model behind it.
KINDS = {
    "views": (
        {"name": "by_owner", "display_name": "By owner", "config": {}},
        View,
    ),
    "forms": (
        {
            "name": "intake",
            "display_name": "Intake",
            "type": "create",
            "config": {},
        },
        Form,
    ),
    "metrics": (
        {
            "name": "row_count",
            "display_name": "Row count",
            "config": {"formula": "COUNT()"},
        },
        Metric,
    ),
}


async def _create(client, list_uuid, kind):
    payload, _ = KINDS[kind]
    resp = await client.post(f"/api/lists/{list_uuid}/{kind}/", json=dict(payload))
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def _age(session_factory, model, uuid: str, days: int) -> None:
    """Backdate a row's deletion so the retention window can be tested without
    waiting a month."""
    when = datetime.now(timezone.utc) - timedelta(days=days)
    async with session_factory() as db:
        await db.execute(
            update(model).where(model.uuid == uuid).values(deleted_at=when)
        )
        await db.commit()


@pytest.mark.parametrize("kind", list(KINDS))
async def test_delete_then_restore_round_trip(client, list_uuid, kind):
    uuid = await _create(client, list_uuid, kind)

    assert (await client.delete(f"/api/lists/{list_uuid}/{kind}/{uuid}")).status_code == 204
    # Gone from the live collection...
    live = await client.get(f"/api/lists/{list_uuid}/{kind}/")
    assert uuid not in [row["uuid"] for row in live.json()]
    assert (await client.get(f"/api/lists/{list_uuid}/{kind}/{uuid}")).status_code == 404

    # ...and present in the bin, with the timestamp a caller needs to show how much
    # of the window is left.
    binned = await client.get(f"/api/lists/{list_uuid}/bin/{kind}")
    assert binned.status_code == 200, binned.text
    assert binned.headers["X-Total-Count"] == "1"
    (row,) = binned.json()
    assert row["uuid"] == uuid
    assert row["deleted_at"] is not None

    restored = await client.post(f"/api/lists/{list_uuid}/bin/{kind}/{uuid}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["uuid"] == uuid

    # Back in the live collection, addressable again, and out of the bin.
    assert (await client.get(f"/api/lists/{list_uuid}/{kind}/{uuid}")).status_code == 200
    assert (await client.get(f"/api/lists/{list_uuid}/bin/{kind}")).json() == []


@pytest.mark.parametrize("kind", list(KINDS))
async def test_restore_conflicts_when_the_name_was_taken(client, list_uuid, kind):
    """A name is unique per list among *live* rows only, so the binned row's name is
    free the moment it is deleted — and taking it is what makes the restore a 409."""
    uuid = await _create(client, list_uuid, kind)
    assert (await client.delete(f"/api/lists/{list_uuid}/{kind}/{uuid}")).status_code == 204

    # The same name is available again precisely because the index is partial.
    replacement = await _create(client, list_uuid, kind)
    assert replacement != uuid

    clash = await client.post(f"/api/lists/{list_uuid}/bin/{kind}/{uuid}/restore")
    assert clash.status_code == 409, clash.text

    # The failed restore left it in the bin rather than half-applied.
    binned = await client.get(f"/api/lists/{list_uuid}/bin/{kind}")
    assert [row["uuid"] for row in binned.json()] == [uuid]


@pytest.mark.parametrize("kind", list(KINDS))
async def test_permanent_delete_only_from_the_bin(client, list_uuid, kind):
    uuid = await _create(client, list_uuid, kind)

    # A live one cannot be purged — destroying one is always two deliberate steps.
    early = await client.delete(f"/api/lists/{list_uuid}/bin/{kind}/{uuid}/permanent")
    assert early.status_code == 404

    assert (await client.delete(f"/api/lists/{list_uuid}/{kind}/{uuid}")).status_code == 204
    gone = await client.delete(f"/api/lists/{list_uuid}/bin/{kind}/{uuid}/permanent")
    assert gone.status_code == 204

    assert (await client.get(f"/api/lists/{list_uuid}/bin/{kind}")).json() == []
    # And it is not restorable afterwards.
    assert (
        await client.post(f"/api/lists/{list_uuid}/bin/{kind}/{uuid}/restore")
    ).status_code == 404


@pytest.mark.parametrize("kind", list(KINDS))
async def test_past_the_window_it_leaves_the_bin(client, session_factory, list_uuid, kind):
    """Past the retention window a row is due to be purged and is no longer promised
    back, so the bin stops offering it — the rule the records and lists bins follow."""
    _, model = KINDS[kind]
    uuid = await _create(client, list_uuid, kind)
    assert (await client.delete(f"/api/lists/{list_uuid}/{kind}/{uuid}")).status_code == 204
    await _age(session_factory, model, uuid, days=45)

    binned = await client.get(f"/api/lists/{list_uuid}/bin/{kind}")
    assert binned.json() == []
    assert binned.headers["X-Total-Count"] == "0"
    assert (
        await client.post(f"/api/lists/{list_uuid}/bin/{kind}/{uuid}/restore")
    ).status_code == 404


async def test_the_bin_is_addressed_by_uuid_not_by_name(client, list_uuid):
    """Every other child route takes a uuid *or* a name. The bin cannot: a binned
    `by_owner` and a live one coexist legitimately, so a name has no single answer."""
    uuid = await _create(client, list_uuid, "views")
    assert (await client.delete(f"/api/lists/{list_uuid}/views/{uuid}")).status_code == 204

    by_name = await client.post("/api/lists/%s/bin/views/by_owner/restore" % list_uuid)
    assert by_name.status_code == 422  # not a uuid, and not coerced into one


async def test_a_child_binned_on_its_own_survives_its_lists_round_trip(
    client, session_factory, list_uuid
):
    """A list delete stamps every child with the list's own `deleted_at` and its
    restore reverses exactly that set. A view binned *earlier* carries a different
    timestamp, so it is left behind — and has to still be in the bin afterwards."""
    view = await _create(client, list_uuid, "views")
    assert (await client.delete(f"/api/lists/{list_uuid}/views/{view}")).status_code == 204

    assert (await client.delete(f"/api/lists/{list_uuid}")).status_code == 204
    assert (await client.post(f"/api/lists/{list_uuid}/restore")).status_code == 200

    binned = await client.get(f"/api/lists/{list_uuid}/bin/views")
    assert [row["uuid"] for row in binned.json()] == [view]
    # The list's own default views came back live, so the restore did not simply
    # revive everything it found.
    live = await client.get(f"/api/lists/{list_uuid}/views/")
    assert live.status_code == 200
    assert view not in [row["uuid"] for row in live.json()]
    assert len(live.json()) >= 1


async def test_purging_a_view_takes_its_grants(client, session_factory, list_uuid):
    """`permission_grants` has no foreign key to its resource, so the hard delete is
    the only thing that can clear them — otherwise they outlive the row."""
    from src.auth.users.schemas import UserCreate
    from src.auth.users.services import UserService
    from src.permissions.models import PermissionGrant

    async with session_factory() as db:
        alice = await UserService.create(
            db, UserCreate(username="alice", email="a@example.com", password="pw")
        )
        alice_uuid = str(alice.uuid)

    view = await _create(client, list_uuid, "views")
    granted = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "view",
            "resource_uuid": view,
            "user_uuid": alice_uuid,
            "actions": ["view"],
        },
    )
    assert granted.status_code == 201, granted.text

    async with session_factory() as db:
        before = (
            await db.execute(
                select(PermissionGrant).where(
                    PermissionGrant.resource_type == "view"
                )
            )
        ).scalars().all()
    assert len(before) == 1

    assert (await client.delete(f"/api/lists/{list_uuid}/views/{view}")).status_code == 204
    assert (
        await client.delete(f"/api/lists/{list_uuid}/bin/views/{view}/permanent")
    ).status_code == 204

    async with session_factory() as db:
        after = (
            await db.execute(
                select(PermissionGrant).where(
                    PermissionGrant.resource_type == "view"
                )
            )
        ).scalars().all()
    assert after == []
