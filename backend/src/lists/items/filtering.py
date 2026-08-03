"""Translate a view's filter/sort config into JSONB query clauses.

Values live in ``list_items.data`` as JSONB, so comparisons go through
``data ->> '<column_uuid>'`` (text). Number columns are cast to numeric so
ranges and ordering are correct; ISO date/time strings compare and sort
correctly as text.

This is the one reader that cannot lean on ``coerce_shape`` — the comparison happens
in Postgres, not in Python — so ``_field`` reproduces its rule in SQL. Get that wrong
and a filter disagrees with the grid it filters.
"""
from typing import Optional

from sqlalchemy import Numeric, and_, case, cast, func, or_

from ..columns.enums import COMPUTED_TYPES, ColumnType
from ..columns.models import Column
from .models import ListItem

_COMPARATORS = {">", ">=", "<", "<=", "gt", "gte", "lt", "lte"}


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
    cell = ListItem.data[str(column.uuid)]
    if (column.config or {}).get("multiple"):
        return cell.astext
    return case(
        (func.jsonb_typeof(cell) == "array", cell[0].astext),
        else_=cell.astext,
    )


def _as_text(value: object) -> Optional[str]:
    return value if value is None else str(value)


def _numeric(column: Column):
    """The cell as a number, or NULL where it does not hold one.

    A `number` column can be cast outright — every value in it is one. A `calculated`
    column cannot: the language has no fixed output type, so one formula yields numbers
    and the next yields text, and `CAST('Ada' AS numeric)` does not fail the row, it
    fails the whole statement. The `jsonb_typeof` guard is what makes a range filter or
    a numeric sort safe on a column whose type is decided per formula rather than per
    column — non-numeric cells simply compare as NULL, which is how a filter already
    treats a cell it cannot answer for.
    """
    cell = ListItem.data[str(column.uuid)]
    if column.type == ColumnType.number:
        return cast(_field(column), Numeric)
    return case((func.jsonb_typeof(cell) == "number", cell.astext), else_=None).cast(
        Numeric
    )


def _numeric_comparable(column: Column, value: object) -> bool:
    """Should a `>`/`<` on this column compare numbers rather than text?

    Always for a `number` column. For a `calculated` one it depends on the question:
    the same column can hold `30` on one row and `"Ada Lovelace"` on the next, so the
    value being compared against is the only signal for which comparison was meant —
    and text ordering on numbers ("9" > "10") is exactly the sort of wrong answer a
    stored, filterable column now makes easy to hit.
    """
    if column.type == ColumnType.number:
        return True
    return column.type in COMPUTED_TYPES and isinstance(value, (int, float)) and not (
        isinstance(value, bool)
    )


def _condition(cond: dict, columns: dict[str, Column]):
    key = str(cond.get("column_uuid"))
    column = columns.get(key)
    if column is None:
        return None

    op = cond.get("operator")
    value = cond.get("value")
    field = _field(column)
    is_number = _numeric_comparable(column, value)

    if op in ("is", "eq", "="):
        return field == _as_text(value)
    if op in ("is_not", "neq", "!="):
        return field != _as_text(value)
    if op == "contains":
        return field.ilike(f"%{value}%")
    if op == "does_not_contain":
        return field.notilike(f"%{value}%")
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


def apply_filters(stmt, filters: Optional[dict], columns: dict[str, Column]):
    if not filters:
        return stmt
    clauses = []
    for cond in filters.get("conditions") or []:
        clause = _condition(cond, columns)
        if clause is not None:
            clauses.append(clause)
    if not clauses:
        return stmt
    conjunction = filters.get("conjunction", "and")
    combined = or_(*clauses) if conjunction == "or" else and_(*clauses)
    return stmt.where(combined)


def _order_terms(key: str, direction: str, columns: dict[str, Column]) -> list:
    """How to order by one column — zero, one or two ORDER BY terms.

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

    if column.type in COMPUTED_TYPES:
        return [directed(_numeric(column)), directed(_field(column))]
    field = _field(column)
    if column.type == ColumnType.number:
        field = cast(field, Numeric)
    return [directed(field)]


def apply_sorts(stmt, sorts, columns: dict[str, Column], group_by: Optional[dict] = None):
    order = []
    # Grouping requires all rows of a group to be adjacent, so order by the
    # group column first; the view's own sorts break ties within each group.
    if group_by:
        order.extend(_order_terms(str(group_by.get("column_uuid")), "asc", columns))
    for spec in sorts or []:
        order.extend(
            _order_terms(str(spec.get("column_uuid")), spec.get("direction"), columns)
        )
    order.append(ListItem.id)  # stable tiebreaker, and the default ordering
    return stmt.order_by(*order)
