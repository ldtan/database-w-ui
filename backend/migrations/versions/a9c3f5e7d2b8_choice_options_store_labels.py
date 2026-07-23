"""choice options store labels, cells store the label

Choice columns used to store options as ``[{id, label, color}]`` with records
holding the option **id** (a slug of the label nobody could see — known-gaps §4).
Options are now plain label strings and a cell stores the label itself, so this
rewrites, per choice column:

1. the column config — ``options`` becomes a list of labels, a ``default``
   holding an id becomes the label;
2. every record cell for that column, id -> label (element-wise when multiple);
3. every view filter condition on that column, id -> label.

Soft-deleted columns, records and views are included: a restore must not
resurrect id-shaped data. Hand-written: autogenerate cannot see inside JSONB.

Revision ID: a9c3f5e7d2b8
Revises: d5e2f8a1c9b7
Create Date: 2026-07-23 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = 'a9c3f5e7d2b8'
down_revision: Union[str, Sequence[str], None] = 'd5e2f8a1c9b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Rows rewritten per fetch, same reasoning as maintenance/reconcile_shapes.
CHUNK = 5_000

# Lightweight table handles: typed columns so JSONB round-trips as Python
# objects on any driver, without importing application models into a migration.
columns_t = sa.table(
    "columns",
    sa.column("id", sa.Integer),
    sa.column("uuid", UUID(as_uuid=True)),
    sa.column("list_id", sa.Integer),
    sa.column("config", JSONB),
)
items_t = sa.table(
    "list_items",
    sa.column("id", sa.Integer),
    sa.column("list_id", sa.Integer),
    sa.column("data", JSONB),
)
views_t = sa.table(
    "views",
    sa.column("id", sa.Integer),
    sa.column("list_id", sa.Integer),
    sa.column("config", JSONB),
)


def upgrade() -> None:
    bind = op.get_bind()

    cols = bind.execute(
        sa.select(
            columns_t.c.id, columns_t.c.uuid, columns_t.c.list_id, columns_t.c.config
        ).where(sa.text("type = 'choice'"))
    ).all()

    # list_id -> {column uuid -> {old id -> label}}, for the view pass below.
    maps_by_list: dict[int, dict[str, dict[str, str]]] = {}

    for col_id, col_uuid, list_id, config in cols:
        config = config or {}
        raw_options = config.get("options") or []
        # Old shape is [{id, label, color}]; plain strings mean the column was
        # created after the cutover (or the migration is re-running) — skip it.
        id_to_label = {
            opt["id"]: opt["label"]
            for opt in raw_options
            if isinstance(opt, dict) and "id" in opt and "label" in opt
        }
        labels = [
            opt["label"] if isinstance(opt, dict) else opt for opt in raw_options
        ]

        new_config = dict(config)
        new_config["options"] = labels
        new_config.pop("choices", None)
        default = new_config.get("default")
        if isinstance(default, str) and default in id_to_label:
            new_config["default"] = id_to_label[default]
        if new_config != config:
            bind.execute(
                sa.update(columns_t)
                .where(columns_t.c.id == col_id)
                .values(config=new_config)
            )

        if not id_to_label:
            continue
        maps_by_list.setdefault(list_id, {})[str(col_uuid)] = id_to_label

        # Record cells: id -> label, element-wise when the cell holds a list.
        key = str(col_uuid)
        last = 0
        while True:
            rows = bind.execute(
                sa.select(items_t.c.id, items_t.c.data)
                .where(
                    items_t.c.list_id == list_id,
                    sa.func.jsonb_exists(items_t.c.data, key),
                    items_t.c.id > last,
                )
                .order_by(items_t.c.id)
                .limit(CHUNK)
            ).all()
            if not rows:
                break
            for item_id, data in rows:
                last = item_id
                value = (data or {}).get(key)
                if isinstance(value, list):
                    new_value = [id_to_label.get(v, v) for v in value]
                elif isinstance(value, str):
                    new_value = id_to_label.get(value, value)
                else:
                    continue
                if new_value != value:
                    new_data = dict(data)
                    new_data[key] = new_value
                    bind.execute(
                        sa.update(items_t)
                        .where(items_t.c.id == item_id)
                        .values(data=new_data)
                    )

    # View filters: a saved condition on a choice column stores the option value.
    if maps_by_list:
        views = bind.execute(
            sa.select(views_t.c.id, views_t.c.list_id, views_t.c.config).where(
                views_t.c.list_id.in_(list(maps_by_list))
            )
        ).all()
        for view_id, list_id, config in views:
            config = config or {}
            conditions = (config.get("filters") or {}).get("conditions") or []
            col_maps = maps_by_list[list_id]
            changed = False
            for cond in conditions:
                mapping = col_maps.get(str(cond.get("column_uuid")))
                if not mapping:
                    continue
                value = cond.get("value")
                if isinstance(value, str) and value in mapping:
                    cond["value"] = mapping[value]
                    changed = True
                elif isinstance(value, list):
                    new_value = [mapping.get(v, v) for v in value]
                    if new_value != value:
                        cond["value"] = new_value
                        changed = True
            if changed:
                bind.execute(
                    sa.update(views_t)
                    .where(views_t.c.id == view_id)
                    .values(config=config)
                )


def downgrade() -> None:
    # One-way: the labels cannot recover which slug each cell used to hold.
    pass
