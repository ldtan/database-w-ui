# Search: finding a record without reading every record

**Status: designed 2026-08-05, not built.** This is the largest remaining read cost in the
app by a wide margin, and unlike the two paging fixes before it (see
[capacity and performance](capacity-and-performance.md)) it cannot be settled by choosing a
better plan for the query that already exists. The query is asking something no index can
answer. This says what to ask instead, what that changes, and what it costs.

Cross-references: [data model](data-model-design.md) · [capacity](capacity-and-performance.md)
· [known gaps](known-gaps.md).

---

## The measurement

Against a seeded 100,000-record table (`paging_bench`, 20 columns), app CPU per request:

| request | count | page | total |
|---|---|---|---|
| plain listing | 11.5 ms | 3.8 ms | 15 ms |
| filter `status = 'open'` | 12.7 ms | 4.0 ms | 17 ms |
| search `q=alpha` (0 matches) | 268 ms | 501 ms | **769 ms** |
| search `q=Record 12345` (1 match) | 548 ms | 273 ms | **821 ms** |

Two things in that table are worth reading carefully, because together they say exactly where
the fix has to land.

**The count is always the full cost.** It has no `LIMIT` to stop it, so it reads and tests
every record whatever the term is. That half is unconditional.

**The page is expensive precisely when the term is rare.** A `LIMIT 25` seq scan stops as soon
as it has 25 rows, so a term matching half the table fills a page in the first fifty records and
is already fast. A term matching *nothing* reads all 100,000 to prove it — which is the 501 ms
above. Searching is therefore slowest exactly where a person is most likely to be doing it: a
specific name, a specific invoice number, or a typo.

An index that helps selective terms and does nothing for unselective ones is not a compromise
here. It is the shape of the problem.

## What runs today

`TableRecordService._apply_search` (`records/services.py:329-375`) builds one condition per column
and ORs them:

```sql
WHERE data ->> '3f2a…' ILIKE '%alpha%'   -- text column
   OR data ->> '9b41…' ILIKE '%alpha%'   -- number column
   OR data ->  '7c08…' ?| ARRAY['e1d2…', 'f4a9…']   -- lookup column
   ...
```

Reference columns (`lookup`, `user`, `group`) take the second shape because the cell stores a
**uuid** and the thing a person types is a **label** living in another table. `_reference_matches`
resolves label to uuids first — permission-filtered, capped at `_SEARCH_REF_MAX = 200` — and the
cell is then tested with `?|`, which is true for a single value and for one element of a
multi-valued cell alike.

None of it is indexable. Both halves are explained below.

## Three guarantees the current shape provides

Any replacement has to keep these, or it is a different feature wearing the same name.

1. **A query can never match a column uuid.** The keys of `data` are uuids. Matching against
   the blob — `data::text ILIKE '%…%'` — would put them in range, so typing a fragment of a
   uuid would return records for reasons nobody can see. This is why the current code matches
   per column rather than per record; the docstring says so outright.

2. **A reference is found by its label, and only if you may read the target.**
   `_reference_matches` requires `lookup` on the target table and then applies
   `record_view_filter` to its records. Search must not become a way to learn what is in a table
   you cannot read.

3. **Search runs in Postgres, over every record.** Filtering a fetched page in the browser
   would silently fail to find anything past it. This is not negotiable and nothing below
   changes it.

## Why the obvious fixes do not fit

**"Index the expression."** A functional index is built on a *specific* expression. The
expression here is `data ->> '<column-uuid>'`, and those uuids are per-table and unbounded — a
new column mints a new one at any time. There is no finite set of indexes to create. This is
the whole reason the problem is structural rather than a missing `CREATE INDEX`.

**"There is already a GIN index on `data`."** There is (`records/models.py:24`), and it is
`jsonb_ops` — containment and key existence. It serves `data @> {...}` in `_enforce_unique` and
`data ? 'key'` in two other places, so it stays. It does nothing for `%substring%`, which is not
a containment question.

**"`data::text ILIKE`, indexed with trigrams."** Fast, and it breaks guarantee 1 on the keys and
guarantee 2 on reference uuids in one move.

**Full-text search (`tsvector`).** Indexable, cheap, and the wrong semantics: it matches whole
words with prefix support, so `lph` would not find `alpha` and `2345` would not find
`Record 12345`. The search box in a spreadsheet-shaped app is a substring box. Changing it to a
word box is a bigger behaviour change than anything proposed here — worth revisiting only if
`pg_trgm` turns out to be unavailable (see the risks).

## The design: two derived columns on `table_records`

Add two columns maintained beside `data`, each derived **only from the record's own row**:

```python
# The record's own scalar values, joined — never a key, never a uuid.
search_text: Mapped[Optional[str]] = mapped_column(sa.Text)
# Every uuid this record's reference cells point at.
search_refs: Mapped[Optional[list[str]]] = mapped_column(ARRAY(sa.Text))
```

with

```python
sa.Index("ix_table_records_search_text", "search_text",
         postgresql_using="gin", postgresql_ops={"search_text": "gin_trgm_ops"}),
sa.Index("ix_table_records_search_refs", "search_refs", postgresql_using="gin"),
```

and the predicate becomes, for the whole record rather than per column:

```sql
WHERE (search_text ILIKE '%alpha%' OR search_refs && ARRAY['e1d2…', 'f4a9…'])
```

Both halves are index-served. `_reference_matches` is unchanged — it still resolves the label
under the target table's permissions, so guarantee 2 holds untouched; only the final cell test
moves from `data -> 'key' ?|` to `search_refs &&`.

### What goes into `search_text`

Values, never keys — which is what satisfies guarantee 1 without matching per column.
Specifically, for every column `in_shape()`:

- **text** — the value; **rich text is stripped to its plain text first** (see below).
- **number, date, time, datetime, link, choice** — the stored value as it is stored, so the
  ISO date a search currently matches keeps matching.
- **calculated** — the *stored* value, which is already an index maintained for exactly this
  kind of use (`records/services.py:1009-1013`). Search inherits its staleness rule and adds none.
- **checkbox** — nothing. Today `data ->> 'k'` yields `true`, so searching `true` returns every
  ticked record; that is an accident of JSONB rendering, not a feature.
- **lookup, user, group** — nothing. They go to `search_refs`.

Multi-valued cells contribute their elements joined, not their JSON serialisation. Values are
joined with `\n`. No lowercasing: `ILIKE` is already case-insensitive and `gin_trgm_ops`
supports it, so normalising would only create a second spelling to keep in step.

### Why deriving from the row alone is the load-bearing decision

The tempting version of this stores the reference **label** in `search_text` — then one column
serves everything and the query is a single `ILIKE`. It is also the version that fails.

A label lives in another row, often in another table. Renaming one target row would make every
record pointing at it stale, so the design would owe a cascade: find the pointers, rewrite them,
and get it right across a lookup chain, a user rename, a group rename, and a display-column
change. And a stale *search* index is not the benign thing a stale calculated index is. The
calculated index only ever misorders a row (`columns/backfill.py:8-13`); a stale search blob
makes a record **unfindable by the name it now has**, which reads as data loss.

Storing uuids instead removes the dependency completely. A uuid does not change when the row it
names is renamed, so `search_refs` is correct for as long as the pointer is correct — and the
pointer is part of the record, rewritten whenever the record is. There is no cascade to build,
no sweeper to write, and no window in which a rename hides a record.

That is the same discipline the clone remap and the calculated index already follow: derive from
what the row itself holds, and let the volatile part be resolved at read time. Here the volatile
part is `_reference_matches`, which already runs per query and stays that way.

## What this changes on purpose

Four behaviour changes, three of which are fixes.

- **Multi-valued cells stop matching their punctuation.** Today `data ->> 'k'` on `["a","b"]`
  renders `["a", "b"]`, so searching `", "` matches every multi-valued cell in the table. Joining
  the elements ends that.
- **Rich text stops matching its own markup.** Rich cells store sanitised HTML
  (`records/services.py:958`), so today searching `href` or `strong` matches every record with a
  link or a bold word. Indexing `richtext.to_text(value)` instead fixes it, and that helper
  already exists — it is what the length limits count, on the same reasoning that a limit is
  about how much someone wrote rather than how much markup their formatting generated.
- **Checkbox cells stop matching `true`.** Deliberate, per the table above.
- **Scalar matching becomes per record rather than per column.** This is the only one that is
  a loss rather than a gain, and it is smaller than it sounds: `_apply_search` already ORs
  across every column, so "any column matches" is what a caller can observe either way. Nothing
  in the API searches one named column — that is what `filters` is for, and filters are
  untouched.

The reference half is equivalent rather than changed, which is worth spelling out because it
looks like a widening. Today each reference column is tested against the uuids resolved for
*that* column; with `search_refs` the record is tested against the union. Those agree because
uuid spaces are disjoint by construction: a column targeting table X only ever holds X's uuids,
so a uuid resolved for column A cannot appear in column B's cell unless both target the same
table — in which case both are permitted or both are not, and `_reference_matches` returns the
same set for each.

## Where it is maintained

`_validate_data` (`records/services.py:911`) is the one seam every write passes through — the API,
a form, and the CSV import — and it already computes the calculated index at its end. Both
columns are computed in the same place, from the settled record, for the same reason: every
operand is in its final shape by then.

What can make them stale, and what handles it:

| cause | effect | handled by |
|---|---|---|
| record edited | none — rewritten with the record | the write path |
| target row renamed | none — `search_refs` holds uuids | *nothing needed* |
| column deleted, hidden, or `multiple` toggled | that cell's text lingers | nightly sweep |
| formula edited | inherited from the calculated index | existing recompute |
| rich-text sanitiser changed | old text lingers | nightly sweep |

The sweep is a new maintenance module in the exact shape of `maintenance/recompute_calculated.py`:
chunked, committed per batch, per-table advisory lock (its **own** namespace — see the collision
note at `backfill.py:62`), idempotent, and safe to kill. Everything in the table above is a
lingering *extra* match, never a missing one, which is what makes a nightly backstop adequate
rather than a correctness requirement — the same argument that licenses the calculated sweep.

## Migration and rollout

1. `CREATE EXTENSION IF NOT EXISTS pg_trgm` in its own migration, so a deployment that lacks
   the privilege fails there and not halfway through.
2. Add both columns, nullable. Nothing reads them yet.
3. Backfill through the chunked job, **not** in the migration and **not** in SQL. A SQL backfill
   is far faster and is the wrong trade: it would be a second spelling of the text-building rule,
   and any drift between it and `_validate_data` produces records that are silently unsearchable.
   One spelling, invoked from a job that can be run, killed and re-run.
4. `CREATE INDEX CONCURRENTLY` for both (`postgresql_concurrently=True` plus an autocommit block
   in Alembic — a plain `create_index` here would lock writes on `table_records` for the duration).
5. Switch `_apply_search` over. This is the only irreversible-feeling step, and it is not: the
   old predicate can be restored without touching data, since the columns are additive.

Step 3 before step 4 on purpose — building a GIN index over a column that is still NULL
everywhere and then filling it is strictly more work than the reverse.

## The risks, in the order they should be checked

Each of these can sink the design, and each is cheap to check before any code is written.

1. **`pg_trgm` on the deployment.** Render's Postgres supports it, but confirm on the actual
   instance rather than from the docs — the whole design assumes it. If it is unavailable the
   fallback is `tsvector`, with the word-versus-substring change described above, and that
   should be a decision rather than a discovery.

2. **The `ESCAPE` clause defeating the index.** `like_pattern` pairs with
   `.ilike(pattern, escape=LIKE_ESCAPE)`, and `LIKE_ESCAPE` is `\` — already Postgres's default,
   so the clause is redundant. If SQLAlchemy still emits `ILIKE :p ESCAPE '\'` and the planner
   declines the trigram index because of it, dropping the explicit escape on this one predicate
   costs nothing. **Verify with `EXPLAIN` before anything else**, because it is exactly the sort
   of detail that leaves the whole design measurably useless while looking correct.

3. **Index size against 1 GB of storage.** A trigram GIN index commonly runs one to three times
   the size of the text it covers. At 100k records this could plausibly be 30–80 MB for one
   table, against a gigabyte that holds roughly 500k records for the *whole* tenant
   (see [deployment](capacity-and-performance.md)). Measure `pg_relation_size` on the seeded
   table before committing. On this deployment it is entirely possible that disk, not time, is
   the binding constraint — which would not make the design wrong, but would make it something
   to enable per table rather than everywhere.

4. **Write cost.** Every record write gains a string build (cheap, pure Python over the row's
   own values) and GIN index maintenance (not cheap). The clone plan already identifies GIN
   maintenance on `ix_table_records_data` as the dominant per-insert cost, and this adds a second
   GIN index to the same table. Measure a bulk insert before and after; if it is bad, GIN's
   `fastupdate` and a tuned `gin_pending_list_limit` are the levers.

## Verification

- `EXPLAIN (ANALYZE, BUFFERS)` on the rewritten predicate at 100k records, for a term matching
  0, 1 and ~50,000 records. The last one should still choose a seq scan; a bitmap heap scan
  there would be the planner making a mistake, not a win.
- The four behaviour changes above each get a test that asserts the *new* behaviour and names
  the old one, since three of them are bug fixes and a future reader should not restore them.
- A test that a record whose reference target is renamed is still found by the new name and no
  longer found by the old — the property the uuid choice buys, and the one worth pinning against
  a later "optimisation" that stores labels.
- A test that a search cannot match a column uuid, and one that a lookup into a table the caller
  lacks `lookup` on contributes nothing. Guarantees 1 and 2, pinned.
- The sweep gets `test_maintenance_batching.py`'s treatment: chunk size forced below the row
  count, proving the last batch lands.
- Re-run the table at the top of this document and put the result in
  `capacity-and-performance.md`. If the numbers contradict anything asserted here, the claim
  gets corrected rather than left standing.

## What this does not fix

- **Unselective terms stay slow-ish**, and correctly so — see the opening. The count is what
  improves unconditionally.
- **Terms shorter than three characters** cannot use a trigram index and fall back to a scan.
  Acceptable: they match almost everything, so the page fills immediately and only the count
  suffers. Worth a minimum-length guard only if measurement says so.
- **`_SEARCH_REF_MAX = 200`.** A label matching more than 200 target rows is silently truncated
  today, and this design inherits that. It is a pre-existing correctness gap, not a new one, and
  it deserves its own fix — probably a subquery rather than a materialised table, which
  `search_refs && (SELECT array_agg(...))` accommodates without changing the index.
- **The target-table scan inside `_reference_matches`.** Resolving a lookup label runs
  `data ->> '<display-key>' ILIKE '%…%'` against the *target* table — the original unindexable
  shape, one table over. Searching a 100k table that looks up another 100k table therefore still
  scans 100k rows, just somewhere else.

  Fixing it needs a third derived column, `display_text`, holding just the display column's
  value — trigram-indexed for `_reference_matches`, and plainly indexed for the exact-label
  resolution a write does when a lookup cell arrives as a name rather than a uuid
  (`records/services.py:1348`, an `IN` over the same unindexable extraction, which every CSV
  import runs per batch).

  It is a clean follow-on rather than part of this change, because its staleness rule is
  different: changing *which* column is the display column invalidates a whole table at once,
  where the two columns above are invalidated only by their own record. That is still bounded
  and still a job, but it is a second argument to make rather than a corollary of the first.
  Worth doing second, and worth deciding on its own evidence.
