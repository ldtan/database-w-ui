"""`unique` and `multiple` may not be configured together.

They used to be accepted together and then mean nothing at all. `_enforce_unique`
skips any cell holding a table, because there is no single value to compare, so a
column carrying both took unlimited duplicates in silence — the setting sat in the
dialog, the constraint did not exist, and nothing anywhere said so.

Refused rather than implemented. A multi-valued unique column would have to answer
"may two records share *any* element", which is a different constraint with its own
query and its own index, and nobody has asked for it. The bug is a column claiming
something it does not do, and refusing the combination is what stops it claiming.

The last test here is the other half: columns configured before this check keep
working. There is no retro-validation and no migration, so the skip in
`_enforce_unique` is still load-bearing and must stay.
"""
from sqlalchemy import select

from src.tables.columns.models import Column


async def _create(client, table_uuid: str, **body):
    return await client.post(f"/api/tables/{table_uuid}/columns/", json=body)


async def test_creating_a_unique_multiple_column_is_refused(client, table_uuid):
    resp = await _create(
        client,
        table_uuid,
        name="tags",
        display_name="Tags",
        type="choice",
        config={"choices": "a;b;c", "unique": True, "multiple": True},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    # Both settings are named: the caller has to pick one, and a message naming
    # only "unique" would read as "you cannot have unique columns".
    assert "unique" in detail and "multiple" in detail


async def test_either_setting_alone_is_still_fine(client, table_uuid):
    """The refusal is about the pair. Neither setting is restricted on its own."""
    only_unique = await _create(
        client,
        table_uuid,
        name="serial",
        display_name="Serial",
        type="choice",
        config={"choices": "a;b;c", "unique": True},
    )
    assert only_unique.status_code == 201, only_unique.text

    only_multiple = await _create(
        client,
        table_uuid,
        name="tags",
        display_name="Tags",
        type="choice",
        config={"choices": "a;b;c", "multiple": True},
    )
    assert only_multiple.status_code == 201, only_multiple.text


async def test_the_refusal_is_not_specific_to_choice_columns(client, table_uuid):
    """`multiple` belongs to every reference type too, and `unique` is common to
    all of them — so the check sits above the per-type blocks rather than inside
    the choice one."""
    resp = await _create(
        client,
        table_uuid,
        name="reviewers",
        display_name="Reviewers",
        type="user",
        config={"unique": True, "multiple": True},
    )
    assert resp.status_code == 400, resp.text


async def test_patching_multiple_on_over_a_unique_column_is_refused(
    client, table_uuid, add_column
):
    """The check runs after the config merge, so a PATCH that resends only the
    setting it is turning on still sees the stored one it collides with. Before
    the merge it would have compared against a half-empty dict and let it through."""
    col = await add_column(
        table_uuid,
        name="serial",
        display_name="Serial",
        type="choice",
        config={"choices": "a;b;c", "unique": True},
    )
    resp = await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}",
        json={"config": {"multiple": True}},
    )
    assert resp.status_code == 400, resp.text


async def test_patching_unique_on_over_a_multiple_column_is_refused(
    client, table_uuid, add_column
):
    """The same collision from the other side."""
    col = await add_column(
        table_uuid,
        name="tags",
        display_name="Tags",
        type="choice",
        config={"choices": "a;b;c", "multiple": True},
    )
    resp = await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}",
        json={"config": {"unique": True}},
    )
    assert resp.status_code == 400, resp.text


async def test_turning_one_off_in_the_same_patch_is_accepted(
    client, table_uuid, add_column
):
    """Refusing the pair must not make a column that holds it unfixable. Sending
    both keys at once resolves the collision, so there is always a way out that
    does not involve deleting the column."""
    col = await add_column(
        table_uuid,
        name="tags",
        display_name="Tags",
        type="choice",
        config={"choices": "a;b;c", "multiple": True},
    )
    resp = await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}",
        json={"config": {"unique": True, "multiple": False}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["unique"] is True
    assert resp.json()["config"]["multiple"] is False


async def test_a_column_configured_before_the_check_still_writes(
    client, session_factory, table_uuid, add_column
):
    """No retro-validation, so columns that predate this check keep both settings
    and keep working — writing to one must not raise, and duplicates go on being
    accepted exactly as they were.

    This is why `_enforce_unique` keeps skipping table values instead of relying on
    the config check to have made tables impossible. Written straight to the
    database, because the API is now the one thing that cannot produce this state.
    """
    col = await add_column(
        table_uuid,
        name="tags",
        display_name="Tags",
        type="choice",
        config={"choices": "a;b;c", "multiple": True},
    )
    async with session_factory() as db:
        column = (
            await db.execute(select(Column).where(Column.uuid == col))
        ).scalars().one()
        column.config = {**(column.config or {}), "unique": True}
        await db.commit()

    first = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: ["a"]}}
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: ["a"]}}
    )
    assert second.status_code == 201, second.text
