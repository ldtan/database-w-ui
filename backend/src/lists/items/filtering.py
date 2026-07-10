"""Translate a view's filter/sort config into JSONB query clauses.

Values live in ``list_items.data`` as JSONB, so comparisons go through
``data ->> '<column_uuid>'`` (text). Number columns are cast to numeric so
ranges and ordering are correct; ISO date/time strings compare and sort
correctly as text.
"""
from typing import Optional

from sqlalchemy import Numeric, and_, cast, or_

from ..columns.enums import ColumnType
from ..columns.models import Column
from .models import ListItem

_COMPARATORS = {">", ">=", "<", "<=", "gt", "gte", "lt", "lte"}


def _field(key: str):
    return ListItem.data[key].astext


def _as_text(value: object) -> Optional[str]:
    return value if value is None else str(value)


def _condition(cond: dict, columns: dict[str, Column]):
    key = str(cond.get("column_uuid"))
    column = columns.get(key)
    if column is None:
        return None

    op = cond.get("operator")
    value = cond.get("value")
    field = _field(key)
    is_number = column.type == ColumnType.number

    if op in ("is", "eq", "="):
        return field == _as_text(value)
    if op in ("is_not", "neq", "!="):
        return field != _as_text(value)
    if op == "contains":
        return field.ilike(f"%{value}%")
    if op == "is_empty":
        return or_(field.is_(None), field == "")
    if op == "is_not_empty":
        return and_(field.isnot(None), field != "")
    if op in _COMPARATORS:
        left = cast(field, Numeric) if is_number else field
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


def apply_sorts(stmt, sorts, columns: dict[str, Column]):
    order = []
    for spec in sorts or []:
        key = str(spec.get("column_uuid"))
        column = columns.get(key)
        if column is None:
            continue
        field = _field(key)
        if column.type == ColumnType.number:
            field = cast(field, Numeric)
        order.append(
            field.desc() if spec.get("direction") == "desc" else field.asc()
        )
    order.append(ListItem.id)  # stable tiebreaker, and the default ordering
    return stmt.order_by(*order)
