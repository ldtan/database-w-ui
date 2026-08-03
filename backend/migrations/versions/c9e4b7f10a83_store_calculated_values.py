"""store calculated column values instead of computing them on read

A `calculated` column used to store nothing: its cell was computed while shaping a
record for the response, which meant Postgres never saw it and it could not be
searched, filtered, sorted or grouped. It is now written into ``list_items.data``
like any other value, by the item service on every record write.

That leaves two things for this revision to settle, both about data that already
exists — the code change alone would show every historical record a blank calculated
cell until somebody happened to save it again:

1. **`compute_order`** on each calculated column, the config key that says where a
   column sits in a chain (a formula may now read another calculated column). Derived
   from the dependency graph; every pre-existing column reads only plain columns, so
   they are all at the front, which is `0`.
2. **The values themselves**, computed once per record from the current formulas.

No schema change: both are JSONB payloads, in ``columns.config`` and
``list_items.data``.

**The application's own code does the computing.** Reimplementing the formula language
in SQL to keep this migration self-contained would mean two implementations of the
thing whose entire premise is that it is small and safe, and they would disagree. The
import is deferred into `upgrade()` and skipped entirely when no calculated column
exists — which is every fresh database, so a from-baseline migration run never touches
application code at all, and this revision cannot break one.

**Down is a real down.** It strips the keys back out, because a downgraded reader would
serve those stored values as if they were current while nothing maintained them any
more.

Revision ID: c9e4b7f10a83
Revises: a1c4e97b2f38
Create Date: 2026-08-03 00:00:00.000000

"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c9e4b7f10a83'
down_revision: Union[str, Sequence[str], None] = 'a1c4e97b2f38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Records read and written per statement. The whole migration is one transaction, so
# this bounds memory rather than lock duration.
CHUNK = 500

_CALCULATED_LISTS = sa.text(
    "SELECT DISTINCT list_id FROM columns "
    " WHERE type = 'calculated' AND deleted_at IS NULL AND hidden = false "
    " ORDER BY list_id"
)

_COLUMNS = sa.text(
    "SELECT uuid, name, type, position, config FROM columns "
    " WHERE list_id = :list_id AND deleted_at IS NULL AND hidden = false"
)

_RECORDS = sa.text(
    "SELECT id, data FROM list_items "
    " WHERE list_id = :list_id AND id > :after ORDER BY id LIMIT :chunk"
)

_WRITE = sa.text(
    "UPDATE list_items SET data = data || CAST(:patch AS jsonb) WHERE id = :row_id"
)

_STRIP = sa.text("UPDATE list_items SET data = data - :key WHERE id = ANY(:ids)")


def _calculated_keys(bind, list_id):
    """This list's calculated columns, as the record keys they occupy."""
    return [
        str(row.uuid)
        for row in bind.execute(_COLUMNS, {"list_id": list_id})
        if row.type == "calculated"
    ]


def upgrade() -> None:
    bind = op.get_bind()
    list_ids = [row.list_id for row in bind.execute(_CALCULATED_LISTS)]
    if not list_ids:
        return  # nothing to compute, and nothing to import to compute it with

    from src.lists.columns.backfill import COMPUTED_FOR, schema_signature
    from src.lists.columns.chain import apply_formulas, build_chain, derive_order
    from src.lists.columns.chain import formula_edges, order_of
    from src.lists.columns.enums import ColumnType
    from src.lists.columns.models import Column

    for list_id in list_ids:
        # Detached `Column` instances rather than a session load: this revision runs on
        # a raw connection, and the chain functions only ever read name/type/position/
        # config off what they are given.
        columns = {
            str(row.uuid): Column(
                name=row.name,
                type=ColumnType(row.type),
                position=row.position,
                config=row.config or {},
            )
            for row in bind.execute(_COLUMNS, {"list_id": list_id})
        }

        # 1. compute_order, in chain order so each column's dependencies are settled
        #    before it is reached.
        plan, key_of_name = build_chain(columns)
        by_name = {column.name: column for column in columns.values()}
        edges = formula_edges(list(columns.values()))
        for key, column, _formula in plan:
            wanted = derive_order(edges.get(column.name, set()), by_name, None)
            # Written even when it matches what an absent key would read as (0), so
            # every calculated column states its place rather than implying it.
            if wanted != order_of(column) or "compute_order" not in (column.config or {}):
                column.config = {**(column.config or {}), "compute_order": wanted}
                bind.execute(
                    sa.text(
                        "UPDATE columns SET config = CAST(:config AS jsonb) "
                        " WHERE uuid = CAST(:uuid AS uuid)"
                    ),
                    {"config": json.dumps(column.config), "uuid": key},
                )

        # 2. the values, then the stamp saying which schema they were computed for.
        #    Without it every calculated column would come out of this migration
        #    reporting `recompute_pending`, and a warning that is on for everybody the
        #    moment they upgrade teaches people to ignore it. Soft-deleted records are
        #    included: one that is restored should read the same as a live row.
        keys = [key for key, _column, _formula in plan]
        after = 0
        while True:
            rows = bind.execute(
                _RECORDS, {"list_id": list_id, "after": after, "chunk": CHUNK}
            ).all()
            if not rows:
                break
            after = rows[-1].id
            for row in rows:
                data = row.data or {}
                values = apply_formulas(plan, key_of_name, dict(data))
                patch = {
                    key: values[key] for key in keys if values[key] != data.get(key)
                }
                if patch:
                    bind.execute(
                        _WRITE, {"row_id": row.id, "patch": json.dumps(patch)}
                    )

        # 3. the stamp. Computed after the values, from the same `columns` the plan was
        #    built from — including the `compute_order` written back in step 1, so the
        #    signature matches the one a reader computes a moment later.
        signature = schema_signature(columns)
        for key, column in columns.items():
            if column.type is not ColumnType.calculated:
                continue
            column.config = {**(column.config or {}), COMPUTED_FOR: signature}
            bind.execute(
                sa.text(
                    "UPDATE columns SET config = CAST(:config AS jsonb) "
                    " WHERE uuid = CAST(:uuid AS uuid)"
                ),
                {"config": json.dumps(column.config), "uuid": key},
            )


def downgrade() -> None:
    bind = op.get_bind()
    for list_row in bind.execute(_CALCULATED_LISTS):
        list_id = list_row.list_id
        for key in _calculated_keys(bind, list_id):
            after = 0
            while True:
                ids = [
                    row.id
                    for row in bind.execute(
                        _RECORDS, {"list_id": list_id, "after": after, "chunk": CHUNK}
                    )
                ]
                if not ids:
                    break
                after = ids[-1]
                bind.execute(_STRIP, {"key": key, "ids": ids})

    # `compute_order` goes too: it describes a chain that only the stored-value design
    # has, and leaving it behind would have a re-upgrade treat a derived number as one
    # somebody set by hand. `_computed_for` likewise names a schema version of values
    # that no longer exist.
    bind.execute(
        sa.text(
            "UPDATE columns SET config = config - 'compute_order' - '_computed_for' "
            " WHERE type = 'calculated' "
            "   AND (config ? 'compute_order' OR config ? '_computed_for')"
        )
    )
