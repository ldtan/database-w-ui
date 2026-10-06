# Capacity and performance

What this app costs to run, measured rather than guessed, and what that means for the
deployment described in `deploy/render/README.md`.

Measured 2026-08-01 against the seeded school demo (`src.demo.school`: 9 lists, ~740
records, 16 users) on an AMD Ryzen 7 5700U. That core is roughly a cloud vCPU and
probably a little faster, so **treat every number here as a best case** — the same code
on Render is likely 10–30% worse.

> **Which plan is deployed.** Since 2026-08-11 the API runs on Render's `starter`: **0.5 CPU,
> 512 MB, always-on**. This document was written against the free plan's 0.1 CPU and still
> quotes it throughout, because the CPU-millisecond measurements are what was actually taken
> and they do not change with the plan — only the wall times derived from them do. **Where a
> figure below says "on the free tier", divide it by 5 for the plan in production.** That is
> arithmetic, not a second measurement; nothing here has been re-measured on `starter`. The
> memory ceiling did not move at all: both plans are 512 MB.

## Why CPU-time, and not latency

Latency on a laptop tells you nothing about a 0.1-CPU instance. **CPU-milliseconds per
request** does, because a plan with N CPU delivers N×1000 ms of CPU per wall second, and
the platform meters exactly that. A request costing X ms of CPU therefore cannot complete
faster than `X / (1000 × N)` seconds no matter how idle the box is, and the sustained
ceiling is `N × 1000 / X` requests per second.

The method, if you need to redo it after a change: start the app, then sample the uvicorn
process's `TotalProcessorTime` before and after a run of N identical requests and divide.
Do not measure wall time — argon2 runs on four threads by default, so it burns roughly
3× more CPU than its latency suggests, and that gap is the whole story below.

## What each operation costs

| Operation | App CPU |
|---|---|
| `GET /health` | ~0 ms |
| `GET auth/users/me` | 6 ms |
| `GET lists/{u}/columns/` | 14 ms |
| `GET lists/{u}/views/` | 20 ms |
| `GET lists/{u}/items/?limit=25` | 23 ms |
| `GET lists/{u}/items/?limit=100` | 29 ms |
| `GET lists/{u}/metrics/values` | 28 ms (see "Metrics" below) |
| `GET lists/{u}/items/?q=…` (search) | 106 ms |
| **Opening a list** (list + columns + items + views + forms) | **98 ms** |
| **`POST auth/tokens/login`** | **717 ms** |

Reading is cheap and scales the way you would hope: `limit=100` costs only 26% more than
`limit=25`, because records are one JSONB row each and a page is one query — the per-row
work is real but small. Search is the expensive read, at ~4× a plain page, because
`_reference_matches` resolves labels in other tables before it can match cells.

Everything else is a rounding error next to login.

## Metrics: the same total, on the app's CPU or on the database's

The 28 ms above is the school demo's ~740 records, which says nothing about a large list.
A metric is an aggregate over *every* record in scope, so its cost scales with the list —
and unlike the re-index below it runs on every request, on a timer, while someone is
looking at the strip.

`metrics/pushdown.py` compiles the common aggregate shapes into one SQL statement so
Postgres computes them, and falls back to streaming the records into Python for the rest.
Measured 2026-08-05, same machine and method, on a 100,000-record list with two number
columns (the scan's cap lifted so both paths cover the same records):

| Formula | Scan — app CPU | Compiled — app CPU | Compiled — Postgres wall |
|---|---|---|---|
| `SUM(qty)` | 1,991 ms | ~9 ms | 156 ms |
| `SUM(qty * price)` | 2,175 ms | ~3 ms | 196 ms |
| `COUNT()` | 1,884 ms | ~0 ms | 12 ms |
| `AVG(qty)` | 1,941 ms | ~0 ms | 130 ms |
| `SUM(qty) / COUNT()` | 2,003 ms | ~0 ms | 155 ms |

What the table is meant to settle:

- **The scan costs ~20 µs of app CPU per record**, linear, which is what
  `METRIC_ROW_LIMIT` (50,000 — so ~1 s) exists to bound. On the free tier's 0.1 CPU that
  cap is still ~10 wall seconds, which is why the strip is hidden by default and refreshes
  on its own slower clock (`METRIC_REFRESH_SECONDS`, default 60). Since live records became
  a server push that floor **throttles the invalidation** rather than setting a polling
  interval — the grid refreshes on the event, the strip at most once a minute however many
  events arrive — which keeps the same cost ceiling on a transport that can now signal far
  faster than the old timer ticked.
- **The compiled column is at the measurement floor**, not actually zero: Windows
  `time.process_time` resolves to ~15.6 ms, so these are batch averages. The honest
  reading is "indistinguishable from zero app CPU". The work moved to Postgres, which is
  the resource this deployment has spare — the database has never been the bottleneck.
- **It is the app's CPU that was the constraint, and it is gone for these shapes.** The
  row cap is now what a formula falls back *to*, not a property of metrics.

Which formulas compile, and why the rest deliberately do not, is in `pushdown`'s module
docstring and known-gaps §9. One consequence worth knowing before it is reported as a bug:
`numeric` is exact decimal where Python's floats are binary, so a compiled
`SUM(qty * price)` answered `106250024.4` where the scan answered `106250024.40004165`.
The compiled one is the more accurate of the two.

## Re-indexing a calculated column

The one operation whose cost scales with the *list* rather than the page. A calculated
column's value is stored so Postgres can filter and sort on it, and a schema change — a
formula edited, or a column one reads renamed or deleted — leaves that stored copy behind
until `POST /lists/{uuid}/columns/recompute` or the nightly `recompute_calculated` rebuilds
it. Nothing a person reads depends on it: reads recompute and overwrite the stored value, so
a stale index misplaces a row in a sorted or filtered view but never shows a wrong number.

Measured 2026-08-04, same machine and method, on a synthetic list with two calculated
columns per record (`qty * price` and a `CONCAT`), every row forced to rewrite:

| | 20,000 records | 50,000 records |
|---|---|---|
| App CPU | 0.67 s | 1.83 s |
| **CPU per record** | **0.034 ms** | **0.037 ms** |
| Peak Python memory over baseline | 2.8 MB | 2.8 MB |
| Longest contiguous event-loop block | — | 23 ms |

Three things that table is meant to settle:

- **It is linear**, so the per-record figure extrapolates. 5,000 records (the
  `FORMULA_INLINE_MAX_RECORDS` threshold) is ~0.2 s of CPU; 500,000 — what a 1 GB database
  holds — is ~18 s of CPU, about three minutes of wall time on the free tier's 0.1 CPU.
- **Memory is flat, not proportional.** 500-row batches are read, rewritten and released,
  so a list ten times longer costs the same 2.8 MB. This is why there is no row ceiling:
  a big list makes a slow job, not a failing one.
- **The event loop is never blocked long.** Every batch awaits the database, so the longest
  stretch of uninterrupted Python is ~23 ms — comfortably inside the health check, so the
  instance is not restarted underneath a running re-index.

A pass over a list that is *already* current costs ~0.026 ms per record and writes nothing,
because rows whose values already agree are skipped rather than rewritten with identical
bytes. That is what makes running the nightly sweep unconditionally cheap.

Two measurement traps worth recording, since both produced numbers that were wrong by an
order of magnitude before they were caught. `tracemalloc` traces every allocation and
inflates CPU roughly 10×, so memory and timing cannot be sampled in the same run. And an
event-loop watchdog built on `await asyncio.sleep(0)` is a busy spin: it re-schedules
immediately, saturates the process, and `process_time()` then charges all of it to the job
being measured. Sleep a real interval and measure the overshoot instead.

## Converting a column's type

The one operation that deliberately blocks writers while it runs, and the reason it is
allowed to is that it is the only way to make it correct. A conversion is two calls: `preview`,
which converts every cell in memory and writes nothing, and `apply`, which does the
whole job — read, convert, write both the new value and the original, retype the column
— inside **one transaction**. It used to stage the work in a hidden column between the
two, and the gap that opened is what lost writes (known-gaps §11).

Measured 2026-08-15, same machine and method, `text` → `number` over a list whose every
cell converts:

| | 5,000 records | 20,000 records | 50,000 records |
|---|---|---|---|
| `preview` app CPU | 78 ms | 297 ms | 859 ms |
| `apply` app CPU | 125 ms | 438 ms | 1,219 ms |
| **`apply` wall — the list is locked this long** | **0.3 s** | **1.4 s** | **3.5 s** |
| CPU per record (apply) | 25 µs | 22 µs | 24 µs |

Linear, so the per-record figure extrapolates, and the two numbers to read are the last
two rows.

**Roughly 24 µs of app CPU per record**, about two-thirds of a calculated re-index's
~0.035 ms — unsurprising, since both walk every record once and do a small amount of
Python per cell, and this one has no formula to evaluate. Divide the CPU column by the
plan's share to get Render figures: on `starter` (0.5 CPU) the 50,000-record apply is
~2.4 s of CPU.

**The wall time is the number that matters**, because it is how long every other writer
to that list waits. The apply holds row locks on every record it has written until it
commits, which is the price of the whole thing being atomic — and it is the right price
for a point-of-no-return schema change, but it is a real cost and not a rounding error.
Wall exceeds CPU by roughly 3× here because most of the gap is Postgres doing the
`jsonb_set` updates; that side of the box has capacity to spare, so the ratio should
hold rather than worsen on the deployed plan.

**`ROW_CEILING` stays at 50,000 on the strength of this.** A ~3.5 s lock locally implies
something in the range of 7–10 s on `starter`, which is tolerable for an operation a
person deliberately triggered and confirmed, on a list size the deployment has not come
close to. If a list that big ever becomes plausible, the fix is a job that can hold the
lock outside a web request — not a return to staging, which is what the window bug was.

Two things this does **not** cost. The preview is free to abandon: it creates no column,
writes no value and reserves no name, so closing the dialog makes no request at all.
And a refusal — a failed `required`/`unique` gate, or a record written by somebody else
mid-apply — rolls back the entire transaction, so a conversion either happens or leaves
the list exactly as it was.

## Cloning a list with its records

The other operation that scales with the list rather than the page, and the only one that
was ever a threat to the instance rather than to a response time. `POST /lists/{uuid}/clone`
with `with_records: true` used to load every source record as an ORM instance, build a
second instance for each copy, and hold both until a single flush at the end.

Measured 2026-08-04, same machine, on a synthetic list of three columns at ~1.9 kB per
record, with RSS sampled every 10 ms from a separate thread *during* the call — reading it
afterwards measures what survived garbage collection, which for the old version understated
the peak by about 5×:

| | 20,000 records | 100,000 records |
|---|---|---|
| Peak RSS over baseline, before | 174 MB | 859 MB |
| **Peak RSS over baseline, after** | **0.6 MB** | **0.6 MB** |
| Wall time, before | 4.3 s | 23.7 s |
| **Wall time, after** | **1.9 s** | **9.9 s** |
| Throughput, after | 10,700 rows/s | 10,100 rows/s |

- **This was an availability bug, not a slow endpoint.** 859 MB of peak growth cannot happen
  on a 512 MB instance; the process is killed and every user goes down with it. Even 20,000
  records put it at ~320 MB against a ~146 MB idle baseline. One request, from any user who
  owns a list, was enough.
- **Memory is now flat, not merely smaller** — the same 0.6 MB at both sizes, and the same
  at any size. The source is keyset-paged and each batch is copied by a single
  `INSERT ... SELECT` that remaps the column-uuid keys with `jsonb_object_agg`, so record
  data never becomes a Python object at all. Only the ids and the new uuids cross into the
  app.
- **It also got 2.4× faster**, which is a side effect rather than the point: what was
  removed is a JSONB decode and re-encode per row, plus two ORM instances. On a 0.1-CPU
  instance the win is larger than it looks here, because nearly all of the remaining time
  is Postgres rather than app CPU, and app CPU is what the platform meters.
- **The batches deliberately do not commit as they go**, unlike the maintenance jobs that
  share this shape. Those can be interrupted because their half-done state is one the data
  was already in; a half-populated clone is not. Keeping the batches in one transaction is
  what leaves a failed clone with nothing behind.

Wall time is still linear, and its dominant term is now `ix_list_items_data`, the GIN index
on `data`: every inserted row has to be indexed into it, which costs more than the heap
insert and the key remap together. Extrapolating, 500,000 records — what a 1 GB database
holds — is roughly 50 s here and correspondingly longer on the free tier. That is a slow
request, not a broken one, and Render allows a 100-minute request, so there is no case yet
for moving the work to a background task. Deferring it would not have helped anyway: the
same loop in a background task holds the same memory in the same process, and would have
OOMed after telling the caller it had succeeded.

## Deleting records in bulk asked per record

`POST /lists/{uuid}/items/bulk-delete` is not memory-shaped like the two above — its cost is
round trips. For each uuid it resolved the record, then asked whether the caller could delete
it, which fetched their group membership and their grants afresh every time; then the service
resolved every uuid a second time to soft-delete it one row at a time.

Statements per request, counted at the driver with a `before_cursor_execute` listener,
2026-08-05:

| uuids in the batch | before | after |
|---|---|---|
| 1 | 6 | 5 |
| 5 | 18 | 5 |
| 20 | 63 | 5 |
| 100 | 303 | 5 |

Three per record, plus a fixed three — now five, flat. The batch is resolved once by
`effective_actions_bulk`, which reads the groups and grants once for the whole set, and the
delete is a single `UPDATE ... WHERE uuid IN (...)`.

Two things are worth carrying forward from this one:

- **The endpoint had no cap**, so the caller chose how much of this to do; it is now 500, the
  same limit the other bulk bodies carry. Round-trip latency is what a cap is really bounding
  here — on a hosted database each of those 303 statements is a network hop, and the count was
  the caller's to set.
- **`effective_actions_bulk` was already only *claiming* to be bulk.** It resolved the rows in
  one query, then dropped its last reference to them — and the identity map holds instances
  weakly, so they were collected and the chain walk that follows selected every one back by
  id. The bulk query was pure overhead on top of the per-row queries it was meant to replace.
  Nothing about this is visible from the results; it needed a statement count to see, which is
  why there is now a test that asserts the count does not follow the batch size. The other
  caller, the `/effective` batch endpoint behind the grid's per-row control gating, gets the
  same fix for free.

## Import is bounded now; export always was

These two are worth reading together, because they look symmetrical and are not.

**Export is fine, and was already built this way.** It opens its own session, selects only
the `data` column through Core so no rows enter the identity map, streams them from a
server-side cursor with `yield_per=1000`, and yields CSV a row at a time into a
`StreamingResponse`. Labels resolve in batches of 500 on that same session — deliberately,
since a second session would have each export holding two pooled connections and enough
concurrent exports would deadlock the pool — with the resolver's cache evicting oldest-first
past 50,000 entries. Memory is flat in the size of the list. `limit: null` means "the whole
list", but the whole list streams, so what an unbounded export costs is time and bandwidth,
not the instance. Nothing to fix.

**Import was the opposite on every count.** It read the upload whole with `await file.read()`,
decoded it whole, and for JSON parsed it whole again — so the memory cost was set by whatever
was sent, before anything had looked at it. Then it inserted rows through the item service,
several round trips apiece, with no ceiling on how many.

Both ends are now capped: `MAX_UPLOAD_BYTES` (8 MB), checked *while* the upload is read in
64 kB pieces rather than after, and `IMPORT_MAX_ROWS` (1,000). Both are settings rather than
constants, and both importers — list records and user accounts — read the same two, since the
reasoning is identical either side. The batch sizes around them stay constants: they are
shaped by round-trip latency and memory, not by anything a deployment has an opinion about,
and their optimum is flat enough that there is no better value to find.

The ordering is the point. A row cap on its own bounds the database work and nothing else,
because the file has to be read before its rows can be counted — a single 500 MB row passes a
row cap and still kills the instance. And the row check runs before the first insert rather
than as it goes, so a file one row over the limit imports *nothing*. That is what makes
"split it and import each part separately" advice someone can act on; the alternative leaves
them reconciling which thousand rows already went in.

## Two indexes for the questions actually asked

Both tables were indexed for a question nobody was asking.

Measured 2026-08-05 on a scratch database of 100,000 records spread over 50 lists (so one
list is 1/50th of the table, which is what a real workspace looks like) and 50,000 grants
over 2,000 users and 50 groups. `EXPLAIN (ANALYZE, BUFFERS)`, same session and same data
with the indexes dropped and then created:

| | before | after |
|---|---|---|
| Grid page — one list's live records, id order, `LIMIT 100` | 5.9 ms, 553 buffers | **0.29 ms** |
| Collection filter — grants for one principal and resource type | 1.0 ms, 44 buffers | **0.20 ms**, 4 buffers |

**`list_items (list_id, id) WHERE deleted_at IS NULL`.** The grid orders by `id` —
`apply_sorts` appends it as the tiebreaker and it is the whole ordering when no sort is
chosen. Given a `list_id` index that offers no order, the planner preferred the *primary
key*, which is already in id order, and simply discarded the rows belonging to other lists:

```
->  Index Scan using list_items_pkey on list_items
      Filter: ((deleted_at IS NULL) AND (list_id = 1))
      Rows Removed by Filter: 9850
```

9,850 rows read and thrown away to return 100. What that cost tracks is not how large the
list is but how *sparse* it is in the table — a workspace with many lists is worse per list,
not better — and `OFFSET` paging multiplies it per page, since page 50 discards fifty times
as much. With `id` trailing `list_id` the scan is already in the right order and stops after
100 rows.

**`permission_grants (user_id, resource_type)` and `(group_id, resource_type)`, each partial
on its principal.** Every collection-visibility filter asks what one principal may see. The
existing unique indexes *can* serve that — `resource_type` leads them and the filters do
constrain it — but that is the entire usable prefix and it has five values, so answering
meant walking a whole fifth of the index:

```
->  Bitmap Index Scan on uq_permission_grants_user   (Buffers: shared hit=33)
      Index Cond: ((resource_type = 'workspace') AND (user_id = 1234))
```

33 buffers to return nothing. Principal-first makes it 2. The resource-led index stays: grant
management still asks the original question, "who may touch this resource".

Neither index is free on writes, and `list_items` now carries two on `list_id`. The plain one
stays because the bin asks the opposite question — soft-deleted rows — which the partial index
deliberately excludes. Against the GIN index on `data`, which every insert already pays for,
both are noise.

**When the planner actually takes it.** Re-measured 2026-08-05 against a second dataset, this
time varying how sparse the list is in the table and A/B-ing the index in place:

| list's share of the table | index chosen | buffers | time |
|---|---|---|---|
| 1% | **`ix_list_items_list_id_id`** | 44 | 0.04 ms (from 2.81 ms) |
| 5% | `list_items_pkey`, 1,882 rows discarded | 408 | 0.39 ms |
| 49% | `list_items_pkey`, 109 rows discarded | 71 | 0.06 ms |

The index is ignored unless the list is sparse, and that is the planner being right rather
than wrong: reaching rows through it costs a random heap fetch each, which only beats walking
the primary key in order once there are enough foreign rows to skip. Sparsity is the variable
that decides it — which is the same thing the paragraph above says, now with the other side of
the curve measured. A workspace with many lists is where this index earns its keep; a database
with one big list will never use it, and does not need it.

## Paging by cursor, because `OFFSET` cannot be indexed away

Measured 2026-08-05 on a scratch database of 200,000 records: one list holding 100,000 (99,009
live), interleaved 50/50 with twenty other lists, `data` averaging 533 bytes, 120 MB heap, every
current index in place.

One page of 100 in the default order:

| page | `skip` | | `after` | |
|---|---|---|---|---|
| | time | buffers | time | buffers |
| 1 | 0.08 ms | 40 | 0.08 ms | 43 |
| 50 | 3.31 ms | 1,800 | 0.08 ms | 42 |
| 200 | 12.16 ms | 7,180 | 0.08 ms | 44 |
| 500 | 30.85 ms | 17,942 | 0.08 ms | 44 |
| 990 | 68.25 ms | 35,526 | 0.08 ms | 44 |

**No index can fix this, which is the whole reason the endpoint needed a second way to page.**
`OFFSET` is applied by the Limit node *above* the scan, so the scan is obliged to produce
complete rows — heap fetches included — for every row that is then discarded. The planner
knows it, and at depth it deliberately refuses `ix_list_items_list_id_id` in favour of the
primary key: reaching 99,000 doomed rows through the partial index would mean 99,000 random
heap fetches, where the primary key walks them in order. It picks the cheaper way to do
unnecessary work. A cursor is the only thing that stops the work being requested, because
`id > :after` moves the starting point *into* the scan.

Through `ListItemService.list`, which is what the endpoint actually spends:

| page | `skip=` | `after=` | |
|---|---|---|---|
| 1 | 3.5 ms | 3.9 ms | 0.9× |
| 50 | 7.0 ms | 5.6 ms | 1.3× |
| 200 | 15.7 ms | 4.6 ms | 3.5× |
| 500 | 31.7 ms | 4.1 ms | 7.8× |
| 990 | 60.2 ms | 3.9 ms | **15.6×** |

**The finding that made this worth building** was not the 100k list. Deep pages of a huge list
are a place few people go. But the cost is driven by how expensive the *discarded* rows are to
produce, and both a filter and a sparse list make them more expensive:

| | page 1 | page 50 | page 200 |
|---|---|---|---|
| filtered view (`status = 'open'`, 49,504 rows), `skip` | 0.20 ms | **99.13 ms** | 105.42 ms |
| filtered view, `after` | 0.14 ms | 0.18 ms | 0.20 ms |
| 10,000-record list at 5% of the table, `skip` | 0.42 ms | **26.79 ms** | — |
| 10,000-record list, `after` | 0.36 ms | 0.38 ms | — |

Page 50 of a ten-thousand-record list is an ordinary place for an ordinary person to be, and
it costs 27 ms of the app's scarcest resource to get there. That is the case this fixes.

**It is also a correctness fix.** `skip` names a position, so a record deleted ahead of that
position pulls every later one back a place and the next page steps over one the caller never
saw. Ids only grow, so an insert cannot cause this — a delete can, and a bin any editor can
fill makes it routine rather than a race. `tests/test_lists_flow.py` pins the difference:
after one delete, `skip=3` returns `04, 05, 06` while `after=` the same cursor returns
`03, 04, 05`.

**What it does not fix, deliberately.**

- **Sorted or grouped listings.** `after` is refused there with a 400 rather than approximated,
  because `id > :after` only names a position when `id` is the ordering. It would not have
  helped anyway: ordering by a data column is an unindexed expression, so Postgres sorts the
  whole matching set on *every* page — 105 ms at page 1, rising only to 268 ms at page 990. That
  path is slow everywhere, not slow deep, and what it needs is an expression index (still
  entangled with the `multiple` toggle; see `known-gaps.md`).

  Ordering by a **reference** column costs one more thing on top of that: it LEFT JOINs a
  derived table of the target's labels (`items/references.py`), so the target list is
  scanned once per query. Only a request that actually sorts or groups by such a column
  pays it — the count and an unordered listing build no sources at all — and because the
  join is on the unique `uuid` it adds columns to a row and never rows to the result, so
  the page size and `X-Total-Count` cannot drift apart. The derived table is a simple one,
  so Postgres flattens it and still gets to choose between an index nested loop and a hash
  join on the real relative sizes of the two lists.
- **`X-Total-Count`.** A flat 12–13 ms against this list, paid on every page load whichever way
  the page was fetched. Once the page itself is ~4 ms, the count is three-quarters of the
  request — it was the largest single thing this endpoint did, and is dealt with in the next
  section.
- **The grid.** Still pages with `skip`, and should: PrimeReact's paginator offers numbered page
  links, and page 7 is an offset by definition. `after` serves API clients and anything built
  as "load more". Converting the grid is a UX decision about losing jump-to-page, not a
  performance one.

## The total was recounted on every page turn

`count(*)` cannot be made cheaper. Measured on the same 200,000-record database, the count
of the 100k list runs an **index-only scan** on `ix_list_items_list_id_id` — 269 buffers,
zero heap fetches after a vacuum, which is as good as the plan gets. It still takes 12 ms,
because it has to visit all 97,029 matching index entries and that is ~0.12 µs each. It is
CPU, not I/O:

| | buffers | heap fetches | time |
|---|---|---|---|
| before `VACUUM` | 3,131 | 15,734 | 16.5 ms |
| after | **269** | **0** | 12.5 ms |

Removing 94% of the buffer traffic bought 24% of the time. There is no index and no query
rewrite left — three spellings of the same count (over the whole-row subquery `count()`
actually builds, over an id-only subquery, and flat with no subquery at all) all produced the
identical plan and the same 12–13 ms, so the whole-row projection that looked like an obvious
culprit was being flattened away all along.

What was actually wasteful was **asking**. The total rode in a header on the page request, so
turning a page re-asked a question whose answer cannot have changed — the count ignores
`skip`, `limit`, the sort and the grouping by construction. Page 2 of a search made the server
count every matching record a second time to return the number it had just returned.

So the count moved to `GET items/count`, taking only the parameters that change which records
match — the view, the search, the filter — and `GET items/` gained `total=false`. The grid
fetches the count under a key holding exactly that scope (`recordCountKey`, deliberately the
same shape as `metricValuesKey`), so paging, sorting and grouping all read the cached answer:

| one page turn on the 100k list | before | after |
|---|---|---|
| plain listing | 15.5 ms | **3.3 ms** |
| under a search | 769 ms | **501 ms** |

First load is unchanged — it still costs a page and a count, now as two requests instead of
one. Everything after it is the page alone.

**Sizing this honestly:** the count is proportional to *matching* records, so it is 12.2 ms at
97,029 rows, 2.4 ms at 10,000 and 1.8 ms at 1,980. On the school demo it is under 2 ms and
this change is unmeasurable. It matters at a size the deployment has not reached.

**The search numbers above are the real finding.** Counting is a rounding error next to what
search costs on a large list:

| request on the 100k list | count | page | total |
|---|---|---|---|
| plain | 11.5 ms | 3.8 ms | 15 ms |
| filter `status = 'open'` | 12.7 ms | 4.0 ms | 17 ms |
| search `q=alpha` (0 matches) | 268 ms | 501 ms | **769 ms** |
| search `q=Record 12345` (1 match) | 548 ms | 273 ms | **821 ms** |

`_apply_search` runs an `ILIKE` against one JSONB extraction per column, OR'd, over every row.
The GIN index on `data` is `jsonb_ops` — containment and key existence — so it does nothing for
pattern matching. Removing the duplicate count is the only part of that this change touches;
the scan itself remains, and is now by a wide margin the most expensive read in the app.

Fixing it is a design problem rather than a tuning one, which is why it is not done here.
`pg_trgm` indexes an *expression*, and the expressions are `data ->> '<column-uuid>'` where the
uuids are per-list and unbounded — there is no finite set of indexes to create. The realistic
shapes are a maintained `search_text` column or a generated column with an immutable extractor,
and both turn per-column matching into matching over the row's values, which is a deliberate
behaviour change: `_apply_search` matches per column specifically so a query cannot hit a
column *uuid* instead of something a person can read. Reference columns complicate it further,
since they resolve labels to uuids first and match with `?|`, which no trigram index touches.

That design is now written up in **[search-design.md](search-design.md)**, which settles the
uuid problem by indexing values without keys and the reference problem by indexing the uuids
rather than the labels — so nothing it maintains depends on a row other than its own. Not built.

## Archiving records, and the fraction at which it starts to pay

Every fix above makes a question cheaper to ask. Archiving is the first one that asks about
fewer rows: `archived_at` drops a record out of `ListItemService._select`, and
`ix_list_items_list_id_id` is partial on both lifecycle flags so the index follows the
predicate. What that is worth is not obvious in advance, because the planner decides whether
to *use* the partial index, and it only does so when reaching rows through it beats walking
the primary key in order.

Measured 2026-08-06 against `paging_bench` — the 300,000-row table, list 1 holding 97,029 live
records interleaved 50/50 with twenty other lists, 20 columns. Each fraction is
`VACUUM (ANALYZE)`-ed then `EXPLAIN (ANALYZE, BUFFERS)`-ed twice, second run reported. The
search is a term matching **nothing**, which is the expensive case (a `LIMIT` cannot stop a
scan early when there is nothing to find):

| archived | live | grid page | `items/count` | search (0 matches) | search's index |
|---|---|---|---|---|---|
| 0% | 97,029 | 0.11 ms | 12.6 ms | 104 ms | `ix_list_items_list_id` |
| 50% | 49,509 | 0.34 ms | 12.1 ms | **141 ms** | `ix_list_items_list_id` |
| 60% | 39,609 | 0.26 ms | 5.8 ms | **41 ms** | `ix_list_items_list_id_id` |
| 70% | 29,709 | 0.07 ms | 4.2 ms | 32 ms | `ix_list_items_list_id_id` |
| 80% | 19,809 | 0.10 ms | 4.0 ms | 30 ms | `ix_list_items_list_id_id` |
| 90% | 9,905 | 0.08 ms | 1.7 ms | 13 ms | `ix_list_items_list_id_id` |
| 99% | 1,981 | 0.13 ms | 0.5 ms | 3.7 ms | `ix_list_items_list_id_id` |

**The count is the clean win and it is immediate.** It falls with the live row count from the
first record archived — 25× by 99% — because it was already an index-only scan whose whole
cost is visiting matching entries, and archiving removes entries.

**Search has a cliff, not a slope, and it sits between 50% and 60% archived.** Below it
archiving buys *nothing*: the planner stays on the plain `list_id` index, still reaches every
row of the list, and the `archived_at IS NULL` test is far too cheap to matter next to the
ILIKE fan-out it precedes — 141 ms at 50% archived against 104 ms at none is noise, not a
regression. Above it the planner switches to the partial index, and search steps down 3.4× in
one move and then improves roughly with the live count. So **half a list archived is the
threshold that matters for the app's most expensive read**, and anything less is not worth
doing for search alone.

**The grid page neither gains nor suffers**, which is the result to keep in mind when someone
proposes archiving to "speed up the list". It is 0.1 ms throughout: already flat, already
independent of list size, and the operation people perform most.

Two caveats, both measured:

- **A list whose rows sit at one end of the table can get slower.** Repeating this against
  list 23 — 100,000 rows appended after the other 200,000 rather than interleaved — the page
  never leaves the primary key and goes **129 ms → 208 ms** from 0% to 99% archived, because
  the scan must walk further through discarded rows to find 100 live ones. The 129 ms baseline
  is the pre-existing sparsity cost described in "Two indexes for the questions actually
  asked"; archiving makes it worse rather than better. Sparsity decides this, as it decides
  everything else about that index.
- **The heap and the GIN index on `data` do not shrink.** The rows are still there. That is
  the ceiling of the flag approach: a list heading for millions of records outgrows it, and
  the version without the ceiling is a separate `list_items_archive` table that the rows
  physically move to. Every read already funnels through `visible_stmt`, so that migration
  stays open.

### When to archive

Archiving only helps the operations whose cost **scales with the live row count** — search,
`count(*)`, filters and sorts, metrics that fall back to the Python scan, and deep `skip`
paging. It does nothing for the default paged listing. So the question is not "is this list
big" but "is this list big *and* is anyone doing those things to it".

Thresholds below are stated on the free tier's 0.1 CPU, where wall time is ~10× the CPU figures
above — that is how they were derived, and they are left in those terms so the table and the
measurements stay in one unit. On the deployed 0.5 CPU, every *wall* figure in it is five times
smaller and the app-CPU thresholds move up correspondingly; the Postgres-side ones do not move
at all (see the second qualifier below).

| Live records in one list | What starts to hurt |
|---|---|
| < 10k | Nothing. Do not archive for performance. |
| 10k–25k | Search crosses ~1 s of wall time (~7.7 µs per row on a 20-column list). Deep `skip` paging is already 27 ms at page 50 of a 10k list. |
| 25k–50k | Metrics that do not compile cost ~20 µs a record — ~1 s of CPU, ~10 s of wall — and 50k is `METRIC_ROW_LIMIT`, where they truncate. |
| 50k–100k | Search is 4–8 s of wall time; `count(*)` ~120 ms; a sort or a group re-sorts the whole matching set on *every* page (~105 ms of Postgres at 100k). |
| 100k+ | Search is unusable on the free tier, and ~1 s on the deployed Starter — better, but a second of a single event loop that everyone else queues behind. Archiving is maintenance, not tuning. |
| ~500k | 1 GB of database storage is full at ~1.9 kB a record — a limit archiving does not move, since the rows stay. On `basic-256mb` this one is buyable: storage is provisioned separately (~$0.30/GB-month) and can be raised. |

Three qualifiers belong with that table:

- **Column count drives search cost as much as row count.** Search ORs one `ILIKE` per column,
  so a 40-column list costs roughly twice per row what a 20-column one does. 25,000 assumes
  about twenty.
- **A bigger instance buys its CPU multiple on the app-CPU half only** — search and non-compiled
  metrics — and nothing on the Postgres-side sorting and counting. The Starter upgrade bought 5×
  (0.1 → 0.5 CPU) on the first half and exactly nothing on the second: `count(*)` at ~120 ms and
  the ~105 ms re-sort per page are Postgres's own work on its own instance, and they are
  unchanged. Those thresholds do not move with the web plan.
- **A workspace with many lists is worse per list, not better**, since sparsity is what decides
  whether the planner takes `ix_list_items_list_id_id` at all.

`VITE_ARCHIVE_HINT_THRESHOLD` (default 25,000, `0` disables) is where the first row of that
table is wired into the app: past it, a list offers the archive to anyone holding `delete`,
and only when the record count is the whole list's rather than a filtered subset's.

## Login was 30× a page load, and most of it was waste

The 717 ms breaks into three parts:

- **argon2 verify — 316 ms.** `PasswordHasher()` with library defaults: t=3, m=64 MiB,
  p=4. Expensive on purpose; that is what a password hash is for.
- **RS256 sign — 272 ms.** Not on purpose. `create_access_token` passed `PRIVATE_KEY` —
  the PEM *string* — to `jwt.encode`, so python-jose re-parsed a 4096-bit RSA key on
  every single call. Handing it a `jose.backends.RSAKey` built once at import costs
  **6 ms** instead. Same signature, same algorithm, 45× cheaper.
- **~129 ms** of session, DB round trips and framework.

The second one also charged every **token refresh**, which each active user does every
`ACCESS_TOKEN_EXPIRE_MINUTES` (15). So a logged-in user who never touched anything still
cost ~290 ms of CPU per quarter hour.

## Two failure modes that arrive before you run out of CPU

**1. A login froze the whole process.** `verify_password` is synchronous and was called
from an `async def` route, so argon2 ran *on the event loop*. Uvicorn runs one worker, so
there is no second thread to absorb it. Measured, with an unrelated cheap request
(`GET auth/users/me`) running alongside:

| Concurrent logins | p50 of the unrelated request |
|---|---|
| 0 | 34 ms |
| 1 | **459 ms** |
| 2 | 639 ms |
| 4 | **1250 ms** |

One person signing in made everyone else's app 13× slower. On 0.1 CPU, multiply again.

**2. Concurrent logins exhaust the RAM before the CPU.** The process idles at ~146 MB,
and every in-flight argon2 hash adds **64 MiB** on the default parameters. Six at once
exceeds 512 MB and the instance is killed. This matters more than it sounds, and the $7
Starter upgrade did **not** help: Free and Starter are both 512 MB. It remains the sharpest
edge in the deployment, and the only things that move it are the argon2 parameters or a 2 GB
Standard instance at $25.

## What this means per plan

Wall-clock floor per request with no contention at all:

| | Free (0.1 CPU) | **Starter (0.5 CPU) — deployed** | Standard (1 CPU) |
|---|---|---|---|
| Open a list | ~1.0 s | ~0.20 s | ~0.10 s |
| Poll / cell edit | ~230 ms | ~46 ms | ~23 ms |
| Login — before the fixes | ~7.2 s | ~1.4 s | ~0.72 s |
| Login — after the fixes | **~0.67 s** | ~0.13 s | ~0.07 s |
| Steady-state active users | ~50 | ~245 | ~490 |

"Active user" means a tab open on a list: one poll per `VITE_LIVE_REFRESH_SECONDS`, a
navigation every couple of minutes, a token refresh every 15 — about 1.5 ms of CPU per
second each at the 60s interval this row was computed against, taken at a 60% utilisation
target because a single event loop queues badly above that.

**Production runs 15s as of 2026-08-18, so halve that row.** The two floors absorb most of
the change — the count stops at 30 and the metric strip stays at 60 — so only the records
page actually quadruples, taking an active tab to ~2.9 ms of CPU per second and the
steady-state column to roughly ~25 / ~125 / ~250. Arithmetic from the per-endpoint costs
above; nothing was re-measured. Note this row was never the binding limit anyway (see the
login burst below), and it counts tabs someone is *using* — `useLiveList` still stops
polling on a hidden tab and after 5 idle minutes on a visible one.

**Since live records became a server push (2026-08-22), that whole row is a worst case
rather than the normal one, and it has not been re-measured either.** An open list holds
an SSE stream and refetches when the server says its records moved, so a quiet list costs
an active tab *nothing* between changes instead of a request every 15 seconds — the
interval above is only what the app falls back to when the stream cannot be held open.
Two caveats keep it from being a free win. A **busy** list now signals faster than the
timer ever ticked, which is why the count and metric floors were kept through the swap and
why they throttle invalidations rather than only setting intervals. And a held stream is
much cheaper than a poll but not free: it is an open socket and an asyncio task for up to
`SSE_MAX_STREAM_SECONDS`, writing a heartbeat every `SSE_HEARTBEAT_SECONDS` and
reconnecting when the server closes it. It holds **no pooled database connection** — that
is the whole reason the route is mounted outside the lists router, and
`tests/test_live_events.py` pins it — so the fifteen-connection pool is not what bounds
concurrent viewers here. The two guardrails still apply, so an abandoned tab still costs
nothing on either transport.

That steady-state row was never the real limit, though. **The limit was a login burst.**
Thirty people arriving at once cost 30 × 717 ms = 21.5 s of CPU, which on the free tier
meant **3.6 minutes** during which the app served nobody. After the fixes below the same
burst costs 2.0 s of CPU — about **20 seconds** on the free tier, **4 seconds** on the
deployed Starter, and nobody else's requests queue behind it. That single change is still
worth more than either paid upgrade: the login fix took 21.5 s to 2.0 s (11×), where
Starter takes wall time to a fifth. Note the burst's *memory* cost is untouched by both.

## Data volume is not a constraint and probably never will be

Records are ~**1.9 kB each** including indexes (the JSONB `data` column itself averages
808 bytes). The entire school demo is **11 MB**. A 1 GB database — what both the free
plan and Basic-256mb give you — therefore holds on the order of **500,000 records**.

So the paid database plan buys persistence and backups, not headroom: the free instance's
30-day deletion (see the `databases` block in `deploy/render/render.yaml`) is a
reliability problem, never a capacity one. Storage will not be what makes you upgrade.

## The frontend needs nothing

The SPA is a static site on a CDN — already free, and no paid tier makes it faster. The
build is ~398 KB of gzipped JS, 36 KB of CSS and ~726 KB of fonts, so roughly 1.2 MB on a
cold visit and effectively zero on repeat visits, since Vite's hashed asset names are
immutably cacheable. Against 100 GB/month that is ~85,000 first-time visits.

(The JS was ~374 KB before drag-reordering; `@dnd-kit/core` and `@dnd-kit/sortable` are
~24 KB gzipped between them. They sit in the main chunk because `ViewControls` is on the
list page itself, not behind a dialog — worth knowing if the entry bundle ever needs
trimming, though at 6% it is not where the weight is.)




The tempting "consolidation" — serving `dist/` from FastAPI with `StaticFiles` to run one
service instead of two — is a **downgrade**. It moves work that a CDN does for free onto
the one resource that is actually scarce, and it gives up the edge caching. The two-service
split in the blueprint is deliberate.

## What was fixed

Three changes, all in the auth path, none of which alter behaviour:

1. **Build the signing key once** (`src/auth/helpers.py`). The PEM is parsed at import into
   a `RSAKey`; `create_access_token` and `decode_access_token` use it. 272 ms → 6 ms per
   token issued, on logins and refreshes alike.
2. **Hash off the event loop.** `verify_password_async` / `get_password_hash_async` wrap
   the synchronous primitives in a threadpool, and the async request paths use them, so
   argon2 no longer blocks every other request in the process. The sync versions remain
   for non-request callers.
3. **Argon2 tuned to the OWASP minimum** (t=2, m=19 MiB, p=1): 316 ms → 44 ms of CPU and
   64 MiB → 19 MiB of peak memory.

Measured after the change, same method and same machine: a login costs **67 ms** of CPU,
down from 717 — a **10.7× reduction**, and rather better than the ~180 ms the parts
predicted, because argon2 at `p=4` was also burning CPU on thread synchronisation that
never showed up in an isolated measurement. `GET auth/users/me` fell from 5.9 ms to
2.7 ms as a side effect, since verification uses the prepared key too.

The blocking is gone outright. Re-running the table above:

| Concurrent logins | p50 before | p50 after |
|---|---|---|
| 0 | 34 ms | 33 ms |
| 1 | 459 ms | **30 ms** |
| 4 | 1250 ms | **36 ms** |

Peak memory per concurrent sign-in also drops 64 MiB → 19 MiB, which is what moves the
six-concurrent-logins OOM ceiling on a 512 MB instance.

Change 3 has a wrinkle worth remembering: **argon2 stores its parameters inside the hash**,
so an existing password keeps verifying at the old, expensive cost forever. That is why
`authenticate` calls `check_needs_rehash` after a successful verify and re-hashes in place
— the cost only actually falls as people sign in. It also means the change is safe: nobody
is locked out, because old hashes are still verifiable on their own terms.

## What is still true after the fixes

- **One instance, one worker.** The login throttle counters
  (`src/auth/tokens/throttle.py`) are correct *only* because of that, and scaling the service
  breaks them. The `alembic upgrade` that shared this caveat no longer does: it moved to
  `preDeployCommand` with the Starter upgrade, so it now runs once per deploy no matter how
  many instances there are. See the comments in `deploy/render/render.yaml`.
- **The spin-down is gone.** It was the free plan's, not the app's: 15 idle minutes, then
  about a minute to answer the next request. Starter is always-on, so the pinger that would
  have papered over it is not needed and neither is the free-instance-hours budget it ate.
- **Search is still the expensive read** at ~106 ms. It has not been a problem at demo
  scale; it is the first thing to look at if list views get slow. Archiving is now a partial
  answer to it, but only past roughly half a list — below that the planner does not take the
  partial index and search costs the same. The real fix is still
  [search-design.md](search-design.md).
- **Nothing is on the free tier any more** except the static site, which does not have a paid
  one. The database moved to `basic-256mb` ($6/mo plus storage), retiring what used to be the
  single largest reliability risk here — a free Render database is deleted 30 days after it is
  created, taking the app down with it — and adding managed backups. The API moved to `starter`
  ($7/mo), which bought always-on, the pre-deploy hook, and 5× the CPU. About $13/mo in total.
- **The 512 MB memory ceiling survived both upgrades**, so the six-concurrent-logins OOM is now
  the sharpest edge in the deployment. Change 3 above (argon2 parameters, 64 MiB → 19 MiB per
  in-flight hash) is what actually moved it; the next thing that would is a 2 GB Standard
  instance at $25/mo, and there is no reason to buy it yet.
