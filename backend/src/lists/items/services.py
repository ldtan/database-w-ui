from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import func, or_, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from src.core.config import settings
from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.core.utils import LIKE_ESCAPE, coerce_uuid, like_pattern
from src.workspaces.models import Workspace

from src.auth.users.models import User, UserGroup

from ..columns.enums import (
    COMPUTED_TYPES,
    PRINCIPAL_TYPES,
    REFERENCE_TYPES,
    ColumnType,
)
from ..columns.chain import ChainStep, apply_formulas, build_chain
from ..columns.models import Column, in_shape
from ..columns.richtext import is_rich, sanitize
from ..columns.validation import (
    normalize_color,
    normalize_email,
    normalize_phone,
    resolve_dynamic_default,
    validate_scalar,
)
from ..models import List
from .filtering import apply_filters, apply_sorts
from .models import ListItem
from .references import label_sources
from .schemas import ListItemCreate, ListItemUpdate

# Namespace for the per-list advisory lock that serializes unique-value writes.
_UNIQUE_LOCK_NS = 0x4C495354  # "LIST"
# How many principals/rows a search resolves a label to before it stops widening.
# A search matching thousands of target rows is not a search anyone meant to run.
_SEARCH_REF_MAX = 200


async def display_column_key(
    db: AsyncSession, target_list_id: int, config: dict
) -> Optional[str]:
    """Which column of a lookup's target list supplies a row's label.

    The lookup's configured `display_column_uuid`, else the target list's primary
    column, else its **first** column. That last fallback is load-bearing, not
    politeness: `primary_column_id` is only ever set by an explicit PATCH, and is
    nulled again when that column is deleted, so most lists have none. Without it
    a lookup at such a list has no label at all — every cell renders as a row stub,
    the picker's search matches nothing, and the export writes uuids.

    Shared by the item service (resolving a label on write), the export formatter
    (writing one out) and the options endpoint (the picker), so the three cannot
    disagree about what a label is — if they did, a file this app exported would
    not import back into it.
    """
    explicit = config.get("display_column_uuid")
    if explicit:
        return str(explicit)

    primary_id = await db.scalar(
        select(List.primary_column_id).where(List.id == target_list_id)
    )
    if primary_id is not None:
        key = await db.scalar(
            select(Column.uuid).where(Column.id == primary_id, in_shape())
        )
        if key is not None:
            return str(key)

    # Same order the grid shows, so the label is the column a person would read
    # first — matching what the UI did before this moved server-side.
    first = await db.scalar(
        select(Column.uuid)
        .where(Column.list_id == target_list_id, in_shape())
        .order_by(Column.position, Column.id)
        .limit(1)
    )
    return str(first) if first is not None else None


def _as_set(value) -> set:
    """A cell's current value as a set of uuid strings — what it already points at."""
    if value is None:
        return set()
    if isinstance(value, list):
        return {str(v) for v in value if v is not None}
    return {str(value)}


def coerce_shape(column: Column, value):
    """A cell's value in the shape its column *currently* declares.

    Toggling a column's `multiple` does not rewrite its records — on a large list
    that would be a multi-minute UPDATE inside a web request — so records outlive
    the setting that shaped them, and a cell can hold `["a", "b"]` on a column that
    now allows one value. Rather than teach every reader about both shapes, the
    service coerces at its edges: here on the way out, and in `_validate_data` on
    the way in. Everything past those two points sees only the declared shape.

    Single column, list value  -> its first element (extras are dropped, which is
    what the user confirmed when they turned `multiple` off).
    Multiple column, bare value -> a one-element list.

    A scheduled reconcile converges the stored data on the same rule, after which
    this is a no-op — it is a migration aid, not a permanent translation layer.
    """
    multiple = bool((column.config or {}).get("multiple"))
    if value is None:
        return None
    if multiple and not isinstance(value, list):
        return [value]
    if not multiple and isinstance(value, list):
        return value[0] if value else None
    return value


def computed_columns(
    columns: dict[str, Column],
) -> tuple[list[ChainStep], dict[str, str]]:
    """The calculated columns to recompute, in chain order, and the name -> key map.

    Thin now: `chain.build_chain` does the work, and this stays as the name every
    reader already imports (the metric scan among them). Parsed once for a whole batch
    rather than once per record, and paired with the name map because a formula is
    written in column *names* while records are keyed by uuid.

    Order is the part that matters. A formula may reference another calculated column,
    so the plan comes back topologically sorted and a caller that walks it once — each
    step reading out of the dict the previous step wrote into — settles a whole chain in
    a single pass.

    A stored formula that no longer parses comes back with a ``None`` formula rather
    than raising: the language's bounds can tighten (they have), and a column saved
    under the old ones must cost its own cell, not the whole read — the same rule
    `evaluate` follows for a row it cannot compute. `apply_formulas` blanks those.
    """
    return build_chain(columns)


def shape_values(
    columns: dict[str, Column],
    computed: list[ChainStep],
    key_of_name: dict[str, str],
    data: dict,
) -> dict:
    """One record's stored `data` as a reader should see it.

    Values are coerced to the shape their column currently declares (see
    `coerce_shape`) and calculated columns are **recomputed**, overwriting whatever the
    record stores under their key. Kept separate from `_shape_for_read` so a caller
    holding plain dicts — a metric scan reads `data` alone rather than hydrating whole
    records, and the export streams `data` alone too — shapes them exactly as the grid
    does.

    Recomputing rather than trusting the stored value is the load-bearing half of the
    design. A calculated cell *is* stored (see `_validate_data`), but only so Postgres
    can filter and sort on it; the stored copy is an index and can fall behind a formula
    edit or a renamed operand. Because every read passes through here, no person ever
    sees a stale answer — the worst a lagging index can do is put a row in the wrong
    place in a sorted or filtered view, which the next write or a re-index corrects.

    **A key with no live column is dropped.** Deleting a column leaves its values in
    every record — that is what makes the restore a real undo — and this used to pass
    them straight through, so a deleted column vanished from the grid while its values
    kept arriving in every payload. The schema says which fields a record has, and a
    reader should see exactly those.

    Dropping it here is safe precisely because it is presentation: `_shape_for_read`
    assigns through `set_committed_value` and never flushes, and an update merges onto
    the *stored* dict (`_validate_data` starts from `dict(existing)`), so a client that
    reads a filtered record and writes it back cannot erase what was filtered. The
    values stay until `DELETE /columns/{uuid}/permanent` or the purge job removes them,
    which remain the only things that do.
    """
    shaped = {
        key: coerce_shape(columns[key], value)
        for key, value in data.items()
        if key in columns
    }
    return apply_formulas(computed, key_of_name, shaped)


class ListItemService:
    @staticmethod
    def _select(list_id: int):
        """A list's *normal* records: neither binned nor archived.

        `visible_stmt` is built on this, and every reader goes through that — so the
        two predicates here are what keep archived records out of the grid, the
        count, search, filters, sorts and the metric scan at once, rather than each
        of those having to remember.

        The pair must also match `ix_list_items_list_id_id`'s `postgresql_where`
        exactly: a partial index is only usable when the query's predicate implies
        the index's, so dropping either clause here quietly costs the index rather
        than returning wrong rows, which is the harder failure to notice.
        """
        return select(ListItem).where(
            ListItem.deleted_at.is_(None),
            ListItem.archived_at.is_(None),
            ListItem.list_id == list_id,
        )

    @classmethod
    async def get_by_uuid(
        cls,
        db: AsyncSession,
        list_id: int,
        uuid: Union[str, UUID],
        for_update: bool = False,
    ) -> Optional[ListItem]:
        """`for_update` locks the row until the transaction commits, for a caller
        that will read the record, change it in Python and write it back.

        Off by default, and deliberately: a read that only renders the record gains
        nothing from the lock and would make every page view queue behind whoever
        is mid-write.
        """
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        stmt = cls._select(list_id).where(ListItem.uuid == parsed)
        if for_update:
            stmt = stmt.with_for_update()
        result = await db.execute(stmt)
        return result.scalars().first()

    @classmethod
    async def get_unbinned_by_uuid(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> Optional[ListItem]:
        """A record that is not in the bin — live *or* archived.

        Exists for the delete path alone, and the reason is the rule that delete wins
        over archive: an archived record must still be bin-able, or the archive would
        be a place records could hide from being deleted, and the only way out would
        be to unarchive first. Every other caller wants `get_by_uuid`, which is the
        list as anyone reading it sees it.

        It still resolves a permission context, because `_security_context` tests
        only `deleted_at` — so the per-item `delete` check works here unchanged.
        """
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(
            select(ListItem).where(
                ListItem.list_id == list_id,
                ListItem.uuid == parsed,
                ListItem.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    @classmethod
    async def get_for_read(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> Optional[ListItem]:
        """`get_by_uuid` for a caller that will *render* the record, so its values
        come back in the shape their columns declare (see `coerce_shape`)."""
        item = await cls.get_by_uuid(db, list_id, uuid)
        if item is None:
            return None
        cls._shape_for_read(await cls._columns_by_key(db, list_id), [item])
        return item

    @staticmethod
    def _shape_for_read(columns: dict[str, Column], items: Sequence[ListItem]) -> None:
        """Present each record in its columns' declared shape.

        Two things happen here: stored values are coerced to the shape their column
        currently declares (see `coerce_shape`), and calculated columns are recomputed
        over the shaped record, overwriting the copy stored for the query index. Both
        are `shape_values`, which anything reading records shares so that a record
        means the same thing wherever it is read from.

        Recomputing here is what makes the stored copy safe to let fall behind: a
        formula edit or a renamed operand can leave the index stale, but never this.

        `set_committed_value`, not a plain assignment: this is a *presentation* fix,
        and marking the instance dirty would have the next flush write the coercion
        back to the database — turning every read into a silent migration, inside
        whatever transaction happened to be open. That still holds now that the
        computed value is legitimately stored, because a read is not where it gets
        written: `_validate_data` and the re-index job are, and both compute it
        deliberately rather than as a side effect of somebody looking.
        """
        computed, key_of_name = computed_columns(columns)
        for item in items:
            data = item.data or {}
            shaped = shape_values(columns, computed, key_of_name, data)
            if shaped != data:
                set_committed_value(item, "data", shaped)

    @classmethod
    async def _reference_matches(
        cls, db: AsyncSession, user, column: Column, pattern: str
    ) -> list[str]:
        """The uuids a reference column points at whose *label* matches the search.

        A `lookup`, `user` or `group` cell stores a uuid; the thing a person types is
        the label, and it lives in another table. So resolve the label to uuids here,
        then match cells against them.

        A lookup obeys the target list's permissions — `lookup` on it, then the
        per-record filter — exactly as the picker and the export do: searching must
        not become a way to learn what is in a list you cannot read.

        Archived target rows are skipped, which is the one place archiving reaches
        across lists: an order whose customer has been archived stops being findable
        by typing "Acme", though the cell still *reads* "Acme" because rendering a
        held reference is a uuid lookup and never comes through here. That is the
        rule stated whole — matching a label is search, and archived records are not
        searchable — and it is also the only shape that performs. The `LIMIT` below
        bounds the result, not the scan: a term matching nothing reads every row of
        the target list to prove it, and without this clause the query could not use
        the partial index at all, so it would keep reading the whole archive while
        every query around it got smaller.
        """
        if column.type in PRINCIPAL_TYPES:
            model = User if column.type == ColumnType.user else UserGroup
            # A user has one label; a group has two since the name/display_name split,
            # and both have to match. The cell *renders* the display name, so matching
            # only the slug would fail to find a group the searcher is looking at —
            # while matching only the display name would fail to find one they know by
            # the slug their export or CSV uses.
            labels = (
                [User.username]
                if column.type == ColumnType.user
                else [UserGroup.name, UserGroup.display_name]
            )
            rows = await db.execute(
                select(model.uuid)
                .where(
                    model.deleted_at.is_(None),
                    or_(*(l.ilike(pattern, escape=LIKE_ESCAPE) for l in labels)),
                )
                .limit(_SEARCH_REF_MAX)
            )
            return [str(u) for u in rows.scalars().all()]

        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        config = column.config or {}
        target = (
            await db.execute(
                select(List)
                .options(selectinload(List.workspace))
                .where(
                    List.uuid == coerce_uuid(config.get("target_list_uuid")),
                    List.deleted_at.is_(None),
                )
            )
        ).scalars().first()
        if target is None or not await PermissionService.has(
            db, user, ResourceType.list, target.id, Action.lookup
        ):
            return []

        key = await display_column_key(db, target.id, config)
        if key is None:
            return []

        rows = await db.execute(
            select(ListItem.uuid)
            .where(
                ListItem.list_id == target.id,
                ListItem.deleted_at.is_(None),
                ListItem.archived_at.is_(None),
                await PermissionService.item_view_filter(
                    db, user, target, Action.lookup
                ),
                ListItem.data[key].astext.ilike(pattern, escape=LIKE_ESCAPE),
            )
            .limit(_SEARCH_REF_MAX)
        )
        return [str(u) for u in rows.scalars().all()]

    @classmethod
    async def _apply_search(
        cls,
        db: AsyncSession,
        lst: List,
        user,
        stmt,
        q: Optional[str],
        columns: dict[str, Column],
    ):
        """Match ``q`` against any of the list's columns.

        Server-side, like every other search here: filtering a fetched page in the
        browser would silently fail to find records past it. Matched per column
        rather than over the whole JSONB blob, so a query cannot match a column
        *uuid* (the keys) instead of a value someone can actually see.

        Reference columns are matched by the label a person can actually read — the
        linked row's, the user's, the group's — not by the uuid the cell stores. The
        label resolves to uuids first (`_reference_matches`), and the cell is then
        matched with `?|`, which is true for a single value *and* for one element of
        a multi-valued cell.
        """
        if not (q and q.strip()) or not columns:
            return stmt
        pattern = like_pattern(q.strip())

        conditions = []
        for key, column in columns.items():
            if column.type in REFERENCE_TYPES:
                uuids = await cls._reference_matches(db, user, column, pattern)
                if uuids:
                    # Typed as text[]: `?|` takes an array of strings, and an
                    # untyped literal list is inferred as jsonb.
                    conditions.append(
                        ListItem.data[key].has_any(
                            sa.literal(uuids, postgresql.ARRAY(sa.Text))
                        )
                    )
                continue
            conditions.append(
                ListItem.data[key].astext.ilike(pattern, escape=LIKE_ESCAPE)
            )

        if not conditions:
            return stmt.where(sa.false())  # a search that can match nothing, matches nothing
        return stmt.where(sa.or_(*conditions))

    @classmethod
    async def visible_stmt(
        cls,
        db: AsyncSession,
        lst: List,
        user,
        columns: dict[str, Column],
        view_uuid: Optional[Union[str, UUID]] = None,
        q: Optional[str] = None,
        filters: Optional[dict] = None,
        group_by: Optional[dict] = None,
    ) -> tuple[sa.Select, Optional[list], Optional[dict]]:
        """The records this caller may see, through this view, matching this search.

        Everything that decides *which* records a request is about, in one place:
        the permission filter, the filters, and the search. Returns the sorts and
        the grouping alongside rather than applying them, because a caller that only
        aggregates (see the metrics service) has no use for an order and should not
        pay to impose one.

        Every reader goes through this so a metric can never disagree with the grid
        it sits above about what is on screen.

        A view supplies the filters, sorts and grouping by default. ``filters`` and
        ``group_by`` are the caller's own, for this request only, and each *replaces*
        the view's rather than narrowing it further — they are how you ask a question
        of a list without saving the question to it. None means "not asked, use the
        view's"; a filter group with no conditions therefore means something
        different, namely "no filter", which is how a caller reads the whole list
        through a view that filters.

        The search is not an override of anything: it narrows whatever is left.
        """
        from src.permissions.services import PermissionService

        from ..views.services import ViewService

        stmt = cls._select(lst.id).where(
            await PermissionService.item_view_filter(db, user, lst)
        )
        # A search narrows whatever the view already shows, rather than replacing it.
        stmt = await cls._apply_search(db, lst, user, stmt, q, columns)
        sorts = None
        saved_filters = None

        if view_uuid is not None:
            view = await ViewService.get_by_uuid(db, lst.id, view_uuid)
            if view is not None:
                config = view.config or {}
                saved_filters = config.get("filters")
                sorts = config.get("sorts")
                if group_by is None:
                    group_by = config.get("group_by")

        stmt = apply_filters(
            stmt, saved_filters if filters is None else filters, columns, user
        )
        return stmt, sorts, group_by

    @classmethod
    async def _after_id(
        cls,
        db: AsyncSession,
        lst: List,
        after: Union[str, UUID],
        sorts,
        group_by,
        skip: int,
    ) -> int:
        """The row id a keyset page starts after, or a ValidationError saying why not.

        Why a cursor is worth having at all: `skip` makes Postgres produce every row
        the page skips and throw it away. That is not something an index can fix —
        OFFSET is applied above the scan, so the scan owes complete rows, heap fetches
        included, for all of them. Measured on a 100k list, one page of 100 costs
        0.08 ms at page 1 and 68 ms at page 990; the same page fetched by cursor costs
        0.08 ms wherever it sits. The gap opens far earlier than "deep paging" suggests
        — page 50 of a filtered view measured 99 ms, and page 50 of a mere 10k-record
        list 27 ms, because the filter and the sparsity both make the discarded rows
        more expensive to produce.

        The cursor is a record uuid rather than an encoded blob. The caller already
        holds it (it is the last row of the page it is showing), it is already this
        API's public name for a row, and — the reason that matters — it cannot encode
        a stale ordering, because it encodes no ordering at all.

        Which is also why it is refused rather than honoured in a sorted or grouped
        listing. `id > :after` only names a position when `id` is what the rows are
        ordered by; under any other order it names an arbitrary subset. A cursor
        answered approximately is worse than one rejected, since a caller cannot tell
        from the rows it gets back that it lost some — the same reason `sort_by` on an
        unknown column is not quietly replaced with the view's.
        """
        if skip:
            raise ValidationError(
                "Ask for a page with either `after` or `skip`, not both"
            )
        if sorts or group_by:
            raise ValidationError(
                "`after` needs the default order; this request is sorted or grouped. "
                "Page it with `skip`, or drop the sort with `sort_by=`"
            )
        parsed = coerce_uuid(after)
        after_id = (
            None
            if parsed is None
            else await db.scalar(
                select(ListItem.id).where(
                    ListItem.list_id == lst.id,
                    ListItem.uuid == parsed,
                    # A cursor names a position in the listing, so it has to name a
                    # record the listing contains — archived as well as deleted.
                    ListItem.deleted_at.is_(None),
                    ListItem.archived_at.is_(None),
                )
            )
        )
        if after_id is None:
            # Starting from the top instead would look, to the caller, like the list
            # rewound — and a record deleted or archived out from under a cursor is
            # exactly when that would happen.
            raise ValidationError(
                "No record in this list has that uuid, so there is no page after it"
            )
        return after_id

    @classmethod
    async def list(
        cls,
        db: AsyncSession,
        lst: List,
        user,
        view_uuid: Optional[Union[str, UUID]] = None,
        skip: int = 0,
        limit: int = 100,
        q: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_dir: str = "asc",
        filters: Optional[dict] = None,
        group_by: Optional[dict] = None,
        after: Optional[Union[str, UUID]] = None,
    ) -> Sequence[ListItem]:
        """One page of the records this caller may see, in this view, matching ``q``.

        ``sort_by`` (a column uuid) is the order the *caller* asked for — a clicked
        grid header — and it replaces the view's saved sorts rather than adding to
        them: clicking a header means "order by this", not "then by this". The same
        rule governs ``filters`` and ``group_by`` (see `visible_stmt`): each is this
        request's answer, and it stands in for whatever the view saved.

        Grouping still leads the ordering, because `apply_sorts` puts it first and a
        group's rows have to stay adjacent to be rendered under one heading. A sort
        is not lost to it — it breaks ties *within* each group.

        Passing ``sort_by`` as an empty string means "no order at all", as distinct
        from omitting it, which falls back to the view's. Naming a column that does
        not exist is read the same way: the caller stated an order and it cannot be
        honoured, so quietly substituting the view's would answer a question nobody
        asked.

        The order has to be imposed here rather than in the browser now that only
        one page is sent: sorting a page client-side would order the 25 rows that
        happened to arrive, not the list.

        ``after`` is the other way to ask for a page: give the uuid of the last
        record you already have, and the next ones come back. It costs the same at
        any depth, where ``skip`` costs more the further in you go — see the note on
        `_after_id` for why, and why no index can change that. The two are not
        combinable; ``skip`` past the first page alongside ``after`` is a caller
        asking for two different pages at once, so it is refused rather than
        guessed at.
        """
        columns = await cls._columns_by_key(db, lst.id)
        stmt, sorts, group_by = await cls.visible_stmt(
            db, lst, user, columns, view_uuid, q, filters, group_by
        )
        if sort_by is not None:
            sorts = (
                [{"column_uuid": sort_by, "direction": sort_dir}]
                if sort_by in columns
                else []
            )
        if after is not None:
            stmt = stmt.where(
                ListItem.id > await cls._after_id(db, lst, after, sorts, group_by, skip)
            )
            skip = 0
        # Only an ordered request pays for this: with nothing to sort or group by there
        # is no reference column to resolve, and `label_sources` returns an empty map
        # without touching the database.
        sources = await label_sources(db, user, columns, sorts, group_by)
        stmt = (
            apply_sorts(stmt, sorts, columns, group_by, sources)
            .offset(skip)
            .limit(limit)
        )
        items = (await db.execute(stmt)).scalars().all()
        cls._shape_for_read(columns, items)
        return items

    @classmethod
    async def count(
        cls,
        db: AsyncSession,
        lst: List,
        user,
        view_uuid: Optional[Union[str, UUID]] = None,
        q: Optional[str] = None,
        filters: Optional[dict] = None,
    ) -> int:
        """How many records `list` would return without a page — the paginator's
        total. Built off `visible_stmt`, the same predicate the listing uses, so
        the count and the page it describes cannot drift apart. That is why it takes
        ``filters`` too: a total that ignored the filter would offer pages the filter
        has already emptied.

        No grouping and no ordering: neither changes how many rows match, and
        imposing an order would sort the whole table to throw the order away.
        """
        columns = await cls._columns_by_key(db, lst.id)
        stmt, _, _ = await cls.visible_stmt(
            db, lst, user, columns, view_uuid, q, filters
        )
        return await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    @classmethod
    async def create(
        cls, db: AsyncSession, lst: List, payload: ListItemCreate, user=None
    ) -> ListItem:
        columns = await cls._columns_by_key(db, lst.id)
        data = await cls._validate_data(
            db, lst, columns, payload.data, existing=None, user=user
        )
        await cls._enforce_unique(db, lst.id, columns, data, exclude_id=None)

        item = ListItem(list_id=lst.id, data=data)
        db.add(item)
        await db.flush()
        new_uuid = item.uuid
        await db.commit()
        # get_for_read, not get_by_uuid: the response renders the record, so it
        # carries coerced values and any calculated column's computed value.
        return await cls.get_for_read(db, lst.id, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        lst: List,
        uuid: Union[str, UUID],
        payload: ListItemUpdate,
        user=None,
    ) -> Optional[ListItem]:
        # Locked, because the merge below is a read-modify-write of a single JSONB
        # blob: two writers touching *different* columns of the same record would
        # otherwise both build `merged` from the value they read before either
        # committed, and whichever commits last silently drops the other's column.
        # The UPDATE's own lock comes too late to help — by then `merged` is already
        # built from the stale read. Taking it at the SELECT makes the second writer
        # wait and then merge into the first one's result. It also hands
        # `_enforce_unique` a row that cannot change under it mid-check.
        item = await cls.get_by_uuid(db, lst.id, uuid, for_update=True)
        if item is None:
            return None

        columns = await cls._columns_by_key(db, lst.id)
        merged = await cls._validate_data(
            db, lst, columns, payload.data, existing=item.data or {}, user=user
        )
        await cls._enforce_unique(db, lst.id, columns, merged, exclude_id=item.id)
        # Reassign a new dict so SQLAlchemy detects the JSONB change.
        item.data = merged
        if payload.permissions_inherit is not None:
            item.permissions_inherit = payload.permissions_inherit
        await db.commit()
        # get_for_read: see the note in create() — the response is a rendered record.
        return await cls.get_for_read(db, lst.id, uuid)

    @classmethod
    async def delete(
        cls, db: AsyncSession, lst: List, uuid: Union[str, UUID]
    ) -> bool:
        # Archived records are bin-able too — see `get_unbinned_by_uuid`. `deleted_at`
        # is set without clearing `archived_at`, so a restore returns the record to
        # the archive it came from rather than silently to the live list.
        item = await cls.get_unbinned_by_uuid(db, lst.id, uuid)
        if item is None:
            return False

        # Only this. References to the row are deliberately left intact: scrubbing
        # them here is what used to make a soft delete irreversible — the pointers
        # were destroyed and nothing remembered what they were, so restoring the row
        # brought it back stripped of every inbound relationship. They are cleared
        # when the record is *purged*, at which point it is unrecoverable anyway.
        item.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        return True

    @classmethod
    async def bulk_delete(
        cls, db: AsyncSession, lst: List, uuids: Sequence[Union[str, UUID]]
    ) -> int:
        """Soft-delete several records in one transaction. Unknown/already-deleted
        uuids are skipped. Returns the number actually deleted. References to these
        rows survive, as for a single delete.

        One UPDATE for the whole batch rather than a resolve-then-assign per uuid,
        which is what made a bulk delete cost a round trip per record. The `list_id`
        clause is what keeps this scoped: a uuid belonging to another list is simply
        not matched, so it is skipped exactly as an unknown one is — and that is the
        only place the scoping lives now, since the caller resolves permissions by
        uuid alone.

        Counting rows matched rather than uuids handled also stops a uuid repeated
        in the payload from being counted twice, which the old loop did.
        """
        parsed = {
            value
            for value in (coerce_uuid(uuid) for uuid in uuids)
            if value is not None
        }
        if not parsed:
            return 0
        result = await db.execute(
            sa.update(ListItem)
            .where(
                ListItem.list_id == lst.id,
                ListItem.uuid.in_(parsed),
                ListItem.deleted_at.is_(None),
            )
            .values(deleted_at=datetime.now(timezone.utc))
            # These rows are in the identity map — the caller's permission check
            # loaded them — so the in-memory copies are updated in Python rather
            # than left claiming they are still live. "evaluate" does that without
            # the extra SELECT "fetch" would issue.
            .execution_options(synchronize_session="evaluate")
        )
        deleted = result.rowcount
        if deleted:
            await db.commit()
        return deleted

    # -- the bin: view and restore soft-deleted records ---------------------

    @staticmethod
    def _select_deleted(list_id: int):
        """Records in the bin: soft-deleted and still inside the retention window.

        Older ones are past `PURGE_DELETED_AFTER_DAYS` and on their way out — the
        purge job may destroy them at any time — so they are no longer offered as
        restorable (see gap #6 in docs/known-gaps.md). With a 0-day window the bin
        is always empty, which is exactly "purge as soon as the job runs".
        """
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=settings.PURGE_DELETED_AFTER_DAYS
        )
        return select(ListItem).where(
            ListItem.list_id == list_id,
            ListItem.deleted_at.is_not(None),
            ListItem.deleted_at >= cutoff,
        )

    @classmethod
    async def get_deleted_by_uuid(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> Optional[ListItem]:
        """A single record from the bin. `get_by_uuid` filters deleted rows out, so
        restore needs its own fetch."""
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(
            cls._select_deleted(list_id).where(ListItem.uuid == parsed)
        )
        return result.scalars().first()

    @classmethod
    async def list_deleted(
        cls, db: AsyncSession, lst: List, skip: int = 0, limit: int = 100
    ) -> Sequence[ListItem]:
        """The list's bin, most recently deleted first, shaped for rendering so its
        reference and calculated cells read the same as in the live grid."""
        columns = await cls._columns_by_key(db, lst.id)
        stmt = (
            cls._select_deleted(lst.id)
            .order_by(ListItem.deleted_at.desc(), ListItem.id.desc())
            .offset(skip)
            .limit(limit)
        )
        items = (await db.execute(stmt)).scalars().all()
        cls._shape_for_read(columns, items)
        return items

    @classmethod
    async def count_deleted(cls, db: AsyncSession, lst: List) -> int:
        """How many records are in the bin — the paginator's total. Built off the
        same predicate as the listing so the two cannot drift."""
        stmt = select(func.count()).select_from(cls._select_deleted(lst.id).subquery())
        return await db.scalar(stmt) or 0

    @classmethod
    async def restore(
        cls, db: AsyncSession, lst: List, uuid: Union[str, UUID]
    ) -> Optional[ListItem]:
        """Bring a soft-deleted record back. Returns None if it is not in the bin.

        A `unique` value may have been taken while the row sat deleted, so the
        restore runs through `_enforce_unique` and can legitimately raise
        `ConflictError`. Nothing else is needed: inbound references were kept intact
        at delete time, so they simply resolve again, and a stale `multiple` shape is
        absorbed by `coerce_shape` on read.
        """
        item = await cls.get_deleted_by_uuid(db, lst.id, uuid)
        if item is None:
            return None

        columns = await cls._columns_by_key(db, lst.id)
        # exclude_id guards against the row matching itself; it is still deleted at
        # this point anyway, so the uniqueness scan (live rows only) would skip it.
        await cls._enforce_unique(db, lst.id, columns, item.data or {}, exclude_id=item.id)

        item.deleted_at = None
        await db.commit()
        return await cls.get_for_read(db, lst.id, uuid)

    @classmethod
    async def purge(
        cls, db: AsyncSession, lst: List, uuid: Union[str, UUID]
    ) -> bool:
        """Permanently destroy a single record from the bin. Returns False if it is
        not in the bin.

        Irreversible, and it does the two things soft-delete deliberately left alone,
        because now the row really is going: it scrubs the inbound lookup references
        that were kept intact so a restore could resolve them, and deletes the record's
        permission grants — polymorphic with no foreign key, so nothing else would —
        before deleting the row. One transaction, mirroring the item purge job.
        """
        item = await cls.get_deleted_by_uuid(db, lst.id, uuid)
        if item is None:
            return False

        item_id = item.id
        # References into this row go first: once it is gone the cells pointing at it
        # would be unresolvable, and nothing else would clean them.
        await cls._scrub_lookup_references(db, lst.uuid, item.uuid)
        # Grants before the row: they point at it polymorphically, so hard-deleting
        # the row would leave them orphaned at an id that no longer exists.
        await db.execute(
            sa.text(
                "DELETE FROM permission_grants "
                "WHERE resource_type = 'item' AND resource_id = :id"
            ),
            {"id": item_id},
        )
        await db.execute(
            sa.text("DELETE FROM list_items WHERE id = :id"), {"id": item_id}
        )
        await db.commit()
        return True

    # -- the archive: records kept, but out of the way ----------------------
    #
    # A second lifecycle axis beside the bin, and orthogonal to it. Where a delete
    # says "this was a mistake, and it goes for good in thirty days", an archive says
    # "this happened, and I have finished with it" — so there is no retention window
    # and no purge job, and the record is expected to sit here indefinitely.
    #
    # What it buys is that `_select` stops returning it, which takes it out of the
    # grid, the count, search, filters, sorts and the metric scan at once, and out of
    # the partial index all of those read. What it deliberately does *not* change is
    # anything outside this list: an archived row keeps its inbound references
    # resolvable and keeps its `unique` values reserved (see `_enforce_unique`).
    #
    # Delete wins where the two meet. Archiving then deleting puts a record in the
    # bin — `_select_archived` requires `deleted_at IS NULL` — because the bin is the
    # surface with the deadline on it, and a record about to be purged must not be
    # hiding somewhere with no deadline. Restoring it returns it to the archive.

    @staticmethod
    def _select_archived(list_id: int):
        """A list's archived records.

        No retention cutoff, unlike `_select_deleted`: nothing expires out of the
        archive, so everything ever archived is still here to unarchive.
        """
        return select(ListItem).where(
            ListItem.list_id == list_id,
            ListItem.archived_at.is_not(None),
            ListItem.deleted_at.is_(None),
        )

    @classmethod
    async def archive(
        cls, db: AsyncSession, lst: List, uuid: Union[str, UUID]
    ) -> bool:
        """Archive one record. False if it is not a live record of this list."""
        item = await cls.get_by_uuid(db, lst.id, uuid)
        if item is None:
            return False

        # Only this, for the same reason `delete` does only this: the inbound
        # references are what make the record worth keeping, and unarchiving has to
        # give back what archiving took.
        item.archived_at = datetime.now(timezone.utc)
        await db.commit()
        return True

    @classmethod
    async def bulk_archive(
        cls, db: AsyncSession, lst: List, uuids: Sequence[Union[str, UUID]]
    ) -> int:
        """Archive several records in one transaction, mirroring `bulk_delete`:
        one UPDATE for the batch, `list_id` doing the scoping so a uuid from another
        list is skipped like an unknown one, and rows counted rather than uuids so a
        repeat in the payload counts once.
        """
        parsed = {
            value
            for value in (coerce_uuid(uuid) for uuid in uuids)
            if value is not None
        }
        if not parsed:
            return 0
        result = await db.execute(
            sa.update(ListItem)
            .where(
                ListItem.list_id == lst.id,
                ListItem.uuid.in_(parsed),
                ListItem.deleted_at.is_(None),
                ListItem.archived_at.is_(None),
            )
            .values(archived_at=datetime.now(timezone.utc))
            # See `bulk_delete`: the caller's permission check loaded these rows, so
            # "evaluate" updates the in-memory copies without a second SELECT.
            .execution_options(synchronize_session="evaluate")
        )
        archived = result.rowcount
        if archived:
            await db.commit()
        return archived

    @classmethod
    async def get_archived_by_uuid(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> Optional[ListItem]:
        """A single archived record. `get_by_uuid` filters them out, so unarchive
        needs its own fetch — exactly as restore does."""
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(
            cls._select_archived(list_id).where(ListItem.uuid == parsed)
        )
        return result.scalars().first()

    @classmethod
    async def list_archived(
        cls, db: AsyncSession, lst: List, skip: int = 0, limit: int = 100
    ) -> Sequence[ListItem]:
        """The list's archive, most recently archived first, shaped for rendering so
        its reference and calculated cells read the same as in the live grid."""
        columns = await cls._columns_by_key(db, lst.id)
        stmt = (
            cls._select_archived(lst.id)
            .order_by(ListItem.archived_at.desc(), ListItem.id.desc())
            .offset(skip)
            .limit(limit)
        )
        items = (await db.execute(stmt)).scalars().all()
        cls._shape_for_read(columns, items)
        return items

    @classmethod
    async def count_archived(cls, db: AsyncSession, lst: List) -> int:
        """How many records are archived — the paginator's total, off the same
        predicate as the listing so the two cannot drift."""
        stmt = select(func.count()).select_from(cls._select_archived(lst.id).subquery())
        return await db.scalar(stmt) or 0

    @classmethod
    async def unarchive(
        cls, db: AsyncSession, lst: List, uuid: Union[str, UUID]
    ) -> Optional[ListItem]:
        """Bring an archived record back into the list. None if it is not archived.

        Unlike `restore` this **cannot conflict**, and that is a property of the
        archive rather than an oversight: `_enforce_unique` scans `deleted_at IS
        NULL`, which an archived row satisfies, so its unique values were never
        released and nothing can have taken them meanwhile. There is nothing to
        re-check and nothing that can 409.
        """
        item = await cls.get_archived_by_uuid(db, lst.id, uuid)
        if item is None:
            return None

        item.archived_at = None
        await db.commit()
        return await cls.get_for_read(db, lst.id, uuid)

    @classmethod
    async def _scrub_lookup_references(
        cls, db: AsyncSession, list_uuid: UUID, deleted_item_uuid: UUID
    ) -> None:
        lookup_columns = (
            await db.execute(
                select(Column).where(
                    Column.type == ColumnType.lookup,
                    Column.deleted_at.is_(None),
                    Column.config["target_list_uuid"].astext == str(list_uuid),
                )
            )
        ).scalars().all()
        await cls._drop_reference(db, lookup_columns, str(deleted_item_uuid))

    # One statement, and it never asks the column what shape its values are.
    #
    # That question is exactly what used to make this wrong: the scrub picked its
    # SQL from the column's *current* `multiple`, so after that flag was toggled it
    # ran the statement for the wrong shape, matched nothing, and quietly left the
    # deleted user's uuid sitting in the record. Now the predicate finds the rows
    # whatever shape they are in — `?|` is true for a scalar string *and* for an
    # element of an array — and the CASE rewrites each row according to the shape it
    # actually has. Nothing to keep in step, so nothing to get out of step.
    _SCRUB_SQL = sa.text(
        """
        UPDATE list_items
           SET data = jsonb_set(
                   data,
                   ARRAY[:key],
                   CASE WHEN jsonb_typeof(data -> :key) = 'array'
                        THEN COALESCE(
                                 (SELECT jsonb_agg(elem)
                                    FROM jsonb_array_elements(data -> :key) AS elem
                                   WHERE elem <> to_jsonb(CAST(:target AS text))),
                                 '[]'::jsonb)
                        ELSE 'null'::jsonb
                   END)
         WHERE list_id = :list_id
           AND deleted_at IS NULL
           AND data -> :key ?| ARRAY[CAST(:target AS text)]
        """
    )

    @classmethod
    async def _drop_reference(
        cls, db: AsyncSession, columns: Sequence[Column], target: str
    ) -> None:
        """Remove a now-dangling uuid from every record held by these columns.

        One UPDATE per column, matched by a JSONB predicate, rather than hydrating
        every referencing record into Python to rewrite it: a column on a large list
        would otherwise pull the whole list into memory inside the delete's
        transaction. Does not commit.
        """
        # The caller has typically just set deleted_at on an ORM object. Flush it
        # first: these are Core UPDATEs, and a later flush would otherwise reorder
        # them after statements that assume the delete already landed.
        await db.flush()

        for column in columns:
            await db.execute(
                cls._SCRUB_SQL,
                {
                    "list_id": column.list_id,
                    "key": str(column.uuid),
                    "target": target,
                },
            )

    # -- validation ---------------------------------------------------------

    @staticmethod
    async def _columns_by_key(db: AsyncSession, list_id: int) -> dict[str, Column]:
        """The list's fields, as every read and write sees them.

        `in_shape` rather than `deleted_at` alone: a hidden column is live but not a
        field of the list, and letting one through here would both leak
        its values into every record payload (`shape_values` emits
        whatever is in this dict) and apply its `required`/`default` on writes.
        """
        result = await db.execute(
            select(Column).where(Column.list_id == list_id, in_shape())
        )
        return {str(column.uuid): column for column in result.scalars().all()}

    @classmethod
    async def _validate_data(
        cls,
        db: AsyncSession,
        lst: List,
        columns: dict[str, Column],
        incoming: dict,
        existing: Optional[dict],
        user=None,
    ) -> dict:
        """Validate incoming values against the schema and return the record to
        store. When ``existing`` is given the update is a partial merge.

        ``user`` is the account making the write, needed to resolve a `current_user`
        default/onupdate; the temporal tokens do not need it."""
        result = dict(existing) if existing is not None else {}

        for key, value in incoming.items():
            column = columns.get(key)
            if column is None:
                raise ValidationError(f"Unknown column '{key}' for this list")
            if column.type in COMPUTED_TYPES:
                # A calculated cell is derived on read and stores nothing, so there
                # is nothing to set. Reject rather than silently drop it, so a client
                # sending one learns it is not editable instead of wondering why its
                # value vanished.
                raise ValidationError(
                    f"Column '{column.name}' is calculated and cannot be set"
                )
            # Blank means empty, whatever the column type. Decided once, here, rather
            # than per type: otherwise "" clears a user cell but 400s a link cell
            # ("not a full URL"), and the same payload means different things
            # depending on which column it lands in.
            if value is None or (isinstance(value, str) and not value.strip()):
                result[key] = None
                continue
            # The other edge of the shape tolerance (see `coerce_shape`): a client
            # echoing back a legacy array to a now-single column writes the first
            # value, rather than being told its own record is invalid.
            value = coerce_shape(column, value)
            if value is None:
                result[key] = None
                continue
            # A rich text cell is stored as HTML and later rendered as HTML, so
            # it is cleaned here rather than in the editor: this is the one point
            # every write goes through — the API, a form, and a CSV import — and
            # a check only the editor performs is one an API client skips. Before
            # validation, so the length limit measures what is actually stored.
            if column.type == ColumnType.text and is_rich(column.config or {}):
                value = sanitize(value)
                if not value.strip():
                    # Nothing survived (a cell that was only markup). Same
                    # meaning as arriving blank, decided a few lines above.
                    result[key] = None
                    continue
            # Validation may normalize (a user column accepts a username and
            # stores the uuid), so the checked value is what gets written.
            #
            # `kept` is what this cell already holds. A reference to something that
            # has since been deleted stays legal *if it is already there* — otherwise
            # binning one row would make every record pointing at it unsaveable,
            # which is a fine way to lock a list. A *new* reference to a deleted
            # target is still refused.
            kept = _as_set((existing or {}).get(key))
            result[key] = await cls._validate_value(db, lst, column, value, kept, user)

        # Apply defaults / on-update stamps and enforce required, over the merged
        # record. `coerce_shape` so a resolved token lands in the column's declared
        # shape (a `multiple` user column stores ["<uuid>"], not "<uuid>").
        for key, column in columns.items():
            if column.type in COMPUTED_TYPES:
                # No stored value, so no default to apply and nothing to require.
                continue
            config = column.config or {}
            onupdate = config.get("onupdate")
            if onupdate is not None:
                # Auto-managed: re-stamped on create and every edit, overwriting
                # whatever is there — an editor cannot set it by hand.
                result[key] = coerce_shape(
                    column, resolve_dynamic_default(column.type, onupdate, user=user)
                )
            elif result.get(key) is None:
                default = config.get("default")
                if default is not None:
                    result[key] = coerce_shape(
                        column, resolve_dynamic_default(column.type, default, user=user)
                    )
            if config.get("required") and cls._is_empty(result.get(key)):
                raise ValidationError(f"Column '{column.name}' is required")

        # Nested lookups are a cross-cell rule, so they are checked over the
        # merged record rather than per column — whichever order the cells
        # arrived in, parent and child are both settled by now.
        await cls._enforce_nested_lookups(
            db, lst, columns, result, incoming, existing, user
        )

        # Every calculated cell, last, over the settled record: defaults have been
        # applied and every operand is in its final form, so a formula sees what the
        # row will actually hold rather than what arrived on the wire.
        #
        # What this writes is an *index*, not the answer. `shape_values` recomputes on
        # every read and overwrites it, so nothing a person sees depends on this being
        # current — it is here so `apply_filters`, `apply_sorts` and `_apply_search`
        # have a `data ->> key` to work with. A schema change can leave it behind;
        # `columns/backfill.py` is what brings it back.
        plan, key_of_name = build_chain(columns)
        apply_formulas(plan, key_of_name, result)

        return result

    @classmethod
    async def _enforce_nested_lookups(
        cls,
        db: AsyncSession,
        lst: List,
        columns: dict[str, Column],
        result: dict,
        incoming: dict,
        existing: Optional[dict],
        user=None,
    ) -> None:
        """Reject a nested lookup value that does not belong to the parent's.

        Only values being *added* are checked — the same `kept` rule as
        `_validate_lookup`: a stale pairing already stored must not make the
        record unsaveable, it is surfaced when the cell itself is edited. A
        dangling pairing (parent or filter column since deleted) is skipped
        rather than enforced, matching how the picker degrades; the cascade in
        ColumnService normally clears those.
        """
        for key, column in columns.items():
            if column.type != ColumnType.lookup:
                continue
            config = column.config or {}
            parent_key = config.get("parent_column_uuid")
            filter_key = config.get("target_filter_column_uuid")
            if parent_key is None or filter_key is None:
                continue
            if key not in incoming:
                # An untouched cell keeps whatever it holds, however stale.
                continue
            new = _as_set(result.get(key)) - _as_set((existing or {}).get(key))
            if not new:
                continue  # kept or cleared — nothing added to check
            parent_key, filter_key = str(parent_key), str(filter_key)
            parent = columns.get(parent_key)
            if parent is None or parent.type != ColumnType.lookup:
                continue
            target_list_id = await cls._lookup_target_id(db, column, config, user)
            if not await cls._nested_filter_live(db, target_list_id, filter_key):
                continue

            parent_set = _as_set(result.get(parent_key))
            if not parent_set:
                raise ValidationError(
                    f"Column '{column.name}' requires '{parent.name}' to be "
                    "set first"
                )
            # `_validate_lookup` already resolved labels to uuids and refused
            # unknown/deleted rows, so every uuid in `new` names a real target
            # row; this only asks whether those rows belong to the parent's.
            rows = await db.execute(
                select(ListItem.uuid, ListItem.data[filter_key]).where(
                    ListItem.list_id == target_list_id,
                    ListItem.uuid.in_(new),
                )
            )
            for _, cell in rows.all():
                if not (_as_set(cell) & parent_set):
                    raise ValidationError(
                        f"Column '{column.name}': the selected row does not "
                        f"belong to the selected '{parent.name}'"
                    )

    @classmethod
    async def _nested_filter_live(
        cls, db: AsyncSession, target_list_id: int, filter_key: str
    ) -> bool:
        """Whether a nesting's filter column is still a live column of the
        target list. Cached per request, like `_lookup_target_id` — import
        calls the write path once per row."""
        cache = db.info.setdefault("nested_filter_columns", {})
        cache_key = (target_list_id, filter_key)
        if cache_key not in cache:
            cache[cache_key] = (
                (
                    await db.execute(
                        select(Column.id).where(
                            Column.list_id == target_list_id,
                            Column.uuid == coerce_uuid(filter_key),
                            Column.deleted_at.is_(None),
                        )
                    )
                ).scalars().first()
                is not None
            )
        return cache[cache_key]

    @classmethod
    async def _enforce_unique(
        cls,
        db: AsyncSession,
        list_id: int,
        columns: dict[str, Column],
        data: dict,
        exclude_id: Optional[int],
    ) -> None:
        """Reject records that duplicate a value on a column marked unique.

        Runs after `_validate_data`, which is what lets a `calculated` column be
        unique: by now this record's computed cells are in ``data``, so the answer
        being checked is the one this write produces. The rows it is checked
        *against* are read as stored, which for a calculated column is the query
        index — so between a formula edit and a re-index, uniqueness is judged
        against the answers the old formula gave. That window admits both a false
        conflict and a missed one; `POST /lists/{uuid}/columns/recompute` closes it,
        and the nightly sweep closes it eventually.

        **Archived rows are checked against, deliberately** — the scan below tests
        `deleted_at IS NULL` only, and an archived row still satisfies it. So an
        archived record keeps its unique value reserved, and that is precisely what
        makes `unarchive` incapable of conflicting: nothing could have taken the
        value while the row was away. It is the one asymmetry with the bin, where a
        binned row *does* release its value and `restore` can therefore 409.

        **A multi-valued column is not checked at all**, which is why `unique` and
        `multiple` may no longer be configured together — see the loop below and
        `ColumnService._validate_config`.
        """
        unique_columns = {
            key: column
            for key, column in columns.items()
            if (column.config or {}).get("unique")
        }
        if not unique_columns:
            return

        # Serialize writes to this list so the check-and-insert is atomic: the
        # lock is held until this transaction commits, closing the TOCTOU window
        # where two concurrent writers both see "no duplicate" and both insert.
        await db.execute(
            select(func.pg_advisory_xact_lock(_UNIQUE_LOCK_NS, list_id))
        )

        for key, column in unique_columns.items():
            value = data.get(key)
            # A list value is skipped because there is no single value to compare,
            # and `ColumnService._validate_config` now refuses `unique` together
            # with `multiple` for exactly that reason. This stays as the guard for
            # columns configured before that check existed, and for a cell still
            # holding the shape its column had before a `multiple` toggle — both of
            # which reach here with a list under a column that says it is single.
            # Silently doing nothing was the whole bug; it is now the residue of it.
            if cls._is_empty(value) or isinstance(value, list):
                continue
            stmt = select(ListItem.id).where(
                ListItem.list_id == list_id,
                ListItem.deleted_at.is_(None),
                ListItem.data.contains({key: value}),
            )
            if exclude_id is not None:
                stmt = stmt.where(ListItem.id != exclude_id)
            existing = (await db.execute(stmt.limit(1))).scalars().first()
            if existing is not None:
                # A calculated column gets its own wording. The writer did not type
                # the value that collided — they edited an operand — so a message
                # about "that value" would be about something they cannot see in
                # front of them, and would read as a complaint about the wrong cell.
                if column.type in COMPUTED_TYPES:
                    raise ConflictError(
                        f"Column '{column.name}' must be unique; this record computes "
                        f"{value!r}, which another record already has"
                    )
                raise ConflictError(
                    f"Column '{column.name}' must be unique; that value already exists"
                )

    @staticmethod
    def _is_empty(value) -> bool:
        return value is None or value == "" or value == []

    @classmethod
    def _reference_inputs(cls, column: Column, value, noun: str):
        """The raw values of a reference cell, as (multiple?, [non-empty values]).

        Blank strings are dropped rather than resolved: an empty cell means "no
        reference", and passing "" through to a label match would silently link the
        record to whichever target row happens to have a blank label.

        Values are otherwise passed through **verbatim**. Trimming them here would
        be a silent rewrite of the thing we then match on: a target row labelled
        " Springfield High" exports with its leading space, and a trimmed import
        would fail to find it — breaking the round-trip on a file this app wrote.
        """
        multiple = bool((column.config or {}).get("multiple"))
        if multiple and not isinstance(value, list):
            cls._type_error(column, f"a list of {noun}s")

        raws = []
        for raw in value if multiple else [value]:
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                continue
            if not isinstance(raw, str):
                cls._type_error(column, f"a {noun} uuid or name")
            raws.append(raw)
        return multiple, raws

    @staticmethod
    def _as_uuid(raw: str) -> Optional[UUID]:
        """A reference value read as a uuid, or None if it is a name/label.

        Trimmed only for the *parse*: surrounding space around a uuid is noise, but
        around a label it is part of the label (see `_reference_inputs`).
        """
        return coerce_uuid(raw.strip())

    @classmethod
    async def _validate_value(
        cls,
        db: AsyncSession,
        lst: List,
        column: Column,
        value,
        kept: set = frozenset(),
        user=None,
    ):
        """Validate a value and return what should be stored (usually the value
        itself; reference types normalize to a uuid string).

        ``kept`` is the set of uuids this cell already holds; those may point at a
        deleted target, because they were valid when they were written.
        """
        # References need the database; every other type is a pure scalar check.
        if column.type == ColumnType.lookup:
            return await cls._validate_lookup(db, lst, column, value, kept, user)
        if column.type in PRINCIPAL_TYPES:
            return await cls._validate_principal(db, column, value, kept)
        if column.type == ColumnType.link and isinstance(value, str):
            # Store what was validated: the check parses the trimmed URL, so
            # trimming here keeps the stored string and the checked string the
            # same one — otherwise `unique` and `max_length` see the padding.
            value = value.strip()
        if column.type == ColumnType.email and isinstance(value, str):
            # Same rule as a link, one step further: an address is stored in its
            # canonical form, so the same mailbox typed two ways is one value.
            # `unique` depends on it, and so does a filter finding both.
            value = normalize_email(value)
        if column.type == ColumnType.color and isinstance(value, str):
            value = normalize_color(value)
        if column.type == ColumnType.phone and isinstance(value, str):
            # E.164, whatever was typed. This is the type's whole reason to exist
            # over a `text` column: one number is one value, so `unique` catches a
            # duplicate spelled differently and a filter finds every spelling of it.
            # It raises for a number it cannot make canonical, so the
            # `validate_scalar` below re-runs it only for the sake of a value that
            # reached the column some other way.
            value = normalize_phone(column.config or {}, column.name, value)
        if column.type == ColumnType.choice and (column.config or {}).get("allow_other"):
            # An "other" answer is typed, so it arrives with whatever spacing was
            # typed around it, and it is stored as the value itself. Trimmed for
            # the same reason a link is: the column's own options are stored
            # trimmed, so a padded " Todo" would otherwise become a value distinct
            # from the option it was meant to be — invisibly, since the padding
            # does not show in a cell.
            if isinstance(value, str):
                value = value.strip()
            elif isinstance(value, list):
                value = [v.strip() if isinstance(v, str) else v for v in value]
        validate_scalar(column.type, column.config or {}, column.name, value)
        return value

    @classmethod
    async def _validate_principal(
        cls, db: AsyncSession, column: Column, value, kept: set = frozenset()
    ):
        """Resolve a `user`/`group` cell to the referenced uuid(s).

        Accepts a uuid (what the UI sends) or a username / group name (what a CSV
        written by our own export contains, so an export round-trips). Stores the
        uuid either way, since names are mutable.

        Resolved in one query for the whole cell, not one per value: import runs
        this per row, so a query per value turns a bulk import into thousands of
        sequential round-trips.

        A uuid in ``kept`` — already stored in this cell — is allowed even if the
        account has since been deleted. Anything else must point at a live one.
        """
        model = User if column.type == ColumnType.user else UserGroup
        name_field = User.username if column.type == ColumnType.user else UserGroup.name
        noun = "user" if column.type == ColumnType.user else "group"

        multiple, raws = cls._reference_inputs(column, value, noun)
        if not raws:
            return [] if multiple else None

        # Parsed once per value, not once per use.
        parsed = [(raw, cls._as_uuid(raw)) for raw in raws]
        uuids = [uuid for _, uuid in parsed if uuid is not None]
        names = [raw for raw, uuid in parsed if uuid is None]

        rows = (
            await db.execute(
                select(model.uuid, name_field, model.deleted_at).where(
                    sa.or_(
                        model.uuid.in_(uuids) if uuids else sa.false(),
                        name_field.in_(names) if names else sa.false(),
                    ),
                )
            )
        ).all()
        live_uuids = {str(u) for u, _, gone in rows if gone is None}
        deleted_uuids = {str(u) for u, _, gone in rows if gone is not None}
        by_name = {name: str(u) for u, name, gone in rows if gone is None}

        resolved: list[str] = []
        for raw, uuid in parsed:
            as_str = str(uuid) if uuid else None
            if as_str and as_str in live_uuids:
                resolved.append(as_str)
                continue
            # Already in the record, and the account has since gone: keep it. The
            # alternative is that binning an account makes every record naming them
            # unsaveable until someone edits the cell.
            if as_str and as_str in kept and as_str in deleted_uuids:
                resolved.append(as_str)
                continue
            hit = by_name.get(raw)
            if hit is None:
                raise UnknownReferenceError(
                    f"Column '{column.name}' references an unknown {noun}: {raw}"
                )
            resolved.append(hit)

        return resolved if multiple else resolved[0]

    @classmethod
    async def _validate_lookup(
        cls,
        db: AsyncSession,
        lst: List,
        column: Column,
        value,
        kept: set = frozenset(),
        user=None,
    ):
        """Resolve a lookup cell to target-row uuid(s).

        Accepts a row uuid (what the UI sends) or the row's **label** — the value
        of the lookup's display column — which is what our own export writes, so a
        file round-trips. A label matching more than one row is an error rather
        than a guess: unlike a username, a row label is not unique.

        A uuid in ``kept`` — already stored in this cell — is allowed even if the
        target row has since been deleted or archived. Anything else must point at a
        live row: nothing new may be linked to a record on its way out, and nothing
        new may be linked to one somebody has finished with.
        """
        config = column.config or {}
        multiple, raws = cls._reference_inputs(column, value, "row")
        if not raws:
            return [] if multiple else None

        target_list_id = await cls._lookup_target_id(db, column, config, user)

        parsed = [(raw, cls._as_uuid(raw)) for raw in raws]
        uuids = [uuid for _, uuid in parsed if uuid is not None]
        labels = [raw for raw, uuid in parsed if uuid is None]

        # Two queries for the whole cell — one for the uuids, one for the labels —
        # rather than one per value. Import calls this per row, so per-value queries
        # turn a bulk import into thousands of sequential round-trips.
        found_uuids: set[str] = set()
        if uuids:
            rows = await db.execute(
                select(
                    ListItem.uuid, ListItem.deleted_at, ListItem.archived_at
                ).where(
                    ListItem.list_id == target_list_id,
                    ListItem.uuid.in_(uuids),
                )
            )
            for row_uuid, gone, archived in rows.all():
                # Live, or already in this cell and since deleted or archived. Both
                # states are tested here so this agrees with the label branch below:
                # if a name cannot pick an archived row, neither may its uuid, or a
                # pasted uuid would make a link the picker refuses to offer.
                if (gone is None and archived is None) or str(row_uuid) in kept:
                    found_uuids.add(str(row_uuid))

        by_label: dict[str, tuple[str, int]] = {}
        if labels:
            key = await cls._lookup_display_key(db, target_list_id, column, config)
            label_col = ListItem.data[key].astext
            # Grouped, so a label shared by a million rows costs one row of result
            # rather than a million: all we need is *one* match and whether there
            # was more than one.
            rows = await db.execute(
                # Postgres has no min(uuid); the text form is what gets stored anyway.
                select(
                    label_col,
                    func.min(sa.cast(ListItem.uuid, sa.Text)),
                    func.count(),
                )
                .where(
                    ListItem.list_id == target_list_id,
                    # Live rows only: a *label* is how something new gets picked, and
                    # nothing new may point at a row in the bin or in the archive. A
                    # uuid already in the cell is handled above.
                    ListItem.deleted_at.is_(None),
                    ListItem.archived_at.is_(None),
                    label_col.in_(labels),
                )
                .group_by(label_col)
            )
            by_label = {
                label: (row_uuid, count) for label, row_uuid, count in rows.all()
            }

        resolved: list[str] = []
        for raw, uuid in parsed:
            if uuid is not None:
                if str(uuid) not in found_uuids:
                    raise UnknownReferenceError(
                        f"Lookup column '{column.name}' references an unknown row: {raw}"
                    )
                resolved.append(str(uuid))
                continue

            match = by_label.get(raw)
            if match is None:
                raise UnknownReferenceError(
                    f"Lookup column '{column.name}' references an unknown row: {raw}"
                )
            row_uuid, count = match
            if count > 1:
                # Row labels are not unique (usernames are), so there is no defensible
                # way to pick one — say so instead of silently linking the wrong row.
                raise ValidationError(
                    f"Lookup column '{column.name}': '{raw}' matches more than one row "
                    f"in the target list; use the row uuid instead"
                )
            resolved.append(row_uuid)

        return resolved if multiple else resolved[0]

    # A lookup's target list and display column are fixed by its config, but a row
    # write resolves them — so an import would re-run these lookups for every row.
    # Cache them on the session, which is scoped to exactly one request.
    @classmethod
    async def _lookup_target_id(
        cls, db: AsyncSession, column: Column, config: dict, user=None
    ) -> int:
        """The target list's id, once the caller is known to be allowed it.

        Deliberately *not* scoped to the owner of the list holding the column. It
        was, left over from when a lookup could only be created against a list you
        own — but `ColumnService._validate_config` has since been relaxed to "any
        list you can `lookup`" (docs/known-gaps.md), and the two never met in the
        middle. The result was that a cross-owner lookup the config-time check
        accepts had the column created fine and then refused every cell write.

        What that join was really supplying is access control: owning both lists
        makes a leak impossible. Removing it means saying so directly instead, with
        the check the read path already makes (`ColumnService.lookup_options`) —
        `lookup` on the target. It is load-bearing, because the resolution below
        matches a row by its **label** as well as its uuid, so an unguarded write
        would let a caller probe a list they cannot read by guessing labels.

        ``user`` is None on the trusted internal paths that have no caller to check
        — a column conversion re-validating stored data, and the demo seeder — and
        the check is skipped for them, as it is for the dynamic defaults above.
        """
        cache = db.info.setdefault("lookup_target_ids", {})
        # Keyed by the caller rather than by the holding list's owner: what varies
        # between two resolutions of the same target within one session is who is
        # asking. The entry is only written once that caller has been authorised,
        # so a hit means "resolved *and* allowed" and the permission query runs
        # once per caller and target — not once per row of an import.
        cache_key = (getattr(user, "id", None), str(config.get("target_list_uuid")))
        if cache_key not in cache:
            target_id = (
                await db.execute(
                    select(List.id)
                    .join(Workspace, List.workspace_id == Workspace.id)
                    .where(
                        List.uuid == coerce_uuid(config.get("target_list_uuid")),
                        List.deleted_at.is_(None),
                        Workspace.deleted_at.is_(None),
                    )
                )
            ).scalars().first()
            if target_id is not None and user is not None:
                # Deferred like the other permission call sites: a module-level
                # import cycles back through auth/workspaces services.
                from src.permissions.enums import Action, ResourceType
                from src.permissions.services import PermissionService

                await PermissionService.require(
                    db, user, ResourceType.list, target_id, Action.lookup
                )
            cache[cache_key] = target_id

        target_list_id = cache[cache_key]
        if target_list_id is None:
            raise UnknownReferenceError(
                f"Lookup column '{column.name}' points at a missing target list"
            )
        return target_list_id

    @classmethod
    async def _lookup_display_key(
        cls, db: AsyncSession, target_list_id: int, column: Column, config: dict
    ) -> str:
        cache = db.info.setdefault("lookup_display_keys", {})
        cache_key = (target_list_id, str(config.get("display_column_uuid")))
        if cache_key not in cache:
            cache[cache_key] = await display_column_key(db, target_list_id, config)

        key = cache[cache_key]
        if key is None:
            raise UnknownReferenceError(
                f"Lookup column '{column.name}' can only be set by row uuid: its "
                f"target list has no column to match a label against"
            )
        return key

    @staticmethod
    def _type_error(column: Column, expected: str) -> None:
        raise ValidationError(f"Column '{column.name}' expects {expected}")
