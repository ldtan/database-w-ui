"""Copies of a linked record's values, kept beside the record that links to it.

A merged form (forms/schemas.MergeConfig) borrows columns from the list its link
column points at. Those values live in the *other* list, which means Postgres cannot
see them — and Postgres is what filters and sorts, since `filtering.py` compiles to
SQL and has no Python evaluator to call. So each borrowed value is also written into
the referring record under a key of its own, and a filter or a sort on it is then an
ordinary `data ->> key` against the indexes that already exist.

**The stored copy is a query index, not the answer.** This is the same bargain a
`calculated` column strikes (known-gaps §5), for the same reason and with the same
safety property: `shape_values` recomputes every mirror on every read and overwrites
what is stored, so a stale copy can misplace a row in a sorted or filtered view and
can never put a wrong value on a screen or in an export. Full materialisation — trust
the stored copy — turns every missed invalidation into a plausible wrong answer, which
is the failure this arrangement exists to refuse.

Two things make mirrors harder than calculated cells, and both are handled elsewhere:

- **They go stale on somebody else's write.** A calculated cell only falls behind a
  *schema* change, which the app can notice. A mirror falls behind whenever the linked
  record is edited — a different list, possibly a different user. `ListItemService`
  fans out on that write, and `maintenance/reconcile_mirrors.py` is the backstop.
- **They cross a permission boundary.** A calculated value derives from the same
  record, so anyone who may read the record may read it. A mirrored value does not, so
  filtering and sorting on one is gated on `view` over the linked list — see
  `visible_stmt`. Without that gate, someone who cannot read the other list could
  binary-search its contents through this one's filters.

The key is `"<link column uuid>:<linked column uuid>"`. Namespaced by the link because
two lookups on one list may point at the same target, and a bare column uuid could not
tell their mirrors apart. It is deliberately *not* a column uuid, which buys two
existing behaviours unchanged: `shape_values` drops keys with no live column, so a
stale mirror cannot reach a payload by accident, and `_validate_data` merges onto the
stored dict, so a client that reads a record and PATCHes it cannot erase one.
"""
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ValidationError

from ..columns.enums import ColumnType
from ..columns.models import Column, in_shape
from ..forms.models import Form
from ..models import List

# Separates the two uuids in a mirror key. Not a character a uuid can contain, so the
# key splits unambiguously and cannot collide with a bare column uuid.
SEP = ":"


def mirror_key(link_column_uuid, linked_column_uuid) -> str:
    return f"{link_column_uuid}{SEP}{linked_column_uuid}"


def split_key(key: str) -> Optional[tuple[str, str]]:
    """The (link, linked) pair a mirror key names, or None if it is not one."""
    if SEP not in key:
        return None
    link, _, linked = key.partition(SEP)
    return (link, linked) if link and linked else None


def is_mirror_key(key: str) -> bool:
    return split_key(key) is not None


class MirrorSpec:
    """One list's mirrors: which link column, which target, which columns borrowed."""

    __slots__ = ("link_key", "target", "columns")

    def __init__(self, link_key: str, target: List, columns):
        self.link_key = link_key
        # The whole List, not just its id: `item_view_filter` takes one, and the read
        # path has to ask it per caller.
        self.target = target
        # {linked column uuid str: Column} — the borrowed columns, live only.
        self.columns: dict[str, Column] = columns

    @property
    def target_list_id(self) -> int:
        return self.target.id

    def keys(self) -> dict[str, Column]:
        """{mirror key: the linked list's column it copies}."""
        return {
            mirror_key(self.link_key, uuid): column
            for uuid, column in self.columns.items()
        }


async def specs_for(db: AsyncSession, list_id: int) -> list[MirrorSpec]:
    """What this list mirrors, cached for the request.

    Two queries at most, and only for a list that actually has a merged form — the
    common case is a list with none, which costs one indexed lookup and an empty list.
    Cached on the session because a 200-row import would otherwise ask per row.
    """
    cache = db.info.setdefault("mirror_specs", {})
    if list_id in cache:
        return cache[list_id]

    configs = (
        await db.execute(
            select(Form.config).where(
                Form.deleted_at.is_(None),
                Form.list_id == list_id,
                Form.config["merge"].isnot(None),
            )
        )
    ).scalars().all()

    # A list may carry the same merge on its create and update forms (the editor
    # writes both), so collapse by link column rather than building one spec each.
    wanted: dict[str, set[str]] = {}
    for config in configs:
        merge = (config or {}).get("merge") or {}
        link = merge.get("link_column_uuid")
        if not link:
            continue
        borrowed = wanted.setdefault(str(link), set())
        for field in merge.get("fields") or []:
            if field.get("column_uuid"):
                borrowed.add(str(field["column_uuid"]))

    specs: list[MirrorSpec] = []
    for link_key, borrowed in wanted.items():
        if not borrowed:
            continue
        link_column = (
            await db.execute(
                select(Column).where(
                    Column.list_id == list_id,
                    Column.uuid == link_key,
                    in_shape(),
                )
            )
        ).scalars().first()
        if link_column is None:
            continue  # deleted since the form was saved; the merge reads as inert
        target_uuid = (link_column.config or {}).get("target_list_uuid")
        target = (
            await db.execute(
                select(List).where(List.uuid == target_uuid, List.deleted_at.is_(None))
            )
        ).scalars().first()
        if target is None:
            continue
        columns = {
            str(c.uuid): c
            for c in (
                await db.execute(
                    select(Column).where(
                        Column.list_id == target.id,
                        Column.uuid.in_(borrowed),
                        in_shape(),
                    )
                )
            ).scalars().all()
        }
        if columns:
            specs.append(MirrorSpec(link_key, target, columns))

    cache[list_id] = specs
    return specs


def linked_uuid(spec: MirrorSpec, data: Optional[dict]) -> Optional[str]:
    """The record this row links to, as a uuid string, or None.

    A `multiple` link is refused when a merged form is configured, so a cell holding a
    list here predates that config. Take the first, matching `coerce_shape`, rather
    than mirroring nothing.
    """
    raw = (data or {}).get(spec.link_key)
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    return str(raw) if raw else None


async def fetch_map(
    db: AsyncSession, list_id: int, datas: Iterable[Optional[dict]], user
) -> dict[tuple[str, str], dict]:
    """The linked records a page of rows points at, keyed by (link, uuid).

    One query per link column for the whole page, rather than one per row — the shape
    `collectLookupUuids` already uses on the client for labels, moved server-side
    because a mirror is more than a label.

    **Filtered by the caller's access to the linked list, per caller.** A mirror is a
    copy of another list's data sitting in this one's rows; emitting it unfiltered
    would hand every reader of this list the contents of a list they may never have
    been granted. A record that is absent from the map mirrors as blank, which is the
    same degradation `LookupCell` already makes on a 403.

    **No caller means no mirrors.** Fails closed rather than open: a path that has not
    said who is asking cannot be given another list's values.
    """
    specs = await specs_for(db, list_id)
    if not specs or user is None:
        return {}

    from src.permissions.enums import Action, ResourceType
    from src.permissions.services import PermissionService

    from .models import ListItem

    found: dict[tuple[str, str], dict] = {}
    for spec in specs:
        uuids = {u for u in (linked_uuid(spec, d) for d in datas) if u}
        if not uuids:
            continue
        if not await PermissionService.has(
            db, user, ResourceType.list, spec.target.id, Action.view
        ):
            # `lookup` is deliberately not enough: it discloses a display column, and a
            # mirror is arbitrary columns. Same rule the merged form is configured under.
            continue
        rows = await db.execute(
            select(ListItem.uuid, ListItem.data).where(
                ListItem.list_id == spec.target.id,
                ListItem.uuid.in_(uuids),
                ListItem.deleted_at.is_(None),
                await PermissionService.item_view_filter(
                    db, user, spec.target, Action.view
                ),
            )
        )
        for uuid, data in rows.all():
            found[(spec.link_key, str(uuid))] = data or {}
    return found


def resolve(
    specs: list[MirrorSpec],
    linked: dict[tuple[str, str], dict],
    data: Optional[dict],
) -> dict:
    """One record's mirror cells, computed from the page's fetched map."""
    out: dict = {}
    for spec in specs:
        uuid = linked_uuid(spec, data)
        out.update(values_from(spec, linked.get((spec.link_key, uuid)) if uuid else None))
    return out


@dataclass(frozen=True)
class MirrorField:
    """A mirror dressed as a column, for `filtering.py` alone.

    That module builds every comparison as ``ListItem.data[str(column.uuid)]`` and
    reads `type` and `config` to decide how — numeric or text, single or `multiple`.
    So a stand-in whose `uuid` *is* the mirror key and whose type and config are the
    borrowed column's makes filtering and sorting a mirrored value work with **no
    change to `filtering.py` at all**: no alias parameter, no second code path, and no
    risk of the two disagreeing about what a cell means.

    Frozen, and never in the dict `_validate_data` reads. A mirror has one writer, and
    a settable stand-in in the schema dict is exactly how that would stop being true.
    """

    uuid: str
    type: ColumnType
    config: dict
    name: str


async def referenced_fields(
    db: AsyncSession, list_id: int, user, keys: Iterable[str]
) -> dict[str, MirrorField]:
    """Stand-ins for the mirror keys a request names, or a refusal.

    **The permission gate, and the one genuinely new disclosure surface in mirroring.**
    A mirrored value sits in this list's rows, so without this anyone who can read this
    list could order or filter by another list's contents — and binary-search values
    they were never granted, which is the same objection the metric strip answers with
    "a reader who cannot see a record must not learn its value from a total".

    Refuses rather than ignoring. A filter silently not applied returns rows the caller
    believes were excluded, which is worse than an error: they cannot tell.
    """
    wanted = {k for k in keys if k and is_mirror_key(k)}
    if not wanted:
        return {}

    from src.permissions.enums import Action, ResourceType
    from src.permissions.services import PermissionService

    fields: dict[str, MirrorField] = {}
    for spec in await specs_for(db, list_id):
        keyed = spec.keys()
        if not (wanted & keyed.keys()):
            continue
        if not await PermissionService.has(
            db, user, ResourceType.list, spec.target.id, Action.view
        ):
            raise ValidationError(
                f"You cannot filter or sort by fields from '{spec.target.name}' "
                f"because you do not have access to that list"
            )
        for key, column in keyed.items():
            if key in wanted:
                fields[key] = MirrorField(
                    uuid=key, type=column.type, config=column.config or {}, name=column.name
                )

    missing = wanted - fields.keys()
    if missing:
        # A key shaped like a mirror that names no live one — a stale saved view, or a
        # hand-written request. Left out rather than refused: `_condition` skips a key
        # it cannot resolve, which is what a filter on a deleted column already does.
        pass
    return fields


async def mirror_columns(db: AsyncSession, list_id: int) -> dict[str, Column]:
    """{mirror key: linked column} for every mirror this list keeps.

    What `filtering.py` needs, and the reason a mirror key is shaped the way it is:
    `_field` builds `ListItem.data[str(column.uuid)]`, so a synthetic entry whose uuid
    *is* the mirror key makes filters and sorts work on a borrowed value with no change
    to that module at all.
    """
    keys: dict[str, Column] = {}
    for spec in await specs_for(db, list_id):
        keys.update(spec.keys())
    return keys


def strip(data: dict) -> dict:
    """``data`` without its mirror keys.

    For the paths that must not carry a copy across: a clone re-creates the link column
    under a new uuid, so every mirror key in a copied record names a link that no longer
    exists. Rebuilding them on the next write (or the nightly reconcile) is cheaper than
    remapping them, and has one fewer place to get the key format wrong.
    """
    return {k: v for k, v in data.items() if not is_mirror_key(k)}


def values_from(spec: MirrorSpec, linked_data: Optional[dict]) -> dict:
    """The mirror cells a linked record produces, or blanks when there is none.

    A row with no link, or one pointing at a record that has gone, still gets the keys
    — written as ``None``. Leaving them absent instead would make "not linked" and "not
    yet mirrored" the same state to a filter, and `is_empty` could not tell them apart.
    """
    return {
        mirror_key(spec.link_key, uuid): (linked_data or {}).get(uuid)
        for uuid in spec.columns
    }


async def apply(
    db: AsyncSession, list_id: int, data: dict, *, only_links: Optional[Iterable[str]] = None
) -> None:
    """Write this record's mirror cells into ``data``, in place.

    Called at the end of `_validate_data`, beside `apply_formulas`, and for the same
    reason: the record is settled by then, so the link cell this reads is the one the
    row will actually hold rather than whatever arrived on the wire.

    ``only_links`` narrows the work to links whose cell this write touched. A record
    whose link did not move needs no re-read of the other list.
    """
    for spec in await specs_for(db, list_id):
        if only_links is not None and spec.link_key not in only_links:
            continue
        raw = data.get(spec.link_key)
        # A `multiple` link is refused when the merged form is configured, so a cell
        # holding a list here predates that config or was written around it. Take the
        # first, matching `coerce_shape`, rather than mirroring nothing.
        if isinstance(raw, list):
            raw = raw[0] if raw else None

        linked_data = None
        if raw:
            from .models import ListItem

            linked_data = await db.scalar(
                select(ListItem.data).where(
                    ListItem.list_id == spec.target_list_id,
                    ListItem.uuid == raw,
                    ListItem.deleted_at.is_(None),
                )
            )
        data.update(values_from(spec, linked_data))


async def referring_specs(db: AsyncSession, target_list_id: int) -> list[tuple[int, MirrorSpec]]:
    """Every (list id, spec) that mirrors *out of* this list.

    The reverse of `specs_for`, for the fan-out: when a record here changes, these are
    the lists holding copies of it. Answered from the link columns pointing at this
    list rather than by scanning every form, so a list nobody links to costs one
    indexed query.
    """
    cache = db.info.setdefault("mirror_referrers", {})
    if target_list_id in cache:
        return cache[target_list_id]

    target_uuid = await db.scalar(select(List.uuid).where(List.id == target_list_id))
    referring_list_ids = (
        await db.execute(
            select(Column.list_id)
            .where(
                Column.config["target_list_uuid"].astext == str(target_uuid),
                in_shape(),
            )
            .distinct()
        )
    ).scalars().all()

    found: list[tuple[int, MirrorSpec]] = []
    for list_id in referring_list_ids:
        for spec in await specs_for(db, list_id):
            if spec.target_list_id == target_list_id:
                found.append((list_id, spec))

    cache[target_list_id] = found
    return found
