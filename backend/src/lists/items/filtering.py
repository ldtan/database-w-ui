"""Translate a view's filter/sort config into JSONB query clauses.

Values live in ``list_items.data`` as JSONB, so comparisons go through
``data ->> '<column_uuid>'`` (text). Number columns are cast to numeric so
ranges and ordering are correct; ISO date/time strings compare and sort
correctly as text.

This is the one reader that cannot lean on ``coerce_shape`` — the comparison happens
in Postgres, not in Python — so ``_field`` reproduces its rule in SQL. Get that wrong
and a filter disagrees with the grid it filters.

One kind of ordering cannot be expressed against ``data`` at all: a reference column
stores a uuid and shows a label from another table. Resolving that needs the database and
the caller's grants, so it happens in `references.py` and arrives here as a prepared
``sources`` argument — this module stays synchronous and knows nothing about permissions.

A condition's value is normally the literal it was saved with. The exception is a
temporal column, where ``"today"`` and ``"now"`` are resolved *here*, against the
clock, every time the statement is built — so a saved view means what it says the
morning after it was saved.
"""
from typing import Optional

from sqlalchemy import Numeric, and_, case, cast, func, or_

from src.core.utils import LIKE_ESCAPE, like_pattern

from ..columns.enums import COMPUTED_TYPES, NUMERIC_TYPES, ColumnType
from ..columns.models import Column
from ..columns.validation import resolve_filter_token
from .models import ListItem

_COMPARATORS = {">", ">=", "<", "<=", "gt", "gte", "lt", "lte"}

# Every operator `_condition` below answers, and the authority on that set: the
# `operator` field of a saved condition is typed against it (views/schemas.py), and
# `frontend/src/features/views/operators.ts` names the ten the editor offers.
#
# Typing it is what makes an unknown operator a 422. Before that, `_condition`
# returned None for one it did not recognise and `apply_filters` dropped the
# condition — so a typo, or a client written against a spelling this never had, got
# *the unfiltered list* rather than an error. That is the wrong direction to fail in
# for a filter: the answer looks like data, not like a mistake, and a saved view
# carries it silently from then on. The frontend module already warned that adding an
# operator on one side only "gets a filter that silently matches everything"; this is
# the other half of that warning, enforced.
#
# The aliases are deliberate and predate the editor: `is`/`eq`/`=` are one operator
# under three spellings, as are `is_not`/`neq`/`!=` and each comparator.
FILTER_OPERATORS = (
    "is", "eq", "=",
    "is_not", "neq", "!=",
    "contains", "does_not_contain",
    "is_empty", "is_not_empty",
    ">", ">=", "<", "<=",
    "gt", "gte", "lt", "lte",
)


def first_value(column: Column):
    """The cell's single reading: its first element when it holds an array, else itself.

    `coerce_shape`'s rule in SQL, split out from `_field` because two callers want it for
    different reasons. `_field` uses it to absorb records that outlived a `multiple`
    toggle. Reference sorting uses it *unconditionally* — including on a column that
    declares `multiple` — because ordering by the first linked row's label is the only
    reading of a multi-valued cell that is a single sortable value, and it beats the
    alternative the generic path gives: the serialized array text, `["<uuid>", …]`,
    ranked by its leading bracket.
    """
    cell = ListItem.data[str(column.uuid)]
    return case(
        (func.jsonb_typeof(cell) == "array", cell[0].astext),
        else_=cell.astext,
    )


def location_address(column: Column):
    """A location cell's address, as text.

    `location` is the one type whose cell is a JSON **object**, so `->>` on it yields
    the serialized blob — `{"address": "12 Rizal St", "lat": …}` — and every filter,
    sort and search would be asking questions of that string instead of the place.
    The address is the readable half, so it is what those three operate on; the
    coordinates are not something anyone filters by hand.

    Three readers share this: `_field` below, `ListItemService._apply_search`, and
    nothing else — a location is not aggregatable and not formula-referenceable, so
    the numeric and metric paths never see one.
    """
    return ListItem.data[str(column.uuid)]["address"].astext


def _field(column: Column):
    """The cell as a filter should see it — the value `coerce_shape` would return.

    Records outlive the `multiple` setting that shaped them (known-gaps: "Toggling a
    column's `multiple`"), and `->>` on a JSONB array yields its *serialized text*:
    `["Main Building"]`, never `Main Building`. A single column whose records still
    hold arrays would therefore drop every one of them from a filtered view — silently,
    and until the nightly reconcile converged the data. So mirror `coerce_shape`'s rule
    here in SQL: when the cell holds an array, take its first element, exactly as a
    reader does. Comparing raw text and coercing everywhere else is what let the two
    disagree in the first place.

    A column that *declares* `multiple` is deliberately left alone: its records hold an
    array legitimately, so there is no mismatch to absorb, and `contains` has always
    searched the serialized array — which is the closest thing to element-wise matching
    the filter language currently has. Giving multi-valued columns real per-element
    operators is a separate piece of work.
    """
    if column.type == ColumnType.location:
        return location_address(column)
    if (column.config or {}).get("multiple"):
        return ListItem.data[str(column.uuid)].astext
    return first_value(column)


def _as_text(value: object) -> Optional[str]:
    """The comparison value as the text `data ->> key` would answer for it.

    The `bool` case is not tidiness. JSONB spells its booleans `true`/`false`, so
    `data ->> key` on a checked `checkbox` cell is the string `"true"` — while
    Python's `str(True)` is `"True"`, which matches nothing. A JSON `true` in a
    condition therefore used to filter a checkbox column down to no rows at all,
    silently, and only ever answered correctly for a client that happened to send
    the string rather than the boolean. The two other places that stringify a cell
    for comparison outside Postgres — `transfer.services._csv_cell` and
    `columns.conversion._render_scalar` — both carry this same branch; this was the
    one that did not.

    Note `bool` is tested before `str()` and would have to be even if the branch
    read differently, since `bool` is a subclass of `int`.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _numeric(column: Column):
    """The cell as a number, or NULL where it does not hold one.

    A NUMERIC_TYPES column can be cast outright — every value in one is a number,
    whether it is counting seconds, stars or anything else. A `calculated` column
    cannot: the language has no fixed output type, so one formula yields numbers
    and the next yields text, and `CAST('Ada' AS numeric)` does not fail the row, it
    fails the whole statement. The `jsonb_typeof` guard is what makes a range filter or
    a numeric sort safe on a column whose type is decided per formula rather than per
    column — non-numeric cells simply compare as NULL, which is how a filter already
    treats a cell it cannot answer for.
    """
    cell = ListItem.data[str(column.uuid)]
    if column.type in NUMERIC_TYPES:
        return cast(_field(column), Numeric)
    return case((func.jsonb_typeof(cell) == "number", cell.astext), else_=None).cast(
        Numeric
    )


def _numeric_comparable(column: Column, value: object) -> bool:
    """Should a `>`/`<` on this column compare numbers rather than text?

    Always for a NUMERIC_TYPES column. For a `calculated` one it depends on the question:
    the same column can hold `30` on one row and `"Ada Lovelace"` on the next, so the
    value being compared against is the only signal for which comparison was meant —
    and text ordering on numbers ("9" > "10") is exactly the sort of wrong answer a
    stored, filterable column now makes easy to hit.
    """
    if column.type in NUMERIC_TYPES:
        return True
    return column.type in COMPUTED_TYPES and isinstance(value, (int, float)) and not (
        isinstance(value, bool)
    )


def _condition(cond: dict, columns: dict[str, Column], user=None):
    key = str(cond.get("column_uuid"))
    column = columns.get(key)
    if column is None:
        return None

    op = cond.get("operator")
    # "today"/"now" on a temporal column become the reading they name at the moment
    # this query is built, in the acting user's zone. Resolved here rather than when
    # the view was saved, because that is the whole point of writing one: a stored
    # ISO string filters on the day somebody typed it forever.
    value = resolve_filter_token(column.type, cond.get("value"), user)
    field = _field(column)
    is_number = _numeric_comparable(column, value)

    if op in ("is", "eq", "="):
        return field == _as_text(value)
    if op in ("is_not", "neq", "!="):
        return field != _as_text(value)
    # `like_pattern` rather than an f-string: `%` and `_` are LIKE wildcards, so a
    # condition whose value contains one matched far more than it named — a lone `%`
    # matched every non-null cell, and `a_c` matched `abc`. Every other ILIKE in the
    # app already went through this pair; the filter language was the one that did
    # not, and it is the one whose values are saved into a view and re-run later.
    if op == "contains":
        return field.ilike(like_pattern(_as_text(value) or ""), escape=LIKE_ESCAPE)
    if op == "does_not_contain":
        return field.notilike(like_pattern(_as_text(value) or ""), escape=LIKE_ESCAPE)
    if op == "is_empty":
        return or_(field.is_(None), field == "")
    if op == "is_not_empty":
        return and_(field.isnot(None), field != "")
    if op in _COMPARATORS:
        left = _numeric(column) if is_number else field
        right = value if is_number else _as_text(value)
        if op in (">", "gt"):
            return left > right
        if op in (">=", "gte"):
            return left >= right
        if op in ("<", "lt"):
            return left < right
        return left <= right
    return None


def apply_filters(stmt, filters: Optional[dict], columns: dict[str, Column], user=None):
    """Narrow ``stmt`` to the records matching ``filters``.

    ``user`` is who is asking, and it is read for one thing only: a temporal
    condition written against "today" or "now" resolves in their timezone (see
    `columns.validation.resolve_filter_token`). Optional because a caller with
    nobody to attribute the query to still gets a working filter, resolved in UTC.
    """
    if not filters:
        return stmt
    clauses = []
    for cond in filters.get("conditions") or []:
        clause = _condition(cond, columns, user)
        if clause is not None:
            clauses.append(clause)
    if not clauses:
        return stmt
    conjunction = filters.get("conjunction", "and")
    combined = or_(*clauses) if conjunction == "or" else and_(*clauses)
    return stmt.where(combined)


def _order_terms(
    key: str, direction: str, columns: dict[str, Column], sources: dict
) -> list:
    """How to order by one column — zero, one or two ORDER BY terms.

    A reference column (`lookup`, `user`, `group`) orders by the **label it renders**,
    not the uuid it stores, whenever `sources` carries one for it — see `references.py`
    for how that is resolved and why it can be absent. Absent, the column falls through
    to the generic text term below, which is uuid order: what a caller who cannot read
    the target sees in the cells anyway.

    Two for a `calculated` column, because it is the one column type whose values are
    not all the same kind: the formula decides per column, and a list can hold
    `qty * price` beside `CONCAT(first, last)`. Ordering it as text alone would sort
    `100` before `9`; casting it to a number alone would order every text formula by
    NULL. So the numeric reading leads and the text reading breaks its ties, which
    sorts a numeric formula numerically, a text one alphabetically, and a column that
    somehow holds both with the numbers gathered together.

    Nothing about this is read-time any more. A calculated cell is stored under its own
    key like every other value (see `chain.apply_formulas`), so ordering by it is an
    ordinary `data ->> key` and not the no-op it used to have to be.
    """
    column = columns.get(key)
    if column is None:
        return []
    descending = direction == "desc"

    def directed(term):
        return term.desc() if descending else term.asc()

    source = sources.get(key)
    if source is not None:
        return [directed(source.label)]
    if column.type in COMPUTED_TYPES:
        return [directed(_numeric(column)), directed(_field(column))]
    field = _field(column)
    if column.type in NUMERIC_TYPES:
        field = cast(field, Numeric)
    return [directed(field)]


def ordered_keys(sorts, group_by: Optional[dict] = None) -> list[str]:
    """The column uuids a request orders by, in ORDER BY order, deduped.

    Grouping leads, because a group's rows have to stay adjacent to be rendered under
    one heading. Shared with `references.label_sources`: it resolves a label for every
    key here, and `apply_sorts` joins one onto every key here. Two copies of this walk
    could disagree, and the way they would show it is a column quietly ordering by uuid
    again — the exact bug the labels exist to fix.
    """
    keys = [str(group_by.get("column_uuid"))] if group_by else []
    keys += [str(spec.get("column_uuid")) for spec in sorts or []]
    return list(dict.fromkeys(keys))


def apply_sorts(
    stmt,
    sorts,
    columns: dict[str, Column],
    group_by: Optional[dict] = None,
    sources: Optional[dict] = None,
):
    """Order ``stmt``, joining in whatever labels the ordering needs.

    ``sources`` comes from `references.label_sources`, which is async — the resolution
    needs the database and the caller's permissions, and this function has neither. It
    defaults to none, which is both what the pure-config callers want and the behaviour
    every sort had before reference columns learned to order by their label.

    The joins are LEFT and keyed on a unique column, so they add columns to the row and
    never rows to the result. A key is joined once however many times it is ordered by,
    which is what stops a column that both groups and sorts from being added to the FROM
    clause twice.
    """
    sources = sources or {}
    for key in ordered_keys(sorts, group_by):
        source = sources.get(key)
        if source is not None:
            stmt = stmt.join(source.table, source.onclause, isouter=True)

    order = []
    # Grouping requires all rows of a group to be adjacent, so order by the
    # group column first; the view's own sorts break ties within each group.
    if group_by:
        order.extend(
            _order_terms(str(group_by.get("column_uuid")), "asc", columns, sources)
        )
    for spec in sorts or []:
        order.extend(
            _order_terms(
                str(spec.get("column_uuid")), spec.get("direction"), columns, sources
            )
        )
    order.append(ListItem.id)  # stable tiebreaker, and the default ordering
    return stmt.order_by(*order)
