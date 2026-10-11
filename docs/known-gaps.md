# Known gaps, gotchas, and follow-ups

Things that are deliberately unfinished, behave in a way that surprises people, or will
bite whoever touches the code next. Ordered by how much they matter.

---

## 1. There is no public registration

`POST /auth/users/` is **administrative** — superuser-only — and it is the only way to
create an account. The first account exists solely because `seed.py` seeds it into an
empty users table. So today a superuser onboards everyone by hand.

Adding public self-signup means a **new** unauthenticated route (`POST /auth/register`),
not loosening the admin one. It needs its own narrower schema: `UserCreate` extends
`UserBase`, so the caller can already set `is_active`, and the model now carries
`is_superuser` — a public route accepting those fields would let a stranger self-promote.
It would also want rate limiting and email verification. Note `UserService.create` already
auto-provisions a personal workspace for every new user, which is the right behavior for
signup.

**This gap now has a page pointing at it.** A deployment with `VITE_HOME_ENABLED` serves
a public landing page at `/` (frontend/README.md, "The public home page"), and its call
to action is **Log in** rather than "Sign up" precisely because of the above — a signup
button there would be a dead end. The page says in as many words that accounts are
created by whoever runs the deployment, which is the honest version of this gap rather
than a workaround for it.

So whoever ships `POST /auth/register` has two files to finish the job in, not one:
`features/home/HomePage.tsx` (the hero's buttons and the closing paragraph, which
currently explains where an account comes from) and `features/auth/LoginPage.tsx`, which
offers nowhere to go but in. The guide's "Signing in" section says the same thing a
third time and would need the same edit.

## 2. A record shared without its table can't be viewed in the UI

The API supports this on purpose — record routes use `resolve_table_or_404` (existence only)
rather than `get_table_or_404` (which requires table `view`). Verified: a user with `edit`
on one record and *no* grant on the table gets 200 on that record and sees only it in the
table's records.

The UI can't render it. `TableDetailPage` is the only place a record appears, and on mount
it fetches `/tables/{uuid}` and `/tables/{uuid}/columns/`, both gated on table `view`. Both
403 and the page blanks.

The blocker is the **column schema**: records are JSONB keyed by column uuid, so a record
is unrenderable without the column definitions — and that endpoint requires exactly the
permission a record-only grantee lacks. Closing this needs a backend change either way:
let a record grant confer read access to the table's column schema (field names and types,
not its records). That is a deliberate widening, not a no-op.

On top of that, pick one:
- **Degrade `TableDetailPage`** when `/tables/{uuid}` 403s (smaller, one page to maintain).
- **A dedicated record page/permalink** (cleaner separation, a second page to maintain).

Both also need two fallbacks that are easy to miss: `RecordFormDialog` renders from a Form
(`/tables/{uuid}/forms/` is *also* gated on table `view`), and `LookupCell` resolves a value
by fetching a **different** table — so lookups must degrade to an unresolved value when the
target table isn't visible.

## 3. Grants have no foreign key to their resource

`permission_grants` points at its resource via `(resource_type, resource_id)` with **no foreign
key** — `resource_id` means a different table depending on the type, so the database cannot
enforce it. Nothing cascades. A deleted table, view or account leaves its grants behind.

They are inert (the resolver walks the live hierarchy, so a grant on a deleted resource resolves
to nothing) but they accumulate, and they used to make the Share dialog answer "who has access?"
with the names of deleted people. Three things now handle them, and **any new hard delete must
join them** — the schema will not remind you:

- `list_grants` filters out grants whose principal is deleted, so the sharing panel tells the
  truth immediately rather than after a cron run.
- `purge_deleted_items` / `purge_deleted_principals` delete a resource's grants as they finalize
  it — the purge is the only thing that hard-deletes, so it is the only thing that can leave a
  grant pointing at an id that no longer exists.
- `sweep_orphan_grants` catches the rest: resource or principal gone, or past the retention
  window. Anything still *inside* the window is left alone, because it can still be restored and
  its sharing should come back with it.

## 4. Records can hold a choice option that no longer exists

Resolved predecessor: choice cells used to store an invisible option **id** (a slug of the
label), which made every out-of-band write — CSV/JSON import, QR codes — demand a value
nobody could see. Options are now plain labels and a cell stores the label itself (D3 in
the data-model doc), so that whole class of problem is gone.

What remains is the flip side, accepted deliberately: **editing an option's spelling is a
remove+add**, and records holding the removed label keep it. Such a cell still displays
(the grid shows the stored string as-is) but fails validation on its next write, so whoever
edits that record must pick a current option. There is no cascade-rename — the `choices`
shorthand cannot tell a rename from a remove+add, and guessing would rewrite records on a
guess. If bulk relabeling ever matters, it belongs in the planned column **data migration**
feature (planned-features #2), which is built for exactly this find→replace-across-records
shape.

`allow_other` widens the same seam rather than closing it. A column that takes an answer of
the record's own accepts any non-empty string, so nothing about an off-table value is
irregular while the flag is on — but turning it **off** puts every write-in in exactly the
position above: still displayed, still exported, and refused on the next write of that
cell. The dialog says so where the flag is; there is deliberately no sweep that rewrites or
promotes those answers, for the same reason there is no cascade-rename.

---

## 5. A calculated column's value is stored as an *index*, and that has edges

A `calculated` column carries a `formula` in its config — arithmetic over the table's **number**,
**rating** and **duration** columns, string functions over its **text**, **email** and **choice**
columns, date functions over its
**date**/**datetime** columns, and references to other **calculated** columns, all by name
(`min`/`max`/`round`/`abs`/`int`,
`CONCAT`/`UPPER`/`LOWER`/`TRIM`/`LEN`/`LEFT`/`RIGHT`/`REPLACE`, `AGE`/`DT_FORMAT`/`TODAY()`/
`NOW()`, `IF`, `IS_NULL` and `DEFAULT`). The result is a number
or a string depending on the formula; the column has no fixed output type. The formula is parsed
and evaluated by `columns/formula.py`, which is a deliberately tiny AST walker with an
allow-list: **it never calls `eval`**, and the only names it resolves are the column values
handed to it, so a formula cannot reach a builtin, an attribute or a subscript. Keep it that
way — the moment it grows an `eval` or a real namespace, a table editor (an ordinary user, not
an admin) can run code. Note that `+ - * /` are numbers-only on purpose: text is joined with
`CONCAT`, so a string can never slip into `*` and become Python's silent repetition.

A missing cell propagates as "no value" — any formula reading one is blank, never a silent zero.
`IS_NULL(x)` and `DEFAULT(x, fallback)` are the **only two exceptions**, and there have to be
some: every other way of looking at a blank blanks the formula, so without them
`IF(notes == "", …)` can never be true and there is no way to ask the question at all. Both
treat an arithmetic failure as one more way of having no value, which is what makes
`DEFAULT(total / qty, 0)` a working divide-by-zero guard.

`IS_NULL` answers `1`/`0` rather than a boolean, so that `IS_NULL(a) + IS_NULL(b)` counts
missing things instead of blanking (arithmetic here is numbers-only and a bool is not a
number). `DEFAULT` returns one of its two arguments, and **evaluates the second only if it
needs it** — so an uncomputable fallback cannot blank a row whose first argument had a value.

**The two disagree about `""`, on purpose, and it is the thing to know about them.**
`IS_NULL("")` is `0` — a value is present — because it is a question about the cell and an
empty string is an answer somebody gave. `DEFAULT` substitutes for `""`, taking "empty" to mean
exactly what the filter language's `is_empty` means (`IS NULL OR = ''`, untrimmed) — because it
is a *fallback*, and one that fired for a never-filled cell but not for an emptied one is a bug
reported as "sometimes it shows nothing". A cell holding a space is a value to both;
`DEFAULT(TRIM(notes), "—")` is how you say otherwise.

**The load-bearing decision is that the value is written down *and* recomputed.**
`_validate_data` (`records/services.py`) writes each calculated cell into the record on create and
on every update, under the column's uuid like any other value — which is what gives
`apply_filters`, `apply_sorts` and `_apply_search` a real `data ->> key` to work with, since
those run inside Postgres and cannot call a Python evaluator. But `shape_values` still
recomputes on **every read** and overwrites the stored copy before serialising. So the stored
value is a **query index, not the source of truth**.

That is the whole safety property, and it is worth stating plainly: a stale index can put a row
in the wrong place in a sorted or filtered view; it can never put a wrong number on a screen or
in an export. Full materialisation — trusting the stored copy on read — was built once and is
the obvious simplification available later, but it converts every missed invalidation into a
plausible-looking wrong answer, which is the failure mode this arrangement exists to refuse.

**The clock is a second thing that puts the index behind.** Every other way the stored copy
falls out of date is a *schema* change — a formula edited, a referenced column renamed — which
the app can notice and offer a re-index for. `AGE`, `TODAY()` and `NOW()` need no event at all:
the answer for an unchanged record differs the morning after a birthday or a due date. The
property above is what makes that tolerable rather than a bug, and
`maintenance/recompute_calculated.py` already runs nightly for exactly this class of drift, so
for the date-grained two the window is one day on one row. Three consequences:

- A `unique` formula over `AGE` is judged against other rows' *stored* answers, which may have
  been computed on a different day, and a saved view filtered on an age range can miss a row
  for that same window.
- **`NOW()` is the degenerate case, and is allowed anyway.** Its index is wrong a second after
  it is written, so a column built on it is for *reading* — where every value is recomputed and
  therefore exact — and not for sorting, filtering or `unique`, which read the stored copy. No
  sweep can fix that, because there is no interval short enough; the nightly one merely restates
  the answer as of 03:17 UTC. This is a real edge and not a bug only because of the property above: the
  screen and the export are always right. If a filter on "changed in the last hour" is ever
  wanted, it belongs on `updated_at`, which is a real timestamp, not on a formula.
- **A `NOW()` column makes every re-index rewrite the whole table.** `recompute_records` skips
  rows whose stored value already agrees, and with `NOW()` none ever do. That is bounded (a
  nightly pass over one table) and was the price of not special-casing the function, but it is
  worth knowing before putting one on a table of a million records.

Reach is not the only thing a formula has to be bounded on. Because it runs once per record on
every read *and* once per record per re-index, what it may *spend* is bounded too: `MAX_SOURCE`
(length), `MAX_DEPTH` (nesting — length alone is not a bound on depth, since a unary minus costs
one character a level and a few hundred of them overflow the stack) and `_MAX_TEXT` (the size of
any text it produces — `REPLACE` grows a string by a factor set by a literal, and nesting
composes those factors while the source only grows linearly). The first two are refused at
config time; the third blanks the cell, since it depends on the row. Keep all three: without
them a formula that passes every other check is a way to spend the whole server, which is the
same problem as running code, arrived at from the other side. (Those three, `ROW_FUNCTIONS`, and
the two `*_row_expression` seams are public because table **metrics** build a second grammar on
this one — see §9 below. A metric walks this same tree once per record too, over more records
than a grid page, so the bounds matter more there, not less.)

Storing the value is what makes the column ordinary everywhere else: it filters, sorts, groups
and searches like any other, and the export writes it. Sorting is the one place the missing
output type shows: a calculated column emits *two* ORDER BY terms (`_order_terms`), a numeric
reading guarded by `jsonb_typeof` and the text reading behind it, so a numeric formula sorts
numerically and a text one alphabetically without either having to declare which it is. A range
filter picks the same way, from the type of the value being compared against. Casting outright
would not merely mis-sort a text row — `CAST('Ada' AS numeric)` fails the whole statement.

The remaining edges:

- **Nothing keeps the index current across a schema change, on purpose.** A record write
  re-indexes that record for free, but editing a formula, or renaming, deleting or restoring a
  column some formula reads, changes every record's answer without touching a record. There is
  **no drift detection** — no schema digest, no `recompute_pending` flag, no automatic backfill
  — because neither entry point needs to detect anything: the column dialog offers a re-index
  right after a formula edit (it just handled the edit), and `Refresh calculated` in the columns
  panel is always available. `POST /tables/{uuid}/columns/recompute` is the route,
  `columns/backfill.py` the engine, and the nightly `recompute_calculated` the backstop. What
  this costs is a window where a sort or filter answers for the previous formula. A digest and a
  pending badge were built once; they guard against *visible* staleness, which recomputing on
  read has already removed.
- **A re-index is a full-table rewrite with no row ceiling.** Measured at **~0.035 ms of app
  CPU per record** with two calculated columns per record, linear from 20k to 50k rows
  (20,000 records ≈ 0.7 s of CPU, ~7 s on a 0.1-CPU instance; 500,000 — the 1 GB ceiling — is
  ~18 s of CPU, ~3 minutes). Above `FORMULA_INLINE_MAX_RECORDS` (5,000) it goes to a FastAPI
  background task and the route answers at once; below it the request does the work. The
  threshold decides who waits, **not** how much is done: a deferred run still walks the whole
  table, because a half-updated index holds two formulas' answers at once and filters
  incoherently, which is strictly worse than one uniformly a version behind. Peak Python memory
  is flat at **2.8 MB** above baseline at both 20k and 50k rows (500-row batches, each committed
  on its own, so no lock is held across the job) and the longest contiguous event-loop block is
  **~23 ms**, comfortably inside the health check. A pass over an already-current table costs
  ~0.026 ms per record and writes nothing, which is what makes the nightly sweep cheap. A background task is awaited inside the ASGI response cycle, so it has **no
  cancel handle** and uvicorn's graceful shutdown waits for it — a redeploy during one is
  delayed and then truncated, which is recoverable precisely because chunks commit
  independently.
- **One re-index per table, and nothing to unstick.** A session-level `pg_try_advisory_lock`
  keyed on the table admits one run; a second caller — including a second *person* — gets a 409,
  which is right, because the index is shared state and the run in flight is doing their work
  too. Session-scoped for two reasons: it survives the per-chunk `COMMIT`, so one connection
  serves the whole job, and Postgres releases it when the connection drops, so a killed process
  needs no TTL or cleanup path.
- **A superseded run is not detected.** A pass captures its evaluation plan once at the start
  (deliberately — re-reading per chunk would produce a mixture rather than a version), so if a
  formula is edited mid-run the finished index is one version behind and the 409 the editor got
  made it look handled. It converges on the next run or the nightly sweep, and it can only open
  on a table over the deferral threshold with two `configure` holders acting inside the window.
- **Calc-on-calc is allowed; cycles are refused.** `chain.build_chain` topologically sorts the
  graph so a chain settles in one pass per write — `total` reads *this* write's `subtotal` — and
  `find_cycle` refuses a ring at save time, naming it. What a formula may name is
  `FORMULA_REFERENCEABLE_TYPES`: number, **rating** and **duration** (all three are
  `NUMERIC_TYPES` — a cell in any of them holds a plain JSON number), text, **email** (plain
  text with a checked grammar; "the address, or a fallback" and "the part after the @" are
  ordinary things to ask, and nothing about the check makes the string less readable),
  calculated, **choice** (a cell stores its label,
  so it is already the text a formula wants; a *multiple* one reads as its labels joined with
  ", "), and **date/datetime** (a cell is an ISO string, which `AGE` and `DT_FORMAT` read and
  which compares and sorts lexically for free). Still excluded: `checkbox` and `link`, which have
  no text form worth computing over; `color`, which is a rendering instruction rather than a
  value; `location`, whose cell is an object; `time`, which the date functions do not read; and
  the REFERENCE_TYPES, whose ids need a database round
  trip to become a label — the evaluator is a pure function of one record's values and has no
  session to make one with. What a name refers to is
  decided by *where it sits*, not how it is spelled: `column_refs` reports every value-position
  name, including the four that collide with function names (`min`, `max`, `round`, `abs`, all
  valid slugs). Reporting those as functions is what used to let a formula over one skip these
  checks entirely.
- **A calculated column may be `unique`, and it is judged against the index.** It is how you
  express a key no single column holds (`CONCAT(course, section)`), and it works because
  `_validate_data` writes the computed cell before `_enforce_unique` reads it — the answer
  checked is this write's. The rows it is checked *against* are read as stored, so between a
  formula edit and a re-index uniqueness is judged on the answers the old formula gave: a
  false conflict, or a missed one, until `POST /tables/{uuid}/columns/recompute` or the
  nightly sweep runs. This is the one place a stale index is more than a misordered row, and
  it is left that way rather than recomputing every row of the table on every write. Two other
  edges follow from the check being per-write: turning `unique` on leaves existing duplicates
  alone (they surface the next time one of them is saved), and a re-index that *creates*
  duplicates writes them without complaint. `default` is still refused — a value somebody
  supplies is exactly what a formula overwrites.
- **Exports carry the computed value; templates and imports do not.** The export **computes**
  rather than reading the stored copy (`shape_values` over the streamed rows), because a file
  leaves the app and nothing can correct it afterwards — it is the last place that should trust
  an index. `_parse_cell` skips a calculated column on the way back in, so an exported file
  re-imports cleanly and the formula rewrites the cell from the row's own values. The
  *template* still leaves them out: it is a file you fill in, and a heading whose value is
  discarded looks like it works.
- **References are by column *name*, and names are editable.** Rename a column a formula reads
  and the formula silently stops finding it — a missing operand reads as blank (see below), so
  the calculated cells quietly go empty rather than erroring, and the index goes empty with them
  at the next re-index. There is no reverse index from formula to column; renaming is not
  re-checked.
- **A missing operand makes the whole cell blank, not zero.** If any referenced cell is empty,
  the result is `None` and the cell renders `—`. This is on purpose: an incomplete row should
  read as incomplete, not as `0 * price`. Division by zero, a non-finite result, a formula that
  no longer parses and a cycle are all blank the same way — one bad row must not fail a whole
  table read, and a calculated column is not a place data hides.

---

## 6. Trash is done everywhere, and every bin now has a surface

Soft delete already destroys nothing (see "Soft delete destroys nothing" below): deleting a
record, user or group only sets `deleted_at`, and the purge jobs hard-delete once a target is
past its retention window.

**Views, forms, metrics and columns now have a bin too**, at `/tables/{uuid}/{kind}/deleted` with
`…/{kind}/{uuid}/restore` and `…/{kind}/{uuid}/permanent` — routes on each child's own
collection, one shared implementation in `src/tables/bin/services.py`, swept by
`purge_deleted_table_config`. Before that they were the worst of both: deleting one was
irreversible *and* kept the row forever, because nothing read `deleted_at` back and no job
reclaimed it. Three things about that surface are deliberate and worth knowing:

- **uuid only, never a name.** Every child route is uuid-only now, but the bin could never
  have been anything else: the unique index on `name` is partial (`WHERE deleted_at IS
  NULL`), so a binned `sales` view and a live one coexist legitimately and a name in the bin
  has no single answer. (These routes sat at `/tables/{uuid}/bin/{kind}` until children became
  uuid-only — the `/bin/` prefix existed only because `deleted` was itself a legal name.)
- **Gated on table-level `configure`**, the authority that creates them, because a deleted view
  resolves to no permission context of its own. That makes the bin coarser than the live routes
  for a view whose `permissions_inherit` is False — its recovery belongs to the table configurer.
- **A column's restore is partial, and says so.** Restoring clears `deleted_at` and nothing
  else, matching the other three. But deleting a column also nulls the table's
  `primary_column_id` if it pointed there, and un-nests every lookup whose pairing referenced
  it — neither comes back. `RestoredColumnRead` reports the first (`table_has_no_primary_column`,
  read off the table's *current* state, so a primary chosen meanwhile is not flagged). It cannot
  report the second: the pairing was cleared on the dependents, and an un-paired lookup is
  indistinguishable from one that never had a parent.

One thing about columns that is easy to get wrong, and worth stating plainly: **deleting a
column hides its data, it does not erase it.** The values stay under the column's uuid in every
record's `data` — which is exactly what makes the restore a real undo — and nothing but
`…/permanent` or the purge job removes them.

Readers no longer see them: `shape_values` drops any key with no live column, so a record's
payload matches its schema. That is a deliberate pairing rather than a redundancy. Until it was
added, a deleted column's values kept arriving in every record payload while the field itself
had vanished from the UI, so "delete the column" looked like it hid something and did not.
Filtering fixes the leak but makes the remaining hazard quieter, not smaller: someone deleting
a column to get rid of sensitive values now sees exactly what they expect to see, and the
values are still on the rows. `…/permanent` is the only thing that ends that.

Filtering is safe against write-back because an update merges onto the *stored* dict
(`_validate_data` starts from `dict(existing)`) and `_shape_for_read` assigns through
`set_committed_value` and never flushes — so a client that reads a filtered record and PATCHes
it cannot erase what it was not shown (`test_writing_a_record_does_not_erase_a_deleted_columns_values`).

**Delete for good exists** for records, tables, workspaces and now these four: a `…/permanent`
route on each, valid only from the bin, so destroying something is always two deliberate steps
(`test_permanent_delete.py`, `test_table_config_bin.py`). Each clears its target's grants itself,
because nothing else can once the row is gone (gap 3).

**Records now have a bin.** Each table page has a "Deleted records" surface
(`DeletedRecordsDialog`) backed by `GET /tables/{uuid}/records/deleted` and
`POST /tables/{uuid}/records/{uuid}/restore` (`TableRecordService.list_deleted`/`restore`). It tables
soft-deleted rows still inside the retention window and restores them. Two things settled the
shape and are worth keeping in mind before extending it:

- **Authority is the table, not the record.** A soft-deleted record resolves to *no* permission
  context (`_security_context` returns `None` for a deleted resource), so there is nothing to
  check per record. The bin is gated on table-level `delete`; the workspace owner always
  qualifies via the owner short-circuit. This is why the trash is **per-table and owner/grant
  driven**, not superuser-driven — `is_superuser` is orthogonal to data access.
- **Restore's one live edge is uniqueness.** A `unique` column may have gained a duplicate while
  the row sat in the bin, so `restore` runs `_enforce_unique` and can legitimately 409. The
  other two edges are free: inbound references were kept intact at delete time so they just
  resolve again, and a stale `multiple` shape is absorbed by `coerce_shape` on read (the same
  tolerance `reconcile_shapes` converges).

**Principals have a bin too**, at `/auth/users/deleted` and `/auth/groups/deleted` with
`restore` and `permanent` on each, one shared implementation in `src/auth/users/bin.py`. It is a
*separate* surface with a *different* authority — `require_superuser`, not table ownership —
because accounts and groups are identity administration and sit under the permission model:
restoring a group hands back every grant it held. Three things about it are unlike the others:

- **Restore is a real undo, memberships included** — and getting there was a security change, not
  a cosmetic one. The deletes used to clear memberships so the row stopped appearing in the other
  side's read schema, which made the delete lossy in a way nothing recorded: an account came back
  with no groups and no way to tell it had ever had any. The rows are kept now and the reads
  filter instead. Both relationships join through `deleted_at IS NULL` (`models.py`), which covers
  every read of them at once — **but `PermissionService._group_ids` queries
  `user_group_association` directly** and so gets no benefit from that. It checks liveness itself.
  Without that check, deleting a group would have left every grant it holds still reaching its
  members, which is the opposite of what deleting a group means. Anything else that reads the
  association table directly has to make the same check
  (`test_a_deleted_group_confers_nothing_while_it_is_binned`).
- **Restore cannot 409.** `username`, `email` and a group's `name` are plain unique constraints
  rather than the partial (`WHERE deleted_at IS NULL`) indexes the rest of the schema uses, so a
  deleted principal keeps its name reserved and nothing can take it meanwhile. The cost is the
  other side of that coin: a deleted account occupies its username and email forever.
- **Permanent delete is asymmetrical, and for accounts it is usually blocked.** A group is only
  ever a principal, so nothing stands in the way. An account owns workspaces through a non-null
  foreign key, so `…/permanent` refuses with a 409 naming them — soft-deleted ones included,
  since they hold the key just the same. Because `UserService.create` bootstraps a personal
  workspace, **every** account owns at least one from birth, so destroying one always means
  dealing with its workspaces first. That is the guard working as intended, not an edge case,
  but it does mean there is no one-click account destruction and should not be one.

`purge_deleted_principals` still does not delete rows, and still deliberately: it runs
unattended, and the ownership question above needs a person.

**All six bins are now reachable from the app**, which they were not when the last two were
added. Two components cover the four that arrived together, each written once against the
kind rather than copied per kind, matching the API modules behind them:

- `DeletedConfigDialog` serves columns, views, forms and metrics, mounted from each kind's own
  tab in Table settings — the bin sits beside the thing it holds, which is where the records,
  tables and workspaces bins already sat. It is the caller that checks table-level `configure`
  before offering it, matching the gate on the routes. Restoring a column is the one case with
  something to report back: `table_has_no_primary_column` becomes a warning toast, because that
  is the half of the delete the restore cannot undo.
- `DeletedPrincipalsDialog` serves accounts and groups from the People page, offered only to a
  superuser, matching that surface's separate authority. A blocked account purge (the 409 above)
  reaches the user as the backend's own message, workspace names included — that table is the
  entire point of the refusal and must not be flattened into "could not delete".

Each bin's query key is nested under its subject's live key — `["views", tableUuid, "deleted",
…]` — so every `invalidateQueries` already in the app refreshes the bin by prefix match.
Deleting a view leaves the bin correct without the delete having to know a bin exists.

What is still missing:

- **Tables cascade-scrub on delete, and restore does not undo that.** A table restore brings back
  its columns, records, views, forms and metrics (`_TABLE_CHILD_MODELS`, matched on the shared
  `deleted_at`), but the *inbound* lookup references scrubbed at delete time do not come back
  (see "Deleting a table is the deliberate exception"). Since the scrub also clears the
  referencing **column's** config — target, display column, nesting pairing, option filters
  and `required` — the restored table comes back with nothing pointing at it, and each of those
  columns has to be retargeted by hand. That is the larger half of the loss, and it is a
  deliberate trade: the values are unrecoverable either way, so the alternative was leaving
  the column pointing at a table that might never come back and could not be edited meanwhile.

Only targets still *within* the retention window belong in the bin; once purged they are
unrecoverable and simply gone from it.

### The archive is a second axis, and it is not a bin

Records — and only records — also carry `archived_at`. It exists for a different reason than
`deleted_at` and behaves differently at almost every edge, so the two are worth keeping apart
in your head: a delete says *this was a mistake and it goes for good in thirty days*, an
archive says *this happened and I have finished with it*. What it buys is that
`TableRecordService._select` stops returning the record, which takes it out of the grid, the
count, search, filters, sorts and the metric scan in one predicate — and out of
`ix_table_records_table_id_id`, which is partial on **both** flags. That index and `_select` have
to keep saying the same thing: a partial index is only usable when the query's predicate
implies the index's, so dropping `archived_at` from either quietly costs the index rather
than returning wrong rows, which is the harder failure to notice.

Five things about it are deliberate:

- **No retention, no purge.** Nothing removes a record for being archived, so the archive
  surface has no deadline to display and nothing ages out of it. `PURGE_DELETED_AFTER_DAYS`
  still applies only to `deleted_at`.
- **Delete wins where they meet.** `_select_archived` requires `deleted_at IS NULL`, so
  archiving and then deleting puts the record in the *bin* — the surface with the deadline on
  it, because a record about to be purged must not be hiding somewhere with none. `delete`
  does not clear `archived_at`, so restoring returns it to the archive rather than silently to
  the live table. This is also why the delete path resolves through
  `TableRecordService.get_unbinned_by_uuid` (live *or* archived) rather than `get_by_uuid`:
  without it an archived record could not be binned at all.
- **Unarchive cannot 409, and restore can.** `_enforce_unique` scans `deleted_at IS NULL`,
  which an archived row satisfies — so an archived record never releases its unique values and
  nothing can have taken them meanwhile. A *binned* one does release them, which is why
  `restore` re-runs the check and can legitimately conflict.
- **Rendering a label is uuid-keyed and always works; matching a label is search, and archived
  records are out of it.** So a cell already pointing at an archived row still shows its name
  (the options endpoint's resolving branch, `uuid IN (…)`, is unfiltered), while no picker
  offers it, no search in the referring table finds it by that label, and nothing new may link
  to it — by name *or* by pasted uuid, since `_resolve_lookup_values`'s two branches have to
  agree or a uuid would make a link the picker refuses. The one place this reaches across
  tables is `_reference_matches`, which does filter archived rows: its `LIMIT` bounds the
  result and not the scan, so leaving it unfiltered would have kept one query reading the
  whole archive while every query around it got smaller.
- **Maintenance and structural paths still see archived rows**, deliberately: the purge job,
  the table cascade, the permanent column-delete scrub, and the whole conversion path — a
  conversion rewrites archived rows too, or unarchiving one would return data in the old type.
  `TableService.clone` copies them and carries `archived_at` over. Export is the exception that
  proves the rule: it excludes them like every other read, but takes `include_archived` so
  archiving cannot make records permanently unexportable.

**Access is table-level `delete`, like the bin, but for a different reason.** A soft-deleted
record resolves to no permission context at all; an archived one still does. The gate is the
same because archiving is what takes a record away from everyone who can read the table, so the
authority to see the archive is the authority to have put things in it.

---

## 7. Narrowing a lookup's options: stale pairs survive, and both filters are advisory

A nested lookup (planned-features #6) enforces its parent/child pairing **only on values
being added**. That is the same deal choice options struck (gap 4): enabling nesting over a
column with existing data leaves mismatched cells exactly as they are — they render fine,
survive edits to *other* cells, and even survive being re-sent unchanged (a keep, not an
add). The pairing bites the moment the cell itself gains a new value. There is no scan, no
migration, no cascade-fix; if bulk cleanup ever matters it belongs in the planned data
migration feature, same as choice relabeling.

Two consequences worth knowing before extending it:

- **`?parent_value=` on the options endpoint is a convenience, not a boundary.** A client
  that omits it gets the unfiltered search a plain lookup gets — deliberately, because label
  resolution, view-filter value pickers and QR fills all hit the same endpoint with no parent
  context. Consistency is guaranteed by `_enforce_nested_lookups` on the write path, which
  import and QR submissions share. Don't "fix" the endpoint to require the param.
- **A dangling pairing degrades, everywhere.** Deleting or retargeting the parent or the
  filter column cascade-clears the pairing (`_clear_dependent_nesting`), and so does deleting
  the *table* either of them targets — `_scrub_references_to_tables` blanks the column's config
  and then runs the same cascade outward, because a child whose own target survives is not
  itself in scope for the scrub. If a stale pairing is ever encountered anyway (a race, a
  hand-edited config), the picker, the record dialog and the write validation all treat the
  column as a plain lookup rather than erroring.

### The option filter is advisory too, and is the caller's business in no sense at all

A lookup's `option_filters` (a view's `FilterGroup`, over the *target's* columns) narrows what
the picker offers. It inherits both halves of the deal above: it applies to a **search** and
never to resolution by uuid, so a cell keeps its label after the filter stops offering the
row; and nothing enforces it on write, so turning one on over a populated column cannot make
those records unsaveable. Conditions naming a column the target no longer has are dropped by
`apply_filters` rather than raising — which is why `_validate_option_filters` checks them when
the filter is *written*, since an unvalidated typo yields a filter that reads as set and
offers everything.

**Where it differs from `?parent_value=` is the part not to get wrong.** That param is
caller-supplied and safe to be, because it can only name a row uuid the caller already holds.
A general predicate is a different object: `lookup` is the action that hands over a row's
label *without* opening the row, so a caller who chose the predicate could read the target's
hidden columns by elimination — bisect `roll > n` to recover a number, walk `contains` a
character at a time to recover a string. So `option_filters` is read from stored config and
from nothing else. Do not add a filter query parameter to the options endpoint, and do not
"generalise" the nesting param into one; both would quietly convert every lookup-only grant
into a read of the whole target.

The same asymmetry is why `_validate_option_filters` **skips** validation when the caller
cannot `view` the target. A lookup-only grantee is refused `GET /tables/{it}/columns/` on
purpose; an error that distinguished "not a column of the target" from "accepted" would answer
that same question one uuid at a time. They get the silent drop instead — a worse filter for
them, rather than a schema oracle for everyone. `tests/test_lookup_option_filters.py` pins
all of it, `test_the_endpoint_takes_no_filter_from_the_caller` most of all.

### `allow_create` is a third advisory setting, and the one most likely to be misread

A lookup may let the picker **add** a missing row to its target table rather than dead-end
(data-model §5). It reads like a permission and is not one. The button appears only for a
caller who holds `create` on the target, that is resolved per caller rather than from the
column, and `POST /tables/{target}/records/` re-checks it regardless — so the flag can only ever
surface a shortcut to somebody who could already have walked over to the target table and
added the row by hand. It is not validated against the *configurer's* access either, because
whoever configures a table is routinely not whoever fills its forms, and the target's grants
move independently of the referring table's.

The reason this does not reopen the hole the rest of this section guards: `create` confers
`view` (`permissions/enums.py` `_IMPLIES`), so being eligible for the button already implies
being able to read the target's schema. A lookup-only grantee is eligible for nothing, sees
no button, and is still refused `GET /tables/{it}/columns/` — which is also why they could not
fill in a create form even if one were somehow put in front of them.

Three consequences, all of them the advisory deal again:

- **Only the pickers honour it.** An unknown label is still `UnknownReferenceError` from CSV
  import, from the QR fill and from a direct API write. `_validate_lookup` has no
  side effects and must keep none: it runs once per row of an import, so auto-creating there
  would turn one mistyped column into thousands of rows in somebody else's reference table.
- **A created row need not satisfy `option_filters`,** which are not enforced on write. The
  cell resolves and displays its label; no later search offers the row. Exactly the state a
  filter turned on over populated data already produces.
- **Nothing stops a duplicate label**, and duplicates are not free: `_validate_lookup` refuses
  a label matching more than one row rather than guessing, so a second "Acme" costs everyone
  the ability to name the first one in a CSV. The picker therefore offers to create only when
  no suggestion matches the typed text exactly — a client-side courtesy, bounded by the
  25-row suggestion cap, and not a guarantee. `unique` on the target's label column is the
  only real defence.

Two places it is deliberately switched off regardless of config: the filter-condition value
editor (a filter asks about rows that exist, and there is no record for a new one to belong
to) and the grid's inline cell editor (a `Dialog` portals outside the `data-pr-is-overlay`
subtree `DataTable` checks, so the first click inside it would close the cell and unmount the
dialog). Both are one prop, `allowCreate`, decided by the caller — the same shape
`parentValues` uses for nesting. `tests/test_lookup_grants.py` pins the permission halves.

**One PrimeReact trap, and it is the whole feature.** `AutoComplete` renders its overlay only
when `suggestions` is non-empty, unless `showEmptyMessage` is set —
`autocomplete.cjs.js:1000`, `isNotEmpty(props.suggestions) || props.showEmptyMessage ?
show() : hide()`, with the prop defaulting to `false`. The create option lives in
`panelFooterTemplate`, so without that flag it is unreachable in precisely the case it exists
for: a search that matched nothing renders no panel and therefore no footer. `ReferencePicker`
sets `showEmptyMessage={!!create}` — on for a picker that can create, off everywhere else, so
the user and group pickers keep closing silently on no results as they always have.

## 8. Rich text is HTML in a cell, and some things treat it as text

A `text` column in `rich` mode stores HTML. Sanitising is on the **write** path
(`TableRecordService._validate_data` -> `columns/richtext.py`), so it covers direct API
writes and CSV import as well as the editor, and rendering it back is safe because of
that and only because of that. Two places still see the markup:

- **Export writes the raw HTML.** `_csv_cell` has no text-type special case, so a CSV of a
  rich column contains `<p>` tags, and re-importing that file round-trips them back through
  the sanitiser unchanged. That is at least lossless; it is just not pretty in a spreadsheet.
- **Search matches tag names.** `?q=` is `data->>key ILIKE`, so searching for `strong` finds
  every bold cell. Filters and sorts on a rich column compare markup for the same reason.

Neither is a correctness bug and both are cheap to live with. The fix for both is the same
one: store a `to_text` projection alongside the HTML, which is a schema change and was not
worth it for a first cut. `min_length`/`max_length` already measure the visible text, not
the markup, so limits are not affected.

The allowlist in `richtext.ALLOWED_TAGS` and the toolbar in `RecordFields.RICH_TOOLBAR` are
two halves of one decision and must be changed together: a toolbar button whose tag the
sanitiser drops looks like it works and then silently loses the formatting on save.

---

## 9. Metrics are recomputed per request, and the scan is bounded

A metric (data-model §9) stores a formula, never a number: `GET
/tables/{uuid}/metrics/values` answers every card over the records in scope, together, per
request. A total is therefore never stale, and nothing has to be backfilled when a record, a
formula or a filter changes. A `calculated` column used to make the same bargain and no
longer does — it writes its answer down as a query index (§5) — but a metric cannot: its
number depends on the caller's grants and the request's filters (see below), so there is no
single value to store. The costs are worth knowing about.

**There are two paths, and which one runs depends only on the formula.** `metrics/pushdown.py`
compiles the common aggregate shapes into one SQL statement, so Postgres answers them and no
record becomes a Python object; anything it cannot compile falls back to the scan below.
Measured on a 100,000-record table (2026-08-05, same machine and method as
capacity-and-performance.md), per request:

| | scan | compiled |
|---|---|---|
| `SUM(qty)` | 1,991 ms app CPU | **9 ms** |
| `SUM(qty * price)` | 2,175 ms | **3 ms** |
| `COUNT()` | 1,884 ms | **0 ms** |
| `AVG(qty)` | 1,941 ms | **0 ms** |

The compiled column is at the resolution floor of `time.process_time` on Windows (~15.6 ms),
measured over batches — the honest reading is "indistinguishable from zero app CPU, and
12–196 ms of *Postgres* wall time", which is the side of the box that has capacity to spare.
The scan costs ~20 µs of app CPU per record, and that is the number the row limit exists to
bound.

- **The same card shows different numbers to different people, on purpose.** Both paths build
  on `TableRecordService.visible_stmt`, so they honour `record_view_filter`. A reader who
  cannot see a record must not learn its value from a total, which means "Total hours" is
  the total of *your* hours, not the table's. `row_count` in the response is the count after
  permissions, filters and search, so it is the number to show if you want to explain a
  figure — it will not match the table's record count and is not meant to.
- **The scan is capped at `METRIC_ROW_LIMIT` (50,000); the compiled path is not.** Past the
  cap the answers describe the first 50,000 records and `truncated` is set; the card renders
  a note rather than a number that quietly means something else. Refusing outright was the
  alternative and leaves the reader nothing. The scan streams `data` alone (not ORM
  instances) in batches of 1,000 and skips the view's sort, which an aggregate has no use
  for. A compiled request has nothing accumulating in Python to cap, so it answers over
  every matching record and reports the true `row_count`.
- **Compiling is all-or-nothing per request.** One uncompilable metric puts the whole strip
  back on the scan. The alternative — compiled cards answered over every record while
  scanned ones stop at 50,000 — would sit two numbers describing two different record sets
  side by side, with only one carrying the truncation note. Cards agreeing with each other
  is the property the single-pass design exists to provide.
- **What stays on the scan, and why each one.** `MEDIAN` and `UNIQUE` (their equality and
  ordering semantics do not survive the round trip through JSONB text — `1` and `1.0` are
  one element of a Python `set` and two distinct strings in SQL); division *inside* an
  aggregate (Postgres fails the whole statement on a zero divisor where the scan blanks that
  one row); row functions inside an aggregate (`ROUND`, `IF`, `CONCAT`); `multiple` columns;
  and every `calculated` column, whose stored value may lag a formula edit (§5) — totalling
  the stale copy is exactly the silently wrong number the scan exists to avoid, so
  aggregating one still means recomputing it per record. `pushdown`'s module docstring is
  the authority on this table.
- **Arithmetic differs in Postgres's favour.** `numeric` is exact decimal where Python's
  floats are binary, so a compiled `SUM(qty * price)` over 100k rows measured
  `106250024.4` against the scan's `106250024.40004165`. The compiled answer is the more
  accurate one, and the gap is far below the 1e-6 the frontend already rounds to. Worth
  knowing before someone reports it as a bug.
- **Nothing is cached, and the frontend re-asks whenever the records move.** The values
  query rides `useLiveTable`, so a large table is where this will first hurt — and server
  push made that *more* true, not less: a busy table can produce events far faster than
  the old timer ticked. Three things keep it in hand, and they are worth keeping attached
  to each other:
  - **The strip is hidden until the toolbar's Metrics button reveals it**, and `visible`
    gates the query rather than just the markup — a table nobody is totalling costs
    nothing, and the timer only runs for someone actually looking at a total. Making the
    strip default to shown, or rendering it hidden with the query still enabled, quietly
    turns every open table into a repeating full scan.
  - **The cards refresh on their own, slower clock**: `METRIC_REFRESH_SECONDS` (default
    60) is a *floor* this caller passes `useLiveTable`. Under server push it **throttles
    the invalidation** — the grid refreshes on the event, the strip at most once a minute
    however many events arrive — and on the polling fallback it raises the interval, as
    it always did: same promise about cost, two mechanisms. Keeping it through the swap
    was not optional. Invalidating the whole `["records", tableUuid]` namespace per event
    would have been simpler and would put a whole-table scan on every write, per viewer,
    which is dearer than the polling push replaced; that is the reason each caller of the
    hook now names the key it wants refreshed. `LIVE_REFRESH_SECONDS = 0` still disables
    the fallback outright. The rows under a total are what has to be live; a total a
    minute old is a total.
  - **Your own edits do not wait for that clock.** The values key is nested under the
    records key (below), so the invalidation the grid already fires refreshes the cards
    immediately. The throttle above carries only *other people's* changes, which is why
    lengthening it costs so little.

  The three still matter after pushdown, because pushdown does not help the formulas that
  fall back — and those are exactly the expensive ones, since a scan is what they cost.
- **A renamed column breaks its metric — but says so.** Formulas reference columns by name
  and names are editable (§5), and there is still no reverse index. The difference is that a
  metric checks its refs against the live columns before scanning and returns an `error`
  for that card, because a broken `SUM` rendering `0` is the one wrong answer nobody would
  catch. The break costs its own card, never the request.
- **The values query key is nested under the records key** (`["records", tableUuid,
  "metrics", …]`), so every `invalidateQueries(["records", tableUuid])` already in the app
  refreshes the cards. Keep it that way: a sibling key would need a second invalidation
  beside each of the existing ones, which is correct the day it is written and wrong six
  call sites later.

Two things a metric deliberately does **not** do: it has no `permissions_inherit` (unlike
views and forms — it carries no permissions of its own, so table `view` reads and table
`configure` writes), and it has no per-group subtotals (see data-model §10).

## 10. Two kinds of datetime, and only one of them is an instant

The app stores datetimes under two conventions on purpose, and the difference is the
first thing to get straight before touching any of this.

- **System timestamps are instants, in UTC.** `created_at`/`updated_at`/`deleted_at`
  (`core/mixins.py`), token expiry, and grant expiry are `DateTime(timezone=True)` written
  with `datetime.now(timezone.utc)`. The frontend renders them with `toLocaleString()`, so
  the browser converts. Grant expiry is the one that compares against the **database**
  clock (`func.now()` in `PermissionService._not_expired`) rather than the Python one, so
  a grant cannot expire halfway through a multi-check authorization pass; every other
  expiry in the app uses the process clock, which is the same UTC but a different clock.
- **`date`/`time`/`datetime` column values are wall-clock readings, and carry no zone at
  all.** A birth date, a shift start, an invoice due date. They are stored as the string
  the user typed and read back the same way — `frontend/src/lib/temporal.ts` parses and
  formats with local `Date` component accessors and **ignores any offset suffix**, never
  going through `toISOString()`.

**Do not "fix" the second one by converting it to UTC.** A `date` has no time-of-day to
convert, so it lands a day early for anyone west of whoever typed it; a `time` has no date,
and DST means you cannot even pick an offset without one; a future `datetime` freezes an
offset that the jurisdiction may change before the date arrives; and a due date belongs to
the record, not to whichever of its readers is currently looking. `SharePanel` sending
`toISOString()` for a grant expiry is correct precisely because that one *is* an instant —
the two conventions coexist deliberately.

**What the user's timezone is for.** `users.settings.timezone` (an IANA id, defaulting to
`"UTC"`) exists for one job: resolving the `today`/`now` dynamic default on the user's
calendar rather than the server's. Before it, `resolve_dynamic_default` called a bare
`datetime.now()` — UTC on Render — so a `today` default stamped yesterday's date through
the first eight hours of the day for anyone in UTC+8, disagreeing with the calendar they
typed every other cell in the row against. It resolves to a **naive** datetime on the way
out (`_wall_clock_now`), because a stamped value has to be indistinguishable from a typed
one or the two sort against each other wrongly.

Follow-ups, in the order they will be missed:

- **Only the browser ever writes it.** `TimezoneSync` reports
  `Intl.DateTimeFormat().resolvedOptions().timeZone` on load and re-reports whenever it
  differs, which is right for a travelling user but leaves no way to *override* it — a
  picker in the profile page is the missing piece for anyone whose machine is set wrong.
- **`TimezoneSync` has no test**, and the runner that exists cannot give it one. `vitest` runs
  here in a **node** environment with `include: ["src/**/*.test.ts"]` and no jsdom
  (`vite.config.ts`), deliberately: what it pins is rules a record goes through — what counts
  as empty, how a value serializes, which reordering is a permutation — none of which render,
  and PrimeReact under jsdom is a maintenance cost of its own. Six files use it today. A
  component that mounts, reads `Intl` and PATCHes is out of its reach, so this loop still
  terminates by inspection (PATCH → cache update → `local === stored` → early return, with
  the `pushed` ref covering the in-flight window), which is not the same as a green test.
- **Display of instants ignores the setting.** The three `toLocaleString()` call sites (the
  deleted-workspaces, deleted-tables and deleted-records dialogs) use the browser's zone,
  which is right in almost every case and inconsistent with the account preference in the
  rest. Centralising them behind one `formatInstant()` would make that a one-line choice.
- **`zoneinfo` reads the OS tz database**, which Windows does not have — hence the
  unconditional `tzdata` dependency. If it ever goes missing, `available_timezones()`
  returns empty, every zone looks unknown, writes 422, and `today` quietly reverts to UTC.

## 11. Column type conversion ships narrow, and on purpose

`type` is no longer immutable — a conversion changes it (planned features §1). What that
buys and what it costs are both worth knowing before extending it.

- **Only pairs with `text` on one side, plus `date` ↔ `datetime`.** Everything else is
  refused with a message naming the two hops. That is not a stub: chaining X → text → Y
  automatically would collapse two reports and two decision points into one action, and the
  entire design exists so a person reads the report before anything changes. If you widen
  the matrix, widen it with direct pairs that are *better* than the round trip (as
  `date` ↔ `datetime` is), not with auto-chaining.
- **`calculated` is excluded in both directions.** The reasoning changed even though the
  answer did not: converting *away from* one is no longer a compute-and-persist job, since
  the values are already in the records (§5), and converting *to* one no longer discards
  data either. What remains is that gaining or losing a formula is a decision about the
  **column** — where its values come from — rather than a conversion of what is in it, and a
  cell converter is the wrong shape for that.
- **The cutover mutates `Column.type` in place.** It is the one operation that does, and it
  is what keeps the column's uuid, name and position — so views, forms, formulas and
  `primary_column_id` need no repointing. Anything that caches a column's type across a
  request would break here, and nothing does today.
- **It is one transaction, and it used to be two calls with a person in between.** That
  earlier design staged the converted values in a *hidden* column and swapped them in when
  the person pressed Apply — and the gap between those two calls lost writes. Every write
  path filters columns by `in_shape()`, which excludes hidden ones, so a record created while
  the report was on screen got a value under the source key and nothing under the target's,
  and the swap then wrote JSON `null` over it; a record *edited* in that window had the
  backfill's pre-edit value installed over the edit. Both silent, both reported as success.
  It is now `preview` (writes nothing at all) and `apply` (reads, converts, writes, retypes,
  all in one transaction), so there is no window and nothing to stage. **Do not reintroduce a
  staging column as an optimisation** — the cost of the second pass is a few seconds at the
  ceiling, and it is what buys the property.
- **Two things follow from being one transaction.** The apply holds row locks until it
  commits — on the rows it *writes*, which is every record whose source cell had a value; a
  record with an empty cell is skipped and stays writable throughout. Writers to those rows
  block for the duration, which is correct for a point-of-no-return operation and is why
  `ROW_CEILING` is now a bound on lock time as well as on work. And a write that lands while
  the pass is running is refused rather than run over: `_fingerprint` reads the table's row
  count and newest `updated_at` before the pass and again after the retype, and a change
  rolls the whole thing back with a 409. The conversion's own writes go through raw SQL
  precisely so they do not stamp `updated_at` and trip that check on themselves.
- **The re-check is not a full serialisation, and three gaps are worth naming.** It closes
  at the second `_fingerprint` read, not at `COMMIT`, so a row inserted in that last sliver
  is invisible — the same insert race `resanitize.py` already documents, and the reason it
  matters here is that such a record holds a value validated against the *old* type under a
  column that has just been retyped. It sees `table_records` only, so a concurrent
  `PATCH /columns/{uuid}` on the source is neither detected nor preserved: `apply` overwrites
  `config` wholesale. And it is blind to the app's other raw-SQL writers of `data`
  (`backfill`, `resanitize`, `reconcile_shapes`, `_references`, `purge`, the record service's
  cell patch), none of which stamps `updated_at` either — notably a calculated re-index,
  whose advisory lock is deliberately in a *different* namespace, so it can run on the same
  table concurrently. That is currently harmless because both sides merge inside SQL against
  the current row version under row locks, but it is harmless by accident: a future writer
  that read a record in Python and wrote the whole blob back would silently lose the
  conversion's work with no fingerprint change.
- **Storage roughly doubles for the duration**, and permanently for a conversion. Every
  record holds the converted value under the column's own key and the original under the
  binned `<name>__before_conversion` column, until that column is purged. On a 1GB database
  that is worth checking before converting a large text column.
- **`required` and `unique` are cutover gates, not per-cell rules**, so a report can come
  back clean and the apply still refuse. That is deliberate — per cell they would fail every
  ordinary blank. What changed is the cost of failing one: the gates run inside the
  transaction, so a refusal now leaves *nothing* behind, where the staged version left a
  hidden column full of converted values and a 409 waiting for the next attempt.
- **A duplicate's name is not reserved by the preview.** It cannot be — the preview creates
  nothing. The preview reports a clash as a courtesy so nobody reads a report and only then
  gets refused, but the guard is the partial unique index, via the `IntegrityError` the apply
  catches. Under the staged design the name was held from the moment the report was built,
  which is why abandoning had to release it.
- **Above `ROW_CEILING` (50k) a conversion is refused**, full stop. The message used to point
  at a maintenance workflow that never existed; it no longer promises one. If a table that big
  ever becomes plausible, the thing to build is a job that can hold the lock outside a web
  request — not a return to staging.
- **A duplicate inherits the source's `position`**, which reads as "next to it" and is not.
  Nothing in the UI ever sets `position`, so every column is 0 and the order falls through to
  `id` — a duplicate appears last, like any other new column. Placing it adjacent means
  renumbering every column after it, which is a reordering feature and not this one. The
  reordering that *has* since shipped does not close this: a view's `column_order` and a
  form's `fields` are drag-editable and the Columns tab sorts by `display_name`, so all three
  surfaces put the copy somewhere sensible while `position` — the table's default order, which
  the grid falls back to — is still written by nothing.
- **Copying into an *already existing* column is still not built.** `ConversionService.apply`
  always creates its target, so that needs a target parameter and a decision the planned-features
  doc still tables as open: overwrite the target, or only fill cells where it is empty.
- **`Column.hidden` is now set by nothing.** It existed for the staging column and survives
  only so `src/maintenance/drop_conversion_scratch.py` — a one-off, run once after the atomic
  conversion deployed — can find rows the old design stranded. `in_shape()` still tests it, so
  the primitive is available if something ever needs it again; nothing should set it casually.

## 12. A form field's condition is an affordance, not a restriction

A field may be hidden or disabled when a condition over the record's other cells is unmet
(data-model §8). **The server never reads it.** `TableRecordService._validate_data` enforces
`required`, `unique`, `min`/`max` and the rest from the *column's* config whatever any form
says, and it has no form to consult in the first place — the write path is shared with CSV
import, the QR scanner, the grid's inline cell editing and any direct API call.

So the following all hold, and none of them is a bug:

- **A hidden field's column can still be written**, by importing a file, scanning a code,
  editing the cell in the grid, or `POST /records/`. The form editor's own hint says so, at
  the control, because "hidden" reads like "protected" and here it is not.
- **A required column hidden by its condition makes the record unsaveable.** The write is
  refused with `Column 'x' is required` and there is no field on screen to answer it — unless
  the column has a default. The editor warns when a condition is put on a required column;
  it does not refuse, because "required exactly when it is shown" is the ordinary case.
  Making `required` conditional server-side is the real fix and a much larger one: the write
  path would have to pick a form, and three of its five callers have none.
- **Conditions do not reach the grid.** "Edit cells" and the new-row draft resolve one
  editability map for the whole grid rather than one per row, so a conditionally-disabled
  field is still editable in a cell. Per-row would be possible; a column that is editable on
  some rows and not others is a worse grid, so it was not done.
- **A condition naming a deleted column is ignored, and the field shows.** `_condition`
  drops an unresolvable clause rather than failing the query, and the client matches it —
  hiding a field on the strength of a question nobody can answer leaves nothing on screen to
  explain why. The form editor prunes such a condition the next time it seeds, which is what
  keeps `_validate_config` from making the form unsaveable; until then it simply sits there.
- **A condition on a `calculated` column reads as empty while *creating*.** The value is
  computed on write, so a new record has none yet; on edit it is present and compares
  normally. Nothing prevents such a condition, since the edit case is legitimate.

## 13. A `location` cell is an object, and five readers have to know it

Every other column type stores a scalar or a list of scalars, and a great deal of this
codebase quietly rests on that. `data ->> '<column uuid>'` is *the value* for every one
of them. On a `location` it is the serialized blob — `{"address": "12 Rizal St", "lat":
10.3157, "lng": 123.8854}` — so a reader that was not told about this type does not
fail, it answers a different question, silently and plausibly.

**The four that were taught, and what each would have done otherwise:**

| Reader | Without the branch |
| --- | --- |
| `records/filtering.py::_field` | `contains "address"` matches every record; sorting orders by a leading `{` |
| `TableRecordService._apply_search` | a search for `10` matches every latitude and longitude |
| `transfer/services.py::_csv_cell` | a spreadsheet column full of `{'address': …}` |
| `columns/conversion.py::_render_scalar` | the same dict repr, written into every record **permanently** — a conversion is the point of no return |

The first two go through `filtering.location_address` and the last two through
`validation.format_location`, so there are two shared implementations rather than four
copies. **The fifth reader is whoever adds the next one.** If something new stringifies
a cell, it needs a case here, and nothing will tell you: the existing tests pass, the
grid looks right, and only the new surface is wrong.

`location` is deliberately absent from `NUMERIC_TYPES`, `REFERENCE_TYPES`,
`FORMULA_REFERENCEABLE_TYPES` and the metric sets, which is what keeps the numeric,
label-resolving, formula and aggregate paths from ever seeing one. Keep it that way
unless you are prepared to answer what `SUM(office)` means.

Two smaller things worth knowing:

- **`unique` needed nothing.** JSONB `@>` compares objects natively, so two records at
  the same address with different pins are correctly two places — and the same place
  twice is correctly a 409.
- **The key set is closed, and the write path is careful about *when* it normalises.**
  A cell with a surplus key is left alone rather than rebuilt, so `validate_scalar` can
  refuse it. Rebuilding first would drop the key silently, and since `data` is
  schemaless that refusal is the only thing standing between a location cell and a
  place to stash arbitrary JSON. The ordering is load-bearing and looks like a
  redundancy; `test_location_column.py` pins it.

**Geocoding is a third-party dependency that is allowed to be down.** `GET /geocode`
proxies Nominatim so the deployment has one identity, one rate limit and one cache —
none of which a browser can provide — and it answers `available: false` with a sentence
for an outage, a timeout, a missing configuration or a rate-limit rejection. It must
stay that way: a location column works by typing an address and dropping a pin, and
turning an unreachable volunteer-run service into a 5xx would make somebody's address
field appear broken for a reason that has nothing to do with them. The rate limit is a
per-process semaphore, which carries the same single-instance assumption the login
throttle already documents — two web workers are two callers to Nominatim.

**That semaphore is also why this route authenticates by hand.** It used to carry
`dependencies=[Depends(get_current_user)]`, which takes `Depends(get_db)`, and a `yield`
dependency is released only when the response completes — so a caller *queued behind the
gate*, waiting for a turn it had not been given, held a pooled session the whole time.
That is the same bug the events route documents, arrived at from the other direction: a
stream holds a connection because it lasts a long time, a geocode because it waits on a
one-at-a-time gate and then on somebody else's network. Fifteen of them empty the pool
(SQLAlchemy's 5 + 10 defaults) and every unrelated request in the app stalls until
`pool_timeout`, which a location picker debouncing a typed address across a few tabs can
reach on its own. `_authenticate` now resolves the caller inside a session opened and
closed before `search` runs, and `oauth2_scheme` stays a dependency so a caller with no
bearer token is still refused before anything happens and the route keeps its security
requirement in the OpenAPI document.
`test_a_queued_geocode_does_not_hold_a_pooled_connection` is what catches a regression —
nothing else would, until the site froze.

**On some Windows dev machines the geocode call kills the worker, and no amount of
`try` will catch it.** This is the app's first outbound HTTPS request, so it is the
first thing to hit a Python/OpenSSL mismatch that was always there:

```
OPENSSL_Uplink(00007FF…,08): no OPENSSL_Applink
```

It is a **process abort**, not an exception — `ssl.create_default_context()` alone
triggers it, before any socket is opened, so it is nothing to do with this module and
nothing this module can defend against. Linux (and therefore the deployment) is
unaffected. If you meet it, either set `GEOCODE_URL=` in your `.env` to turn address
search off locally — the picker still takes a typed address and a dropped pin, which
is exactly the fallback it is designed around — or fix the interpreter's OpenSSL. Do
not "fix" it by wrapping the call in a broader `except`; there is no exception to
catch, and the next person will waste an afternoon proving that again.

## 14. A generated QR code is a payload, not a link — and four smaller things

**The one people expect and do not get: scanning a generated code with a phone's camera
shows JSON, not the record.** The camera app will offer to copy the text and nothing more,
because the code holds `{"_uuid": …, "full_name": "Ana Cruz"}` rather than a URL. That is
what was asked for and it is right for the badge case — the codes exist to be read by *this
app's* scanner, which turns them into records — but it is not what somebody tapping a badge
with their phone assumes. A URL mode is the obvious addition and `_uuid` is the piece that
would make it possible; it is deliberately not built.

**`_` is now a reserved key namespace, and that is a constraint on whoever adds the next
payload key.** `_uuid` is safe precisely because a column `name` cannot begin with an
underscore, so it cannot collide with a real column — where a plain `uuid` *would* have,
silently, since `uuid` is a legal column name. Two rules follow: metadata keys must keep the
prefix, and `qrFill.ts` skips every `_`-prefixed key **without reporting it**, so a key added
there is invisible to the scanner rather than an error. If a future key needs to be *acted
on* when scanned, that skip is what has to change.

**One over-long payload fails the whole sheet, deliberately.** Every code in a batch carries
the same fields, so if one record's values do not fit then the field choice is wrong for the
table rather than for that record. Rendering thirty-nine codes and quietly omitting the
fortieth is how somebody hands out a badge that does not exist, which is worse than being
told to take a column off.

**The size advice fires on density, not capacity, and the numbers are not the same.** A code
holds ~2,331 bytes at the error-correction level used, but it stops being *scannable off
paper* well before that: the module count grows with the symbol version, and a version-40
code is 177x177 modules. At badge size those modules are finer than a phone camera resolves.
So the warning starts at version 16 and the hard failure is much later — if somebody reports
a code that "looks fine but will not scan", density is the thing to check, not length.

**Printing is global, and the next print view has to cooperate.** `window.print()` prints the
document, and the sheet lives in a dialog that PrimeReact portals to `<body>` — so "print
only this" cannot be expressed structurally. The arrangement is: mark the subtree
`[data-qr-print]`, stamp `qr-printing` on `<html>` for the duration, and let the single
`@media print` block in `index.css` hide everything else. **A second print view must reuse
that pattern rather than add a parallel one**, or the two blocks will each hide the other's
content. The class is removed on `afterprint` with a timeout fallback, because a browser that
never fires that event would otherwise leave the app blank.

**`tables.settings` merges shallowly.** Sending `qr` replaces the whole `qr` object rather
than merging into it — right, because `columns` and `include_uuid` are one answer to "what is
on the badge" and half of one means nothing. But it does mean a client cannot patch one field
of it, and the frontend sends both every time. Validation is also **per known key**: `qr` is
strict (`extra="forbid"`), everything beside it stays free-form, which is what lets the blob
go on being a blob.

## Behavior that surprises people

**`is_superuser` and the permission model are orthogonal.** A superuser administers
*identities* — creating users, promoting them, editing group membership. It does **not**
grant access to anyone else's workspaces, tables, or records; that is entirely the grant
system's business. The reason identity administration has to be locked down at all is that
it sits *underneath* permissions: groups are principals in the grant table, so whoever can
edit membership can hand themselves any grant a group holds, and whoever can reset a
password can simply become the workspace owner. (Both were open to any authenticated user
until this was fixed; `tests/test_account_security.py` keeps them shut.)

**The last superuser cannot be deleted or demoted.** Superusers are only ever minted two
ways — seeded into an empty users table, or promoted by an existing superuser — so losing
the last one locks account administration out permanently, with no migration tool to undo
it by hand. `UserService._guard_last_superuser` refuses; promote a replacement first.

**Changing your own password requires `current_password`; a superuser resetting someone
else's does not.** The first stops a stolen access token becoming a permanent takeover.
The second is a deliberate admin capability — a superuser genuinely *can* take over any
account, which is inherent to the role, not a hole.

**The workspace owner is an implicit superuser** *of their own data* (unrelated to the
`is_superuser` flag above). `effective_actions` short-circuits to all
actions when `user.id == workspace.owner_id`, before any grant lookup. Owners cannot lock
themselves out of their own data no matter how the grants are arranged, and no grant can
*reduce* an owner's access. This is also why the whole pre-existing test suite stayed green
when permissions landed: `DISABLE_AUTH=True` resolves the caller to the seeded owner.

**"Unique" permissions are opt-out, not opt-in.** A record leaves the table's permission
umbrella only when someone flips its `permissions_inherit` to `false`. New records default
to inheriting, so a row added later to a table with carefully-locked-down records is
immediately visible to everyone holding table `view`. There is no "every new row in this
table starts unique" mode. If per-record access is meant to be a table's default posture,
that needs building.

**A record added from the grid usually vanishes, and that is the view working.** "Edit
cells" grows a draft row at the foot of the grid; pressing ✓ posts it and then refetches
the page. Where the record lands is decided server-side by the same filter, sort, grouping
and `skip` the page was fetched with, so a record made just now under `created_at asc` on
page 1 of 40 belongs at the end of page 40 and is simply not in the answer. The toast says
so ("Not on this page — the current sort or filter puts it elsewhere") rather than leaving
it to be read as a lost write. Nothing is written into the cache optimistically for exactly
this reason: the browser cannot know where the row goes, so it asks.

**A required column the draft row cannot reach refuses the record from the server.** The
draft row's cells are the *create* form's fields (`resolveFields(cols, createForm)`)
intersected with the columns the view is showing, so a column that is required but excluded
from the create form, hidden by the view, or of a type with no cell-sized editor (rich text,
multiline) has nowhere to be typed. ✓ then 400s with "Column 'x' is required" — correct, but
you meet it as a server error rather than as a disabled button, because the client does not
evaluate `required` itself. The row's "Open in the record form" action is the way out: it
hands everything typed so far to the Add dialog, which lays out every field the form has.

**Breaking inheritance is reversible.** Flipping `permissions_inherit` back to `true`
re-joins the parent chain, and grants made while the child was independent are kept and
simply re-union with the inherited ones.

**`TableRead.workspace` exposes only a uuid, deliberately.** A table can be granted without
its workspace, so serializing the workspace's `display_name` would leak the name of a
workspace the caller was specifically not given access to. This is why the "Shared with me"
section shows the table's own slug as its subtitle rather than "in «Workspace»".

---

## Gotchas for whoever edits this next

**Alembic owns the schema — but autogenerate is blind to new enum values.** The app no
longer creates tables (`init_db` only *verifies*); run `alembic upgrade head` first. The
workflow is in the README. The trap:

> `alembic revision --autogenerate` diffs **tables and columns, not enum labels**. Adding
> a member to `ColumnType` produces a migration whose `upgrade()` is literally `pass`.
> This is exactly how `invalid input value for enum column_type: "time"` reached a running
> server. Verified: add an enum member and autogenerate emits nothing.

So an enum change must be hand-written, and `ALTER TYPE ... ADD VALUE` cannot run inside a
transaction that then uses the value — it needs Alembic's autocommit escape:

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'time'")
```

Postgres has no `DROP VALUE`, so these are effectively one-way; a downgrade would have to
rebuild the type.

**The startup drift guard is the backstop for exactly that.**
`src/infrastructure/schema_check.py`, run from `init_db`, compares the models against the
live database and **refuses to start**, printing the SQL to run. It catches a forgotten
migration, an incomplete one, and the enum hole above. Drift is reported, never applied —
schema is not mutated behind your back, and with several workers that would race.

**`use_alter` foreign keys need hand-holding in migrations.**
`tables.primary_column_id → columns.id` is circular (`columns.table_id → tables.id`), so the
model marks it `use_alter=True`. SQLAlchemy omits such keys from `CREATE TABLE`, and
autogenerate does **not** emit the follow-up `ALTER` — so the generated baseline silently
had no such constraint until it was added by hand. `tests/test_migrations.py` pins it.

**Every model must be listed in `src/infrastructure/model_registry.py`.** That one module
is imported by `main.py`, `tests/conftest.py`, and `migrations/env.py`. A model missing
from it fails three different silent ways — worst of all in `env.py`, where autogenerate
sees the table as absent from the models and emits a **`DROP TABLE`**.

**Grant expiry is enforced in exactly one predicate, and new grant-reads must go
through it.** A grant may carry an `expires_at`; the "still live" check
(`or_(expires_at IS NULL, expires_at > now())`) lives in
`PermissionService._principal_condition`, which every authorization read funnels
through — `effective_actions`, `effective_actions_bulk`, and the three collection
filters (`workspace_view_filter`, `table_query_filter`, `record_view_filter`). So an
expired grant is invisible everywhere at once. Any *new* path that queries
`permission_grants` for authorization must use `_principal_condition` (not roll its
own principal clause) or it will silently honor expired grants. Compared against DB
`now()`, not a Python timestamp. The owner short-circuit is above the grant lookup,
so **owners never expire** — only grants do. Management paths (`upsert_grant`,
`list_grants`, `revoke_grant`) deliberately build their own clause so a re-share can
still find and update an already-expired row; `upsert_grant` writes `expires_at`
verbatim, so a re-share that omits it clears the expiry (the Share panel resends it).

**Never lazy-load a relationship after a commit.** The session factory sets
`expire_on_commit=False` precisely because a lazy reload under asyncpg raises
`MissingGreenlet`. In particular, `PermissionService` never touches `user.groups` — it
queries `user_group_association` directly, because the `DISABLE_AUTH` user isn't loaded
with groups eager-loaded and the lazy load would blow up outside the greenlet.

**`PermissionService` is imported lazily inside functions, on purpose.** `permissions.services`
imports from `auth.users.services` → `workspaces.services`, so a module-level
`from src.permissions.services import ...` in `workspaces/services.py` or `tables/services.py`
creates an import cycle. The deferred function-local imports in `list_visible`, `TableService.list`,
`TableRecordService.list`/`query`, `ViewService.list`, and `FormService.list` are load-bearing.

**Moving a table between workspaces must assign the `workspace` relationship, not the
`workspace_id` FK.** `TableService.get_any_by_uuid` eager-loads `Table.workspace`
(`selectinload`), and SQLAlchemy's unit of work treats a *loaded* many-to-one as
authoritative: on flush it writes the FK from the relationship, **silently overwriting a
bare `lst.workspace_id = …` assignment** and leaving the table where it was. Both
`TableService.update` (the cross-workspace move) and `transfer_to_user` therefore do
`lst.workspace = target`. This was latent for the whole life of the move feature — it had no
UI and no test, so nothing ever exercised it — and `tests/test_table_transfer.py` now pins it.
Any new code that re-parents a table has the same trap.

**Ownership transfer is the "email" model: a table is pushed, not offered.** Handing a table to
another user (`POST /tables/{uuid}/transfer`) moves it into that user's **immutable `incoming`
workspace** (`is_system`, minted lazily by `WorkspaceService.get_or_create_inbox`, name
reserved so an ordinary workspace can't occupy the slot). There is deliberately **no
accept/consent step** — anyone who *owns* a table can drop it on anyone, exactly as email lets
anyone write to you; the recipient's recourse is to move it out or delete it. The authorization
is owner-only *on the source* (ownership flows through the workspace, so the caller must own the
table's current workspace) — **not** `configure` on the target, which is why transfer is a
separate endpoint from the generic move (nobody holds `configure` on a stranger's inbox). The
inbox is guarded like a system group: `WorkspaceService._guard_system_workspace` refuses to
rename or delete it (its *tables* stay ordinary, so the recipient can still move or delete them).
Existing grants survive the move (they key on the table's `resource_id`, unchanged), so the
giver's previously-shared users keep access; only the giver's *implicit owner* access ends. A
name clash on landing auto-suffixes rather than 409ing.

**Cloning a table remaps column uuids; get it wrong and records render blank.** `POST
/tables/{uuid}/clone` (`TableService.clone`) copies a table into the same workspace — structure
always, records only when `with_records` is set. The clone's columns are new rows with new
uuids, so record `data` is keyed against columns that no longer exist unless every reference
is rewritten through an old→new column-uuid map: the `data` keys, `primary_column_id`, and
the column-uuid refs inside view configs (`filters`/`sorts`/`group_by`/`hidden_columns`/
`column_order`) and form configs (`fields[].column_uuid`). Cell *values* are copied verbatim
— choice labels and lookup/user/group target uuids stay valid — and a **self-referencing
lookup is deliberately left pointing at the original table** (config and cells), so the clone
"reaches into" its source rather than needing a row-uuid remap; pointing it at the clone
instead is the deferred alternative. Two easy-to-miss rules: cloning must **not** call
`_provision_defaults` (it copies the source's real columns/views/forms, so seeding defaults
would duplicate them), and it must **not** copy grants (the clone is a new resource the cloner
owns, private like any new table). `tests/test_table_clone.py` pins the remap.

**The PrimeReact theme hardcodes its colors.** `lara-light-cyan/theme.css` defines
`--primary-color` / `--surface-*` and then never references them (zero `var(--primary-*)`
usages in the file), so overriding those variables recolors nothing. Theming works only
because `themes.css` is a custom, **unlayered** override sheet imported **last** in
`main.tsx` — unlayered rules beat `@layer`ed Tailwind utilities, and last-import wins on
source order. Reordering those imports silently breaks every theme.

**API types are generated; `config` blobs are not.** `frontend/src/lib/types.ts` re-exports
`api-schema.ts`, which `npm run gen:api` builds from the backend's OpenAPI document — so a
renamed or removed field breaks the frontend build rather than drifting silently. **Run it
after any backend schema change.**

What it *cannot* generate is the shape of a `config`: a column's, view's and form's config is
a `dict` to Pydantic — its contents depend on the column type and are validated per type — so
OpenAPI says only "object". `ColumnConfig` / `ViewConfig` / `FormConfig` in `types.ts` are the
frontend's own claim about those blobs, and are what can still quietly go stale.

**`TableSettings` joined them**, for a slightly different reason worth distinguishing.
`tables.settings` is not "typed per column type" — it is a genuinely free-form bag, validated
server-side only for the keys this build understands (`qr` today) and passed through
otherwise, because stripping a key a newer deploy wrote would destroy it on the next write.
So OpenAPI says "object" there and always will, and `TableSettings` is the frontend's claim
about the keys it reads. Read it through `tableSettings(lst)` rather than casting at each call
site, so there is one place to update when a second key appears.
Change a config's shape in the backend and nothing will tell you; change it here too.
Change a config's shape in the backend and nothing will tell you; change it here too.

The endpoints are still untyped, too: `api.get<T>(path)` takes the caller's word for both.
A typed client (`openapi-fetch`) would close that, at the cost of touching every call site.

**A collection's visibility is resolved in bulk, never per row.** `ViewService.list`
and `FormService.list` used to loop `PermissionService.has(...)` per row, with a
comment saying "few views per table" — true in practice and enforced by nothing: there
is no cap on views or forms, and both listings are fetched on every table open, so the
cost was a `_security_context` walk plus a grant SELECT per row per page load. Both go
through `effective_actions_bulk` now, which the records router already used and which
answers in a fixed number of queries. Any new listing that filters by permission
should reach for it rather than the loop.

**Permission-gated controls are hidden while their query is in flight.** `useEffectiveActions`
returns an empty set until the request resolves, so buttons appear a beat after page load
rather than showing and then vanishing (which would let someone click a control they don't
have). Grant and inheritance changes invalidate the cached `["effective"]` queries.

**Every response carries an `X-Request-ID`, and the 500 is the one that needed it
most.** `src/infrastructure/observability.py` stamps each request, echoes the id, and
logs one line per request; `@app.exception_handler(Exception)` logs the traceback
against that id and answers a body that says only "Internal server error". Before
this the app had two loggers in the whole tree, no configuration, no correlation and
no catch-all — a production 500 was a traceback in an ephemeral log stream on a
single instance, with nothing to match it to the report that arrived an hour later.

Two details are easy to get backwards and were, first time:

- **The access line is logged before the ContextVar is reset**, not after. Reset
  first and the one log entry guaranteed to have an id is the one that answers `-`.
- **The id is put on `request.state` as well as in the ContextVar.** Starlette's
  `ServerErrorMiddleware` — which runs the handler on an unhandled exception — sits
  *outside* the app's own middleware, so by the time it is reached the `finally` has
  already run. `request.state` is backed by the ASGI scope and survives, which is
  what lets the 500 carry the same id as the access line for the request that
  produced it.

Third-party loggers are pinned to WARNING regardless of `DEBUG`. Without that,
`python_multipart` writes a line per field byte-range of every uploaded form and
`asyncio`/`httpx` a running commentary — which buries the app's own lines and makes a
single test unreadable.

**`MAX_REQUEST_BYTES` bounds every request body, and is not `MAX_UPLOAD_BYTES`.**
`core/uploads.py` capped a *file* while reading it; nothing capped a JSON body, and
`TableRecordCreate.data` is a `dict` to Pydantic while a `text` column's `max_length` is
unset by default — so one request could buffer, parse, sanitise and store tens of
megabytes on a 512 MB instance. `BodySizeLimitMiddleware` checks `Content-Length`
first (cheap, catches every ordinary client) *and* counts the stream (a chunked
request has no header to check), answering 413.

**The two limits must not be set equal**, and that is the part to get right. The
upload limit is the *good* error — the route checks it and names it in the message.
This one is a backstop for bodies nothing else bounds. A multipart request carrying a
file exactly at `MAX_UPLOAD_BYTES` is a body slightly *over* it, because of the
boundaries and headers around the file — so equal values make the backstop reject
uploads the route was about to accept, with the worse message.
`test_endpoint_rejects_an_oversized_file` catches exactly that, and did.

A per-cell text ceiling is a separate and narrower question — a `text` column can
legitimately hold a long document.

**`/auth/tokens/refresh` and `/revoke` are throttled by address, in their own
bucket.** Both are unauthenticated by construction — presenting the token *is* the
authorisation — and neither had any limit. A refresh token is a bearer credential, so
an unbounded route trading one for an access token is a guessing surface, and each
attempt is a database lookup on the single instance. The bucket is deliberately
*not* login's: sharing it would let a stale session retrying in a background tab push
an address towards a **login** lockout, so the person could not sign in to fix the
thing that was failing. `/revoke` reads the bucket but never fills it — it answers 204
whether or not the token was real, on purpose, so it does not know what a failure was.

**A request must never hold two pooled connections at once.** Export streams rows on a
server-side cursor *and* resolves reference labels — the obvious way to write that is a second
session for the label queries, because "you can't query a session mid-stream". You can: the
label queries run **between** fetches from the cursor, never during one, and asyncpg serves
them on the same connection (`test_export_larger_than_a_batch_resolves_labels_mid_stream`
drives 1200 rows through it). Taking a second session instead would have each export hold two
connections for its whole duration, and enough concurrent exports — each holding one, each
waiting on a second — exhaust the pool and stall until `pool_timeout`. Reordering the
acquisitions does not help: a session grabs its connection at its *first query*, not at its
first stream.

**Every read path over records must apply `record_view_filter`.** Per-record permissions are
only as strong as the weakest path that returns records. Export used to miss this — it
streamed rows filtered on `table_id` alone, so a table-viewer could export records whose
inheritance was broken and were never granted to them, even though the grid hid them
correctly. Fixed, and `test_http_export_respects_item_permissions` guards it. Any *new*
endpoint that returns record data needs the same filter; a table-level `view` check is not
sufficient.

**And "returns record data" includes copying it somewhere the caller can read.**
`TableService.clone` missed this for the whole life of the feature, and it was the worse
half of the same bug: a clone does not hand the rows back in the response, it writes them
into a table the caller owns, where `_COPY_RECORDS` sets `permissions_inherit = true` and
they can be read at leisure afterwards. The route requires `configure` on the workspace,
which confers `view`, so ordinary table access was enough to lift a record whose
inheritance was broken. `clone` now takes the acting user and applies `record_view_filter`
to its keyset select, and `test_clone_respects_item_permissions` guards it. Note the
`permissions_inherit = true` is *correct* given that filter and wrong without it —
everything copied was visible to the cloner, and the clone starts private with no grants.
Carrying the source's flag across instead would be worse than either: a record copied
under `permissions_inherit = false` inherits nothing and has no grant of its own, so it
would be invisible to everyone except the workspace owner, including the person who just
asked for the copy.

**The filter language fails towards "everything", which is the wrong direction, and three
places used to.** A filter that cannot answer a question should say so; each of these
instead returned rows, which reads as data rather than as a mistake and which a saved view
then repeats forever.

- **An unknown operator was dropped, not refused.** `_condition` returns `None` for an
  operator it does not recognise and `apply_filters` drops that condition — so a typo in
  the only condition yielded the *unfiltered table*. `FilterCondition.operator` is now a
  `Literal` over `filtering.FILTER_OPERATORS`, the tuple `_condition` actually answers, so
  it is a 422 on write and on the ad-hoc `filters` param. Note what this deliberately does
  **not** do: `ViewRead.config` is a plain `dict` and `TableRecordService` reads
  `view.config or {}` raw, so a view saved before this still loads and still behaves as it
  did — the type gates writing one, not reading one. `frontend/src/features/views/
  operators.ts` already warned that adding an operator on one side only "gets a filter that
  silently matches everything"; this is that warning enforced from the other end.
- **`contains` built its pattern by interpolation.** `%` and `_` are LIKE wildcards, so a
  condition of `%` matched every non-null cell and `a_c` matched `abc`. It goes through
  `like_pattern()` with `escape=LIKE_ESCAPE` now, like every other ILIKE in the app already
  did — this was the one that did not, and it is the one whose values get saved and re-run.
- **A JSON boolean never matched a `checkbox`.** JSONB spells booleans `true`/`false`, so
  `data ->> key` answers `"true"` while Python's `str(True)` is `"True"`. `_as_text` now
  carries the same `bool` branch `transfer.services._csv_cell` and
  `columns.conversion._render_scalar` both already had. The web editor dodged this because
  it gives a checkbox column a free-text box (fixed separately); every other client hit it.

All three are pinned in `tests/test_view_filtering.py`.

**A page of records can be asked for two ways, and the cursor is not just a faster offset.**
`GET /records/` takes `skip` — what a numbered paginator needs, since page 7 *is* an offset —
or `after=<record uuid>` (`TableRecordService._after_id`). `after` is a **400** in a sorted,
grouped or `skip`ed listing rather than an approximation: `id > :after` names a position only
while `id` is the ordering, and a caller cannot tell from the rows it gets back that it
quietly lost some. A uuid this table does not have, including one since binned, is refused for
the same reason — starting from the top would read as the table rewinding. Three things follow.
The cursor is a bare record uuid rather than an encoded blob precisely so it cannot carry a
stale ordering; don't "improve" it into one. What it fixes is not only depth cost but a
correctness hole — `skip` names a position, so a record deleted ahead of it pulls every later
row back a place and the next page steps over one nobody saw, which a bin any editor can fill
makes routine rather than a race. And sorted listings remain offset-only and are slow
*everywhere* rather than slow deep (they sort an unindexed expression over the whole matching
set), so keyset would not have helped them; that is the path to attack next, not this one.
The count is now a separate question — `total=false` suppresses `X-Total-Count` for a caller
that already holds it, `GET records/count` asks it alone, and the page is fetched *before* the
total so a rejected `after` pays for no count at all.

**"Live records" is server push, and its refresh must stay a permissioned re-read — never
a data push.** An open table holds an SSE stream (`GET /tables/{uuid}/events`) and refetches
when told that table changed; if the stream cannot be held open it falls back to the
polling this replaced, every `VITE_LIVE_REFRESH_SECONDS` (default 10, `0` disables; the
Render deploy sets 15). Both paths go through the `useLiveTable` hook
(`frontend/src/lib/useLiveTable.ts`), and a dearer caller may only ever *slow* its own
refresh, via the `minSeconds` floor the metric strip passes (§9) — under polling that
raised the interval, under push it throttles the invalidation. Nothing refreshes at all
while the tab is hidden or after 5 minutes with no user activity on a visible one; that
guard now lives in `liveStream.ts`, where it can close the stream and stop the fallback
timer with one answer instead of two.

The whole thing works precisely *because* every refresh is an ordinary `GET /records/`, so
it inherits the `record_view_filter` above and can never surface a row the viewer can't see
— the client is told nothing except "look again." **Five things about the push path are
load-bearing, and each one is quiet when broken:**

- **An event carries only "table changed," never record data.** A fan-out of rows would
  bypass the per-record filter every subscriber is entitled to differently. The frames are
  `event: records` and `: ping`; there is nothing else on the channel and nothing else may
  be put there.
- **The stream holds no pooled DB connection** (see the two-connection rule below), or a
  long-lived subscriber pins a session for its whole life and ~15 of them empty the pool.
  This is why the route is **not** on the tables router: that router applies
  `Depends(get_current_user)` → `Depends(get_db)`, and a `yield` dependency is released
  only when the response completes — which for a stream is when the tab closes. It is
  mounted directly in `main.py`, and does its auth and `view` check by hand inside a
  session it closes before streaming. `test_an_open_stream_does_not_hold_a_pooled_connection`
  is what catches a regression; nothing else would, until production hung.
- **Push opens a timing side channel that polling did not.** A viewer with table `view` but
  restricted per record learns the instant *any* record in that table is written, including
  ones they cannot see — their refetch then shows no change. Closing it would mean
  filtering per subscriber, which needs record data on the channel, which is the first
  rule. It reveals timing, never content, and that is the accepted trade.
- **Authorization is checked at connect and never again**, because there is no session to
  re-check with. `SSE_MAX_STREAM_SECONDS` (300) is therefore the **revocation window**:
  losing `view` on a table stops the signals reaching that subscriber at their next
  reconnect. Nothing leaks meanwhile — the events carry nothing and every refetch is
  separately permissioned — but the tap closes late, and that setting is how late.
- **`notify` goes after the commit, never before.** A watcher woken early re-reads the old
  rows and then believes it is current, which is worse than not being told. The scrub a
  table or workspace delete runs across *other* tables is the awkward case: it does not own
  its transaction, so it returns the affected table ids and its callers signal after they
  commit.

Two operational notes. On Render's free tier a focused, open tab polling kept the single
instance awake and spent free hours doing it; the API is on `starter` now, which is
always-on, so that is gone. And **whether Render's static-site rewrite buffers
`text/event-stream` is unverified** — the SPA reaches `/api/*` through it
(`render.yaml`). The response sets `Cache-Control: no-cache, no-transform` and
`X-Accel-Buffering: no`, but if frames still arrive only when the connection closes, the
answers are to point the events request at the API origin directly (which means populating
`ALLOWED_ORIGINS`, since CORS otherwise never enters the picture) or to set
`SSE_ENABLED=False` and stay on polling. That last one is the kill switch, and it is an
API environment change rather than a frontend rebuild on purpose.

**A `user` cell must never resolve to a person's real name.** `/auth/users` is readable by
any authenticated user (the Share dialog needs to table principals), but person records are
private — owner or superuser only. So `user` columns label by **username**, in both
`PrincipalControls.tsx` and the export formatter. Swapping in "first + last name" because it
reads nicer would hand every viewer of a table the personal details of everyone referenced
from it.

**Every reference column exports its label, and the two sides must agree on what the label
is.** `lookup`, `user` and `group` all store a uuid and export the human-readable label
(row label / username / the group's slug `name`, not its `display_name` — only the slug is
unique, so only the slug re-imports); import resolves it back. The load-bearing detail is
that export and import both call `display_column_key` to decide *which* column of the target
table supplies a row's label — if they ever picked differently, a file this app exported would
not import back into it. A label matching two rows is rejected rather than guessed (row
labels, unlike usernames, are not unique), and a row whose label is empty exports as its uuid,
because that is the only thing that identifies it. One consequence to know about: a
multi-valued cell is serialized to CSV as `a;b`, so a label containing `;` will not survive a
CSV round-trip (it does in JSON).

**Reference pickers search server-side; two stragglers still fetch `?limit=1000`.** Every
picker over an unbounded set has the same two jobs, and they need different answers:

- *Search* — a typeahead (`q=…`), because a dropdown listing every option silently stops
  offering the ones past its fetch limit.
- *Resolve* — turn the uuids a page already holds into labels, batched: `?uuid=…&uuid=…`
  (capped at 500), **one request per column per page, never one per cell**.

`PrincipalControls` (users/groups) and `LookupControls` (target rows) both do this now; the
lookup one goes through `GET /tables/{table}/columns/{column}/options`, which is server-side
precisely because only the server knows a row's label (`display_column_key`) — and it applies
`lookup` on the target table plus the per-record filter, so a picker can never offer a row its
user cannot read. Still to convert: `GroupDialog` (member picker) and `PeoplePage`
(person-name annotations).

**A lookup can target any table its creator can `lookup`, and cross-workspace targets make
"degrade to uuid stubs" a routine state, not a rare one.** The config-time check was
relaxed from "a table you own" to "a table you can `view`" (`ColumnService._validate_config`),
and then to "a table you can `lookup`" — which `view` confers, so that second step widened
the set without changing any existing answer. A lookup may therefore point at another of
your workspaces, at a table shared to you, or at one shared *only* as a lookup target.
Nothing about resolution changed — it re-checks the *reader's* access to the target
per-caller (the note below), which is what keeps a cross-owner target from leaking. What
*does* change: a target you don't own can have your access revoked or be deleted entirely
at any time, independent of the referencing table. When that happens the cells fall back to
uuid stubs (`LookupCell`, `retry: false`) exactly as they always could — it just stops
being exceptional. A target the creator cannot read is refused indistinguishably from one
that doesn't exist, so the `target_table_uuid` field can't be used to probe for a stranger's
tables. `tests/test_lookup_options.py` pins all three: own-other-workspace, shared-in, and
refused-when-unviewable.

**Writing a lookup cell obeys the target's permissions too, and for a while it did not.**
The relaxation above was applied to the config-time check and not to the write path, which
went on resolving the target scoped to the owner of the table holding the column
(`TableRecordService._lookup_target_id`). The two disagreed exactly where it mattered: a
cross-owner target the config-time check accepted produced a column that created cleanly and
then refused every value with "points at a missing target table" — reads fine, writes never.
The owner scope is now gone, replaced by `lookup` on the target, the same action the read
path takes. That check is not redundant with the read path's: `_validate_lookup` resolves a
row by its **label** as well as its uuid, so an unguarded write is a way to probe an
unreadable table by guessing. It is skipped only where there is no caller to check — a column
conversion re-validating stored data, and the demo seeder. `test_lookup_grants.py` pins both
halves: `test_a_cross_owner_lookup_accepts_a_write` and
`test_writing_a_lookup_takes_lookup_on_the_target`. Note the tightening in that second one:
a grantee who may edit the host table but was never given anything on the target can no longer
write that cell. They could not see the picker either way — the options endpoint always
required `lookup` — so this makes the write agree with the read rather than taking anything
away that was usable.

**Resolving a label means reading the target table, so it obeys the *target's* permissions,
not the exported/edited table's.** `view` on a table must not hand over the contents of a
different table it happens to point at. The options endpoint enforces this, and so does export
(`_lookup_row_labels`) — a caller who cannot read the target gets uuids instead of labels,
which is what they saw before labels existed. Any new surface that resolves a lookup label
needs the same check; `test_export_does_not_leak_labels_from_an_unreadable_target` pins it.

**The `lookup` action is that check's other half: a table can be shared *as a lookup target
only*.** Granting `lookup` lets other people's lookup columns resolve the table's labels
while the table stays out of `GET /tables/`, out of "Shared with me", and refused by
`GET /tables/{it}` — a reference table ("Schools", "Departments") without handing it over.
Three things this rests on, all easy to break:

- **The collection filters must test actions, not grant existence.** `table_query_filter`
  and `workspace_view_filter` now require `CONFERRED_BY[view]` via jsonb `?|`. Revert
  either to matching any grant row and the lookup-only table reappears in "Shared with me",
  silently undoing the grant. (This also settled a pre-existing oddity: a grant with an
  empty action table used to table the resource while permitting nothing on it.)
- **`record_view_filter` takes the action being exercised.** It short-circuits on the
  table-level check, so at the default (`view`) a lookup-only grantee falls through to
  "only records granted individually" — an empty picker. The three label paths (options,
  export, search by linked label) pass `lookup`. Per-record grants still apply either way:
  `lookup` relaxes the table-level gate, not row-level ones.
- **The target picker needs `GET /tables/?lookup_targets=true`.** A table you cannot name is
  a table you cannot point a column at. That flag is the single deliberate exception to
  "lookup-only tables are invisible", it is off by default, and `ColumnDialog`'s shared-tables
  query is its only caller. The schema stays hidden even then — `GET /tables/{it}/columns/`
  is still refused, so the dialog hides the label-column dropdown (`targetSchemaHidden`) and
  the server's fallback picks the label. `tests/test_lookup_grants.py` pins all of it.

**A row's label falls back: the lookup's `display_column_uuid`, else the target's primary
column, else its *first* column.** That last step is load-bearing — `primary_column_id` is
only ever set by an explicit PATCH and is nulled when that column is deleted, so most tables
have none. Drop it and every lookup at such a table silently degrades to uuid stubs *and* its
picker's search matches nothing.

Three traps worth knowing if you touch these hooks.

**A cell must subscribe to its own label query — never take a resolved map as a prop.**
PrimeReact's `DataTable` re-renders its body only when `value` (the rows) changes. Labels
arrive *after* that, so a map computed in the page and passed down freezes at whatever was
resolved when the rows last changed: assign a user to a record and the cell sits on `…`
forever, even though the page-level hook has the name. `PrincipalCell`/`LookupCell` therefore
take the page's *uuid list* and call the hook themselves. Every cell of a column passes the
same table, so react-query serves them all from one request — the batching survives.

**The query key is the uuid set**, so it changes whenever the records do — including the blip
where a view swap empties the grid mid-refetch. Without `placeholderData: keepPreviousData`
the labels drop out and every cell falls back to a stub.

**"Not resolved yet" must never render like "row/account is gone".** An in-flight uuid shows
`…`; only a settled request with no match shows `unknown user` / the row stub. Note this must
key off `isFetching`, **not** `isPending` — with `placeholderData` the status is already
`success` while the new uuid set is still in flight.

**Record search matches reference columns by label, and that means it obeys the *target's*
permissions.** A `lookup`/`user`/`group` cell stores a uuid while a person types the label, so
`?q=` resolves the label to uuids first and then matches cells with `?|` (true for a single
value and for one element of a multi-valued cell alike). Resolving a lookup's label is a read
of the target table, so it takes `view` on that table plus the per-record filter — otherwise
search becomes a way to probe the contents of a table you were never given. A caller without
that access simply gets no match on those columns, and
`test_search_by_lookup_label_obeys_the_target_tables_permissions` pins it. A label resolving to
more than `_SEARCH_REF_MAX` (200) rows stops widening there — a search matching thousands of
target rows is not a search anyone meant to run.

**PrimeReact's AutoComplete overwrites the input's text on every render.**
`updateInputField(props.value)` runs in a `useUpdateEffect` with **no dependency array**
(`primereact/autocomplete/autocomplete.esm.js:977`), so after *any* re-render it replaces what
you typed with the label of the currently *selected* value. Fetching suggestions re-renders,
so the search text vanished the moment the results arrived — for single-valued pickers only
(`!props.multiple` guards it). `ReferencePicker` works around it by holding the typed text in
state and passing it as `value` while typing: `formatValue` returns a string unchanged, so the
effect writes the same characters back. Every uuid-backed picker (lookup, user, group) goes
through that one component; don't hand `AutoComplete` a `value` you don't control.

It also avoids `forceSelection`, which *clears the model* on blur when the text doesn't match
an option — so typing a few letters and clicking away would silently drop a value that was
already set. Blur restores the selected label instead; only an emptied box clears the value.

**Records have two renderers, and they must never disagree.** Below 640px
(`SMALL_SCREEN` in `lib/useMediaQuery.ts`) the grid is replaced by `RecordCards`, because
twelve columns across a phone is twelve slivers. It is a second *layout*, not a second
implementation, and the seam is where the bugs live:

- Values go through the shared `renderCell`. Ordering was duplicated once and the copies
  drifted — the grid sorted with PrimeReact's comparator (blanks float to the top on a
  descending sort, `"item10"` before `"item9"`) and the cards with ours, so the same table
  came out in a different order depending on the width of the screen. Neither sorts now:
  the grid is `lazy`, both layouts render the page the server already ordered, and there
  is one comparator because there is none. `sortRows` survives only as `compareValues`,
  which `ColumnsPanel` uses to order a column *table*, not records.
- Reference cells used to sort by the **uuid** they hold rather than the label they show —
  uuid7 is time-ordered, so a lookup sorted by creation order scrambled by hex. They now
  sort server-side by the resolved label: a `lookup` by its target row's display column, a
  `user` by username, a `group` by display name (`records/references.py`). A multi-valued
  cell sorts by its *first* link, the only single sortable reading of one. The reach into
  the other table can fail — no `lookup` on the target, a target that is named and
  missing, a target with no column to read a label from — and every one of those falls
  back to the old uuid ordering rather than failing the listing, which is also the order
  on screen for a caller who only ever sees row stubs. A target row the caller may not
  `lookup` sorts as empty rather than by its real label; ordering by it would hand back
  the one thing a row-level grant withholds.
- Anything new the grid grows (a column header affordance, a row action) has no home in a
  card unless you give it one. Sorting is the example: it lived in the column headers, so on
  a phone it did not exist at all until a "Sort by" dropdown was added to the toolbar.

**Every dialog must be `components/AppDialog`, not `primereact/dialog`.** The px width each
one is designed at (`style={{ width: 560 }}`) is wider than a phone; `AppDialog` applies the
breakpoints that override it and caps the content height so the buttons at the bottom of a
long form stay reachable. A raw `Dialog` looks fine on the machine you wrote it on and
overflows the screen it ships to.

**Toggling a column's `multiple` does not rewrite its records — on purpose.** Measured: that
UPDATE takes ~1.4s over 100k rows and **~25s over 1M**, holding row locks on every record, in
one transaction, inside a web request. So the toggle stays O(1) and the data is allowed to lag
the setting that shaped it.

The mismatch is absorbed in **four** places, and nowhere else has to know:

- **Reads** — `coerce_shape` is applied as records leave `TableRecordService` (and in the export,
  which streams raw JSONB and bypasses that path). A single column returns the array's first
  element; a multiple column returns a bare value as a one-element table. Put this in the UI
  instead and every surface — grid, editor, filters, forms, the next page someone writes — has
  to remember it, and one of them won't.
- **Writes** — `_validate_data` coerces the incoming value the same way, so a client echoing
  back the record it was given is never told its own data is invalid.
- **The scrub** — `_SCRUB_SQL` **never asks the column what shape it is**. One statement, whose
  predicate (`?|`) matches a scalar *and* an array element, and whose `CASE` rewrites each row
  per the shape it actually has. This is the one that matters: the scrub used to pick its SQL
  from the column's *current* `multiple`, so after a toggle it matched nothing and left the
  deleted user's uuid sitting in the record. Nothing to keep in step, so nothing to get out of
  step.
- **Filters and sorts** — `_field` in `records/filtering.py` reproduces `coerce_shape`'s rule *in
  SQL*, because this is the one reader whose comparison happens in Postgres rather than in Python:
  `CASE WHEN jsonb_typeof(data -> key) = 'array' THEN data -> key ->> 0 ELSE data ->> key END`.
  This edge was **missed for a long time**, and the cost was severe precisely because it was
  silent — a view filtered on a freshly-toggled column returned *no* un-converged records at all.
  `->>` on a JSONB array yields its serialized text, so a cell holding `["Annex"]` compared as
  `["Annex"]` and matched nothing; an empty `[]`, which every other reader calls blank, compared
  as the string `[]` and so was invisible to `is_empty`. Sorting ranked the leading `[` rather
  than the value behind it, though how visibly depends on the server's collation — punctuation
  sorts ahead of letters under `English_United States.1252` and is largely ignored under a glibc
  `en_US.UTF-8`, so the same data ordered differently on a developer's machine and the server.
  Note the fix keys off `jsonb_typeof`, **not** the column's `multiple`: asking the column would
  be wrong for exactly the rows that disagree with it, which is the same trap `_SCRUB_SQL` was
  written to avoid. A column that genuinely declares `multiple` is left comparing against the
  serialized array, since `contains` searching the whole array is the nearest thing the filter
  language has to element-wise matching — giving multi-valued columns real per-element operators
  is separate work, and not done.

`src/maintenance/reconcile_shapes.py` converges the stored data afterwards (chunked, committed
per batch, idempotent, killable — run it from cron). **Nothing depends on it having run**: it
exists so the tolerance stays a migration aid rather than permanent scaffolding. It includes
soft-deleted records, which a restore would otherwise bring back in a shape their column no
longer accepts.

**Soft delete destroys nothing — that is the whole contract.** Deleting a record, a user or
a group leaves every cell pointing at it *intact*. The cell still holds the uuid, still resolves
(the target comes back flagged `deleted`), and renders as `Springfield High (deleted)`. Only the
**purge** clears it, once the target is past the retention window and going for good.

The old behaviour scrubbed those references at delete time, which quietly made soft delete
irreversible: the pointers were destroyed and nothing remembered them, so a restore would bring
a record back stripped of every inbound relationship. Restore exists now (gap 6); this is what
made it possible.

Three rules hold it together, and all three are easy to get backwards:

- **Resolving is not offering.** `lookup_options` (and `/auth/users?uuid=`) return deleted
  targets when asked for *specific uuids* — a grid labelling what its records already hold —
  and never when *searching*. Nothing new may be linked to something on its way out.
- **A stored reference stays saveable.** `_validate_data` passes the cell's current uuids down
  as `kept`; a reference in that set is accepted even if its target is deleted. Without it,
  binning one row makes every record naming it unsaveable — a fine way to lock a table.
- **The clear happens once, in `_references.clear_expired`**, driven by the same retention
  window. Shape-agnostic (`?|` plus a `CASE`), set-based (one UPDATE per column, not per row).

**Deleting a *table* is the deliberate exception** and still scrubs. Every value in a lookup
pointing at it goes stale at once, and the permission resolver returns *no access* for a
deleted resource, so those labels could not even be authorised. The old justification — that
restoring a table would mean un-cascading its children, which nothing did — has expired:
`TableService.restore` does exactly that, matching each child on the `deleted_at` the cascade
stamped. What has not changed is the asymmetry that makes the scrub a real gap: the restore
brings the table's own contents back and cannot bring back what was cleared on *other* tables'
records, because nothing recorded which cells those were (gap 6, "What is still missing").

**The scrub clears the referencing column's *config* as well as its values**, and for a long
time it cleared only the values. A lookup column left naming a table that no longer exists was
not merely stale, it was stuck: `lookup_options` raised, so the options endpoint 404'd and with
it the picker, the record dialog and every label resolution; `_validate_config` refused any
PATCH that resent the config, which `ColumnDialog` always does; and the dialog rendered the dead
uuid as `…` under the hint about tables shared as lookup targets only — an explanation that was
actively wrong. Nothing in the UI could repair it except deleting the column.

`target_table_uuid` now goes, and with it `display_column_uuid`, `target_filter_column_uuid` and
`option_filters`, each of which names a column *of the dead target*. `parent_column_uuid` goes
too, though it names a sibling on the column's own table that is still alive — the pairing is set
and unset as a unit, and its other half has just been blanked. `required` goes as well, and that
one is not tidiness: it is enforced over the merged record on every write, and the cell is now
empty and unfillable, so a ticked `required` would refuse every create and edit on the table that
holds the column — and could not be un-ticked on its own, since a config PATCH merges onto what
is stored and the refusal would then be about the missing target instead. Dropping it follows
the same rule that refuses `unique` + `multiple`: a column must not claim a guarantee it cannot
enforce. Finally the scrub runs `_clear_dependent_nesting` outward on each column it clears, so
a nested child whose *own* target survives — and which is therefore not in scope for the scrub —
does not keep a pairing whose halves resolve to nothing.

The column degrades to an unconfigured lookup: the options endpoint answers with an empty table
rather than raising (the same tolerance §7 already grants a dangling nesting pairing), the
picker opens empty, and retargeting is an ordinary PATCH. Note the two absent-target cases stay
distinguishable on purpose — a config with **no** target returns nothing, while a config naming
a table that is not there still raises, because that means something got out of step and
answering "no options" would hide it.

Saving a target-less lookup is still refused. Clearing the config makes the column *fixable*,
not valid, and the only sensible repairs are retargeting and deleting — both of which work.
One consequence: a table restore no longer silently re-points these columns. It never restored
anything usable, since the cells were scrubbed at delete time and cannot come back, so what is
given up is a column pointing at a live table and holding nothing.

**Columns that lost their target before this shipped are not repaired by the delete-path fix,
and need the sweep.** The fix changes what a delete does; it does not go back over a database
that already holds dangling columns, and by the deliberate asymmetry above those are the case
that *still raises* — so they stay exactly as stuck as the paragraph before last describes.
`src.maintenance.clear_dangling_lookup_targets` is the one-off that clears them, matching on
live lookup columns whose `target_table_uuid` names no live table and blanking the same fields
the scrub does. It is idempotent, and it must be run once against any database that predates
the fix — the deployed one included. Until it is, this section's past tense is only true of
tables deleted since. **Run `--dry-run` first and keep the output**: it names each column and
the target it points at, and clearing is what destroys the only record of that — including
for targets that are merely soft-deleted and still restorable from the tables bin.

**A `link` value must never reach an `href` unchecked.** The backend only stores URLs whose
scheme is in the column's `allowed_schemes`, itself capped at `SAFE_LINK_SCHEMES`
(`http`, `https`, `mailto`, `tel`) — a column cannot opt back into `javascript:`. But rows
predating a config change, or an odd import, mean the frontend re-checks: build every anchor
through `safeHref` in `lib/links.ts`, which falls back to rendering plain text. Any *new*
surface that renders link cells needs the same call; a raw `href={value}` is stored XSS.

**`POST /permissions/effective` is capped at 500 resources per call** (`max_length` on
`EffectiveActionsQuery.resources`). A page showing more rows than that needs to chunk.

**The onboarding tours and the guide describe the UI by name, and nothing tells them when
it moves.** Three surfaces name real controls in prose — `features/onboarding/tours.ts`
(the three guided tours and the per-page tips), `features/guide/GuidePage.tsx` (the written
version), and the welcome dialog. None of them are typed against the components they
describe, so a renamed button, a moved control or a reorganised page leaves them confidently
wrong, which is worse than silent: the people reading them are the ones with no way to tell
a stale instruction from their own mistake. It has happened once already — the guide spent a
release describing a header that had become a sidebar.

So **any change to the shell, a page's controls, or a flow's steps is not finished until
those are re-read.** Concretely:

- **A tour step points at `[data-tour="…"]`, not at a selector.** Deleting or moving the
  element that carries the attribute does not break the build. The step degrades to a plain
  centred card in the middle of the window — by design, so a missing element is never a
  broken tour, but it means the failure is invisible to CI and to you. Grep `data-tour`
  before removing a component. There are sixteen: the rail, its account band, the nav tree's
  workspaces section, the mobile burger, the dashboard hero, the first workspace and table
  card, New workspace, New table, Deleted, the table header's Settings, ⋯ and Add record, the
  table toolbar, the guide's contents nav, and the help button itself.
- **Step wording has to survive its element being absent**, since that is what a new account
  with no workspaces already sees. Write the body so it reads as an explanation, not as a
  caption for something under a spotlight.
- **`width: "wide" | "narrow"` on a step is the 1024px rail/burger split** (`DESKTOP_SCREEN`).
  Move something across that boundary and the step that names it needs the other variant, or
  it will point at nothing on one of the two layouts.
- **Renaming a visible label means editing prose in two files.** The tours and the guide both
  quote labels verbatim (`Add record`, `Save view`, `Nest under`). `COLUMN_TYPE_OPTIONS` is
  the one place this is already automatic — the guide's type table is generated from it — and
  it is the pattern to reach for when a list of labels starts being duplicated.
- **A genuinely new area of the app wants a tour of its own**, not a tenth step bolted onto
  `basics`. `pageTourFor` matches on the route; adding an entry there and a `Tour` object is
  the whole of it.

The guide's **screenshots are the one part of this that does tell you.**
`frontend/scripts/capture-guide.mjs` drives the real app with Playwright and photographs every
screen in its `SHOTS` table into `public/guide/`; each shot clicks controls by the labels the
guide's own prose quotes, so a renamed button fails the capture run with the id of the shot
that named it. That is an alarm the prose has never had — but only when someone runs it, so:

- **`npm run capture:guide` after any UI change**, alongside re-reading the two prose files.
  It needs the backend, the dev server, and `uv run python -m src.demo.guide` (the seeded
  Sales/Contacts tenant the guide describes, which is why the pictures match the words).
- **The images and `src/features/guide/figures.ts` are committed.** They are build inputs,
  not outputs: the guide has to render for someone who has never run the capture, and
  running it needs a database.
- **A failing shot is a finding, not a flake.** Fix the shot, then ask whether the paragraph
  beside it still describes the same control.

The guide's **internal references** are enforceable, and now enforced, by
`frontend/src/features/guide/guide.test.ts`: every contents entry resolves to a section on the
page and in the same order, every `<Figure>` names a screenshot that exists, every captured
screenshot is still shown somewhere, every in-page `#anchor` resolves, and the contents nav
keeps both the `id` the back-links target and the `data-tour` attribute the tour spotlights.
The figure check is the one that was most worth having: `Figure` renders *nothing* for an
unknown name, by design, so before this a mistyped figure was an invisible missing picture.
The test reads `GuidePage.tsx` as text (via Vite's `?raw`) rather than mounting it, because
vitest here has no jsdom — which also means `SECTIONS` lives in its own `sections.ts` so a node
test can import it.

That bounds the problem but does not solve it: what a test cannot check is whether a sentence
still describes the app. Nothing else here is enforceable, which is why it is written down.
Treat the guide and the tours as part of the UI's surface area rather than as documentation
about it.

**The batch-import template's headings are duplicated across the language boundary.**
`HEADERS` in `features/people/BatchUserDialog.tsx` tables the twelve CSV columns the template
writes; `KNOWN_HEADERS` in `src/auth/users/batch/parsing.py` is the set the server accepts.
Nothing checks them against each other. This is the same class of duplication the bullet
above describes, and it is not automatable the way `COLUMN_TYPE_OPTIONS` was — the generated
`api-schema.ts` publishes the *response* types, not the accepted header table, because the
headers are a CSV convention rather than a JSON schema.

It fails safely, which is the only reason it is acceptable: a header the frontend invents is
rejected by `check_headers` with a `400` naming it, so the template breaks loudly on the
first use rather than silently dropping a column. Adding a column to the import means
editing both, plus the example row beside `HEADERS` and the guide's People section.

**The `List` → `Table` rename left four things deliberately un-renamed.** The domain noun
became `Table` and `ListItem` became `TableRecord` on 2026-10-11 (migration
`f4a9c2e71b85`). Four spellings were kept on purpose, so nobody "finishes the job" and
breaks something:

- **`_UNIQUE_LOCK_NS = 0x4C495354` still spells `"LIST"`** (`src/tables/records/services.py`).
  It is a Postgres advisory-lock *namespace*: its only requirement is that every writer
  picks the same number. Changing it would have meant old and new code holding locks in
  two different namespaces for the length of a deploy — not serializing against each
  other at all, which is the one thing it exists to do.
- **18 NOT NULL constraints still carry `lists_*` / `list_items_*` names.** PG17+
  catalogues them; they are auto-generated, invisible to the app, and invisible to
  Alembic (`compare_metadata` diffs column nullability, not constraint names). Renaming
  them would make the revision fail outright on any server old enough not to have them.
- **`def list(...)` on every service is the collection-read verb, not the noun.** Ten of
  them, including `UserService.list` and `WorkspaceService.list`. So are `list_columns`,
  `list_grants`, `list_deleted`, and friends. Only `list_lists` → `list_tables` moved,
  because there the *object* was what changed.
- **`"item"` remains the slug fallback** in `core/utils.py` and `frontend/src/lib/slug.ts`,
  for any resource whose name slugifies to nothing. It never meant a record.

**`App.tsx` still routes `/lists/:uuid` → `/tables/:uuid`, and that one is permanent.**
It is the only deep link this app has ever had, so it is the URL people bookmarked and
pasted to each other; without it a saved link falls through to the catch-all redirect to
`/`, which reads like the table was deleted. One route, no API surface, no expiry.

A matching server-side shim (`src/tables/legacy.py`, 308-redirecting `/api/lists/*`)
carried the deploy itself, because `dbui-api` and `dbui-web` are separate Render services
and the bundle that calls the API does not move at the same moment the API does. It was
removed once the deploy had settled and the live numbers were checked. If a future rename
needs the same trick, that is the shape: `include_in_schema=False` so `gen:api` never
learns the dead routes exist, and 308 rather than 302 so a redirected write stays a write.

One behavioural note: the onboarding tour id `list` became `table`, and
`onboardingState.ts` maps the old value on read. Without that, everyone who had already
taken the table tour would have been offered it again.
