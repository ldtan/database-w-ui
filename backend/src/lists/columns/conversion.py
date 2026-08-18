"""Turn one column's stored values into another column's type.

The engine behind both planned features 1 (column type conversion) and 2 (column
data migration) — they are the same operation, so they are the same code. A
conversion is a migration into a freshly-made column of the target type, followed
by a cutover; see `services.ConversionService`.

**Text is the hub.** Every type converts to `text` and `text` converts to nearly
every type, so the matrix is 2N rules rather than N². A pair with `text` on neither
side is refused rather than chained automatically: X -> text -> Y is two
conversions, each with its own report and its own chance to stop, and hiding both
behind one action is exactly what the verify-before-cutover design exists to
prevent. `date` <-> `datetime` is the one direct pair, so widening and truncating
do not take a text round trip.

**Almost none of this is new code.** Rendering to text is the export's job and
reuses its `_LabelResolver`, permissions and all. Parsing from text is the import's
job: `_parse_cell` followed by the target column's own `_validate_value`, which
already accepts a username, a group name or a row label and stores the uuid —
that is the CSV round-trip contract, and it means text -> reference costs nothing.
The one genuinely new transform is `text` -> `choice`, which has to mint the
options before it can validate against them.

**What is deliberately not enforced here.** `required` and `unique` are checked at
cutover, not per cell: a blank source cell is ordinary, and failing every one of
them during a backfill would make the report useless. `_validate_value` covers the
rest of the target's config (length, range, pattern, option membership, reference
liveness) and raises `ValidationError` per cell, which is what the report collects.
"""
from datetime import datetime, time
from typing import Optional

import sqlalchemy as sa

from src.core.exceptions import ValidationError

from ..items.models import ListItem
from ..items.services import ListItemService, coerce_shape
from ..models import List
from ..transfer.services import _LabelResolver, _parse_cell, _SKIP
from .enums import COMPUTED_TYPES, REFERENCE_TYPES, ColumnType
from .models import Column
from .validation import format_location, parse_temporal

# The most distinct values `text` -> `choice` will mint options from. A column with
# more distinct values than this is free text, not a set of choices, and turning it
# into a picker with hundreds of entries helps nobody.
MAX_MINTED_OPTIONS = 200

# Multi-valued cells render to text joined the way the export writes them, which is
# also how `_parse_cell` reads them back, so a round trip through text survives.
_JOIN = ";"


def is_supported(source: ColumnType, target: ColumnType) -> bool:
    """Whether v1 converts this pair directly."""
    if source in COMPUTED_TYPES or target in COMPUTED_TYPES:
        # `calculated` stores no value at all. Converting away from it means
        # evaluating a formula per row and persisting the result; converting to it
        # means discarding stored values and adding a formula. Both are real
        # operations, neither is a cell conversion, and neither is in v1.
        return False
    if source == target:
        return True  # a straight copy — feature 2 with matching types
    if ColumnType.text in (source, target):
        return True
    return {source, target} == {ColumnType.date, ColumnType.datetime}


def check_supported(source: Column, target: Column) -> None:
    if is_supported(source.type, target.type):
        return
    if source.type in COMPUTED_TYPES or target.type in COMPUTED_TYPES:
        raise ValidationError(
            "A calculated column cannot take part in a conversion: it stores no "
            "value of its own"
        )
    raise ValidationError(
        f"Cannot convert {source.type.value} to {target.type.value} directly. "
        f"Convert to text first, then to {target.type.value}."
    )


def _render_scalar(value, column: Optional[Column] = None) -> Optional[str]:
    """One already-resolved value as text.

    Matches the export's `_csv_cell` except for `None`, which stays `None` here: an
    empty cell must convert to an empty cell, not to the empty string, or every
    blank row in the source arrives in the target as a value.

    ``column`` is what tells a non-scalar cell from a scalar one. Only `location`
    has one today, and without this its conversion to text would write a Python dict
    repr into every record — permanently, since a conversion is the point of no
    return. `format_location` is the same rendering the export writes, so
    `text -> location` reads it back.
    """
    if value is None:
        return None
    if column is not None and column.type == ColumnType.location:
        return format_location(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _widen_or_truncate(value: str, source: ColumnType, target: ColumnType) -> str:
    """`date` <-> `datetime`, without a text round trip."""
    parsed = parse_temporal(source, value)
    if target == ColumnType.datetime:
        return datetime.combine(parsed, time.min).isoformat()
    return parsed.date().isoformat()


class CellConverter:
    """Converts values from one column's type to another's, a batch at a time.

    Built once per conversion and reused across every chunk, because the label
    resolver it holds caches what it has already looked up — rebuilding it per batch
    would re-ask the same questions for the whole run.
    """

    def __init__(self, db, lst: List, source: Column, target: Column, user):
        self._db = db
        self._lst = lst
        self._source = source
        self._target = target
        self._user = user
        self._resolver: Optional[_LabelResolver] = None

    @property
    def renders_references(self) -> bool:
        return (
            self._source.type in REFERENCE_TYPES
            and self._target.type == ColumnType.text
        )

    async def prepare(self) -> None:
        """Work out how this conversion resolves, and refuse if it cannot."""
        check_supported(self._source, self._target)
        if not self.renders_references:
            return

        self._resolver = _LabelResolver(self._db, [self._source], self._user)
        await self._resolver.prepare()
        if not self._resolver.resolves(self._source):
            # The export writes uuids when it cannot resolve a label, because it
            # shows one caller what that caller may see. A conversion writes the
            # result down for everyone and forever, so silently baking uuid strings
            # into a text column is the wrong answer — stop instead.
            raise ValidationError(
                f"Cannot convert '{self._source.name}' to text: its target cannot "
                "be read, so its values have no labels to convert into. Ask for "
                "access to the target list first."
            )

    async def load(self, rows: list[dict]) -> None:
        """Fetch whatever this chunk needs, in one round trip rather than per cell."""
        if self._resolver is not None:
            await self._resolver.load(rows)

    async def convert(self, value):
        """The source value as the target column would store it.

        Raises `ValidationError` for a cell that cannot be converted; the caller
        records it against its record and carries on.
        """
        # Convert what a *reader* sees, not what happens to be on disk: a record can
        # still hold the shape its column had before a `multiple` toggle, and the
        # conversion should not preserve that accident into the new column.
        value = coerce_shape(self._source, value)
        if value is None:
            return None

        source_t, target_t = self._source.type, self._target.type

        if source_t == target_t:
            # Same type: only the shape can differ.
            return coerce_shape(self._target, value)

        if target_t == ColumnType.text:
            return self._to_text(value)

        if source_t == ColumnType.text:
            # No `coerce_shape` on the way in here. `_parse_cell` already knows the
            # target's shape — for a multiple column it splits a ";"-joined string
            # into its values — so pre-wrapping the text would hand it a list
            # containing one unsplit string and nest the result.
            parsed = _parse_cell(self._target, value)
            if parsed is _SKIP:
                return None
            return await ListItemService._validate_value(
                self._db, self._lst, self._target, parsed
            )

        # `date` <-> `datetime`, the only pair left.
        value = coerce_shape(self._target, value)
        if value is None:
            return None
        if isinstance(value, list):
            return [self._temporal(item) for item in value if item is not None]
        return self._temporal(value)

    def _to_text(self, value):
        """Render to text: resolve references to labels, then stringify."""
        if self._resolver is not None:
            value = self._resolver.cell(self._source, value)
        if isinstance(value, list):
            rendered = [_render_scalar(v, self._source) for v in value]
            joined = _JOIN.join(v for v in rendered if v is not None)
            return joined or None
        return _render_scalar(value, self._source)

    def _temporal(self, value):
        try:
            return _widen_or_truncate(str(value), self._source.type, self._target.type)
        except (ValueError, TypeError) as exc:
            raise ValidationError(
                f"'{value}' is not {self._source.type.value} data"
            ) from exc


async def distinct_text_values(db, list_id: int, column: Column) -> list[str]:
    """The distinct non-empty values of a text column, for minting choice options.

    Ordered so the option list is stable between runs — a set's iteration order is
    not something to write into a column's config.

    Archived rows count, unlike every read that serves the grid. A conversion
    rewrites the whole column, archived rows included, so an option they hold must
    exist or unarchiving one would surface a value the column no longer allows.
    """
    cell = ListItem.data[str(column.uuid)].astext
    rows = await db.execute(
        sa.select(sa.distinct(cell))
        .where(
            ListItem.list_id == list_id,
            ListItem.deleted_at.is_(None),
            cell.isnot(None),
            cell != "",
        )
        # One past the cap is enough to know the cap was exceeded, and it keeps a
        # free-text column with a million distinct values out of memory.
        .limit(MAX_MINTED_OPTIONS + 1)
    )
    # Sorted here rather than in SQL: `SELECT DISTINCT` requires its `ORDER BY`
    # expression to appear in the select list, and each time this JSONB extraction
    # is spelled out it renders as a fresh bind parameter, so Postgres does not
    # recognise the two as the same expression.
    return sorted(value for value in rows.scalars().all() if value is not None)


async def mint_choice_options(db, list_id: int, source: Column, supplied) -> list[str]:
    """The `options` a `text` -> `choice` conversion's target column should have.

    A caller that supplied options is stating what the choices *are*, and a value
    outside that set should fail per cell rather than quietly widening it — so
    supplied options win untouched. Otherwise mint one per distinct source value.

    Runs before the target column is created, not after: `_validate_config` refuses
    a choice column with no options at all, so there is no valid empty column to
    create first and fill in afterwards.
    """
    if supplied:
        return list(supplied)

    values = await distinct_text_values(db, list_id, source)
    if not values:
        raise ValidationError(
            f"'{source.name}' has no values to build choice options from. Supply "
            "the options explicitly."
        )
    if len(values) > MAX_MINTED_OPTIONS:
        raise ValidationError(
            f"'{source.name}' holds {len(values)} distinct values, more than the "
            f"{MAX_MINTED_OPTIONS} a choice column will mint. That is free text "
            "rather than a set of choices; supply the options explicitly if not."
        )
    return values
