"""The order a list's `calculated` columns are evaluated in, and the loops to refuse.

A calculated column's value is **stored** like any other cell, written by the item
service on create and on every update (see `items/services.py`). That is what lets
Postgres search, filter, sort and group it along with everything else, and it is also
what makes order matter: a formula may reference another calculated column, so a chain
like ``subtotal -> tax -> total`` has to be computed front to back or the later links
read the record's previous write instead of this one.

The stored value is an **index, not the source of truth** — every read recomputes it
(`shape_values`) and overwrites the stored copy before serialising. So the order here has
to be right for the same reason on both sides: the writer walks this plan to fill the
index, and the reader walks the same plan to produce what it shows. They share one
builder precisely so they cannot disagree about what a chain means.

The graph is seen twice:

* **`build_chain`** answers "in what order do I evaluate these?" for a caller holding one
  record. It topologically sorts, so it is correct whatever order the columns happen to
  arrive in — a config edited around the service, or a rename that quietly rewired a
  reference, cannot make a chain compute stale values.
* **`formula_edges`** + **`find_cycle`** answer "does this save close a loop?" for the
  column service, in *names*, because that is the form a save has and the form its error
  message needs.

Order among calculated columns that do **not** reference each other is arbitrary and is
left that way: it is settled by `position` then `name` so a plan never depends on
dictionary iteration, but nothing infers meaning from it. (A persisted, settable
`compute_order` was built once and deliberately left out here — it exists to let a person
pin the arbitrary part, and nothing in this design reads it.)

**Cycles are refused at save time** (`find_cycle`), so the graph a writer walks is
normally acyclic. `build_chain` still handles a cycle rather than assuming it away — data
can reach a state the API would not accept, via a restore or a rename — and it does so the
same way it handles a formula that no longer parses: the cell is **blanked**. Leaving the
last computed value in place would be worse, because that value now reads as current.
"""
from functools import lru_cache
from typing import Optional

from src.core.exceptions import ValidationError

from .enums import COMPUTED_TYPES
from .formula import Formula, column_refs, evaluate, parse
from .models import Column

# A plan is built per *record written*, not per request — an import validates each row
# on its own — so parsing the same handful of stored formulas is the one cost here that
# scales with the data rather than with the schema. A `Formula` is immutable and `_eval`
# only ever reads its tree, so one parse can serve every row.
#
# Keyed on the source text, which is what makes it correct across lists: two columns
# with the same formula parse to the same thing, and an edited formula is a different
# key rather than a stale hit. Bounded because the key comes from stored config; 512 is
# far more distinct formulas than a deployment of this size has.
_PARSE_CACHE = 512


@lru_cache(maxsize=_PARSE_CACHE)
def _parse_cached(source: str) -> Formula:
    """`parse`, remembered. Raises exactly as `parse` does — `lru_cache` does not
    remember exceptions, so a formula that no longer parses costs a parse every time,
    which is the same as it cost before and rare enough not to matter."""
    return parse(source)


# One entry of an evaluation plan: the record key to write, the column it belongs to,
# and the formula to evaluate — or ``None``, meaning "this cell cannot be computed;
# blank it".
ChainStep = tuple[str, Column, Optional[Formula]]


def _sort_key(column: Column) -> tuple[int, str]:
    """How two calculated columns that do not depend on each other are ordered.

    Nothing depends on this being one order rather than another — columns the graph
    leaves unconstrained cannot affect each other, or they would be constrained. It
    exists so a plan is *stable*: without it the order would come from dictionary
    iteration, and a test that pins output would pass or fail on insertion order.

    The ``or`` guards are for columns that are not from the database: a `Column(...)`
    built in a test has no `position` yet, and sorting `None` against an integer raises.
    """
    return (column.position or 0, column.name or "")


def parse_formulas(columns: dict[str, Column]) -> dict[str, Optional[Formula]]:
    """Every calculated column of the list, keyed by record key, parsed.

    ``None`` for a formula that is missing or no longer parses. The language's bounds
    can tighten (they have), so a column saved under the old ones must not raise here
    and cost every other column its value — it costs its own cell, which is the rule
    `evaluate` already follows for a row it cannot compute.
    """
    parsed: dict[str, Optional[Formula]] = {}
    for key, column in columns.items():
        if column.type not in COMPUTED_TYPES:
            continue
        source = (column.config or {}).get("formula")
        if not isinstance(source, str) or not source:
            parsed[key] = None
            continue
        try:
            parsed[key] = _parse_cached(source)
        except ValidationError:
            parsed[key] = None
    return parsed


def build_chain(
    columns: dict[str, Column],
) -> tuple[list[ChainStep], dict[str, str]]:
    """An evaluation plan for one list, plus the column name -> record key map.

    The plan is every calculated column in dependency order: a column appears after
    every calculated column its formula reads, so a caller that walks the plan once,
    writing each result back into the record as it goes, computes a chain correctly in
    a single pass.

    The name map comes back with it because a formula is written in column *names*
    while a record is keyed by uuid, and resolving that per record would be per-record
    work for an answer that is the same for the whole batch.

    A column caught in a cycle is placed at the end with a ``None`` formula — see the
    module docstring for why blanking beats leaving the stale value.
    """
    key_of_name = {column.name: key for key, column in columns.items()}
    parsed = parse_formulas(columns)
    calculated = set(parsed)

    # Edges point from a column to the calculated columns it *reads*. A reference to a
    # plain number or text column is not an edge: it is already in the record before
    # any formula runs, so it constrains nothing.
    reads: dict[str, set[str]] = {
        key: {
            key_of_name[name]
            for name in (column_refs(formula) if formula is not None else ())
            if key_of_name.get(name) in calculated
        }
        for key, formula in parsed.items()
    }

    # Kahn's algorithm, taking every ready column each round in `_sort_key` order so
    # the plan is stable rather than dependent on dictionary iteration.
    pending = dict(reads)
    plan: list[ChainStep] = []
    while pending:
        ready = sorted(
            (key for key, deps in pending.items() if not deps & pending.keys()),
            key=lambda key: _sort_key(columns[key]),
        )
        if not ready:
            break  # everything left is in, or downstream of, a cycle
        for key in ready:
            plan.append((key, columns[key], parsed[key]))
            del pending[key]

    for key in sorted(pending, key=lambda key: _sort_key(columns[key])):
        plan.append((key, columns[key], None))

    return plan, key_of_name


def apply_formulas(
    plan: list[ChainStep], key_of_name: dict[str, str], values: dict
) -> dict:
    """Write every calculated cell of one record, in chain order. Mutates ``values``.

    Each step reads out of the same dict it writes into, which is the whole reason the
    plan is ordered: by the time ``total`` is evaluated, ``subtotal`` is already this
    write's value rather than the one the record arrived with.

    A cell whose formula cannot be computed is set to ``None`` rather than left alone.
    A calculated column is not a place data hides — if the formula cannot answer, the
    cell is blank, and that is true whether the reason is a missing operand, a broken
    reference, or a cycle. It matters more now that the value is stored: leaving the
    previous answer in place would put a stale number where a filter can find it.
    """
    for key, _column, formula in plan:
        if formula is None:
            values[key] = None
            continue
        inputs = {
            name: values.get(key_of_name[name])
            for name in column_refs(formula)
            if name in key_of_name
        }
        values[key] = evaluate(formula, inputs)
    return values


# -- the graph as the column service sees it, in names ---------------------------
#
# Saving a column is the one moment the graph is about to *change*, so these work over
# a name -> dependency-names map that includes the pending column, which does not exist
# in the database yet (a create) or still holds its old formula (an update).


def formula_edges(
    columns: list[Column], also: frozenset[str] = frozenset()
) -> dict[str, set[str]]:
    """Which calculated columns each calculated column reads, by name.

    Names, not keys: this is the form the save path has (a formula is written in names)
    and the form its error messages need — "'total' -> 'tax' -> 'total'" is a sentence a
    person can act on, three uuids are not.

    ``also`` names calculated columns that are not in ``columns`` — on a create, the
    column about to exist. Without it a formula already pointing at that name (left
    dangling by an earlier rename, say) would read as a reference to nothing, and the
    edge that closes a loop would be the one edge the check could not see.
    """
    calculated = {c.name for c in columns if c.type in COMPUTED_TYPES} | set(also)
    edges: dict[str, set[str]] = {}
    for column in columns:
        if column.type not in COMPUTED_TYPES:
            continue
        source = (column.config or {}).get("formula")
        try:
            formula = (
                _parse_cached(source) if isinstance(source, str) and source else None
            )
        except ValidationError:
            formula = None
        edges[column.name] = (
            {name for name in column_refs(formula) if name in calculated}
            if formula is not None
            else set()
        )
    return edges


def find_cycle(edges: dict[str, set[str]], start: str) -> Optional[list[str]]:
    """The loop ``start`` sits in, as the path back to itself, or ``None``.

    Only loops through ``start`` matter: the graph was acyclic before this save (every
    previous save was checked the same way), so the only cycle that can exist now is
    one the column being saved just closed. A column whose formula names itself is the
    one-step case and needs no special handling — it is a path of length one.
    """
    path: list[str] = []
    seen: set[str] = set()

    def walk(name: str) -> Optional[list[str]]:
        path.append(name)
        for dep in sorted(edges.get(name, ())):
            if dep == start:
                return [*path, start]
            if dep in seen:
                continue
            seen.add(dep)
            found = walk(dep)
            if found is not None:
                return found
        path.pop()
        return None

    return walk(start)
