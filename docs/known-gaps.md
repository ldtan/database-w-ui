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

## 2. A record shared without its list can't be viewed in the UI

The API supports this on purpose — item routes use `resolve_list_or_404` (existence only)
rather than `get_list_or_404` (which requires list `view`). Verified: a user with `edit`
on one record and *no* grant on the list gets 200 on that record and sees only it in the
list's records.

The UI can't render it. `ListDetailPage` is the only place a record appears, and on mount
it fetches `/lists/{uuid}` and `/lists/{uuid}/columns/`, both gated on list `view`. Both
403 and the page blanks.

The blocker is the **column schema**: records are JSONB keyed by column uuid, so a record
is unrenderable without the column definitions — and that endpoint requires exactly the
permission an item-only grantee lacks. Closing this needs a backend change either way:
let an item grant confer read access to the list's column schema (field names and types,
not its records). That is a deliberate widening, not a no-op.

On top of that, pick one:
- **Degrade `ListDetailPage`** when `/lists/{uuid}` 403s (smaller, one page to maintain).
- **A dedicated record page/permalink** (cleaner separation, a second page to maintain).

Both also need two fallbacks that are easy to miss: `RecordFormDialog` renders from a Form
(`/lists/{uuid}/forms/` is *also* gated on list `view`), and `LookupCell` resolves a value
by fetching a **different** list — so lookups must degrade to an unresolved value when the
target list isn't visible.

## 3. Grants have no foreign key to their resource

`permission_grants` points at its resource via `(resource_type, resource_id)` with **no foreign
key** — `resource_id` means a different table depending on the type, so the database cannot
enforce it. Nothing cascades. A deleted list, view or account leaves its grants behind.

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

---

## 5. Calculated columns are computed on read, and that has edges

A `calculated` column stores no value. It carries a `formula` in its config — arithmetic over
the list's **number** columns and string functions over its **text** columns, referenced by
name (`min`/`max`/`round`/`abs`, `CONCAT`/`UPPER`/`LOWER`/`TRIM`/`LEN`/`LEFT`/`RIGHT`/`REPLACE`,
and `IF`) — and the value is produced in `_shape_for_read` (`items/services.py`) every time a
record is read, injected under the column's uuid and never persisted. The result is a number or
a string depending on the formula; the column has no fixed output type. The formula is parsed
and evaluated by `columns/formula.py`, which is a deliberately tiny AST walker with an
allow-list: **it never calls `eval`**, and the only names it resolves are the column values
handed to it, so a formula cannot reach a builtin, an attribute or a subscript. Keep it that
way — the moment it grows an `eval` or a real namespace, a list editor (an ordinary user, not
an admin) can run code. Note that `+ - * /` are numbers-only on purpose: text is joined with
`CONCAT`, so a string can never slip into `*` and become Python's silent repetition.

Reach is not the only thing a formula has to be bounded on. Because it runs once per record
on every read, what it may *spend* is bounded too: `MAX_SOURCE` (length), `MAX_DEPTH`
(nesting — length alone is not a bound on depth, since a unary minus costs one character a
level and a few hundred of them overflow the stack) and `_MAX_TEXT` (the size of any text it
produces — `REPLACE` grows a string by a factor set by a literal, and nesting composes those
factors while the source only grows linearly). The first two are refused at config time; the
third blanks the cell, since it depends on the row. Keep all three: without them a formula
that passes every other check is a way to spend the whole server, which is the same problem
as running code, arrived at from the other side. (Those three, `ROW_FUNCTIONS`, and the two
`*_row_expression` seams are public because list **metrics** build a second grammar on
this one — see §9 below. A metric walks this same tree once per record too, over more
records than a grid page, so the bounds matter more there, not less.)

The compute-on-read choice buys correctness for free — editing a dependency or the formula is
reflected on the next read, no backfill — at these costs, all currently accepted:

- **You cannot filter, sort or search on a calculated column.** Its value is not in the stored
  JSONB, so `apply_filters`/`apply_sorts`/`_apply_search` see nothing there. They don't error;
  they just never match. Making them work means evaluating in Python (on-read can't push the
  expression into SQL) — a real feature, not a patch.
- **Exports leave calculated columns out** (`transfer/services.py`), and imports skip a value
  for one. That keeps a file this app writes importing cleanly back into it, since the column
  is read-only on write. Exporting the *computed value* would be a nice enhancement, but then a
  re-import has to know to ignore it.
- **No calc-on-calc.** A formula may only reference `number` and `text` columns,
  so it cannot reference another calculated column (that would need cycle detection and
  recursive evaluation). Caught at config time: the referenced column is not one of those types.
  Choice and temporal columns are also excluded for now — they store ids/ISO strings whose text
  form would surprise. What a name refers to is decided by *where it sits*, not how it is
  spelled — `column_refs` reports every value-position name, including the four that collide
  with function names (`min`, `max`, `round`, `abs`, all valid slugs). Reporting those as
  functions is what used to let a formula over one skip these checks entirely.
- **References are by column *name*, and names are editable.** Rename a column a formula reads
  and the formula silently stops finding it — a missing operand reads as blank (see below), so
  the calculated cells quietly go empty rather than erroring. There is no reverse index from
  formula to column yet; renaming is not re-checked.
- **A missing operand makes the whole cell blank, not zero.** If any referenced cell is empty,
  the result is `None` and the cell renders `—`. This is on purpose: an incomplete row should
  read as incomplete, not as `0 * price`. Division by zero and non-finite results are blank the
  same way — one bad row must not fail a whole list read.

---

## 6. Trash is done everywhere except principals

Soft delete already destroys nothing (see "Soft delete destroys nothing" below): deleting a
record, user or group only sets `deleted_at`, and the purge jobs hard-delete once a target is
past its retention window.

**Views, forms, metrics and columns now have a bin too**, at `/lists/{uuid}/{kind}/deleted` with
`…/{kind}/{uuid}/restore` and `…/{kind}/{uuid}/permanent` — routes on each child's own
collection, one shared implementation in `src/lists/bin/services.py`, swept by
`purge_deleted_list_config`. Before that they were the worst of both: deleting one was
irreversible *and* kept the row forever, because nothing read `deleted_at` back and no job
reclaimed it. Three things about that surface are deliberate and worth knowing:

- **uuid only, never a name.** Every child route is uuid-only now, but the bin could never
  have been anything else: the unique index on `name` is partial (`WHERE deleted_at IS
  NULL`), so a binned `sales` view and a live one coexist legitimately and a name in the bin
  has no single answer. (These routes sat at `/lists/{uuid}/bin/{kind}` until children became
  uuid-only — the `/bin/` prefix existed only because `deleted` was itself a legal name.)
- **Gated on list-level `configure`**, the authority that creates them, because a deleted view
  resolves to no permission context of its own. That makes the bin coarser than the live routes
  for a view whose `permissions_inherit` is False — its recovery belongs to the list configurer.
- **A column's restore is partial, and says so.** Restoring clears `deleted_at` and nothing
  else, matching the other three. But deleting a column also nulls the list's
  `primary_column_id` if it pointed there, and un-nests every lookup whose pairing referenced
  it — neither comes back. `RestoredColumnRead` reports the first (`list_has_no_primary_column`,
  read off the list's *current* state, so a primary chosen meanwhile is not flagged). It cannot
  report the second: the pairing was cleared on the dependents, and an un-paired lookup is
  indistinguishable from one that never had a parent.

One thing about columns that is easy to get wrong, and worth stating plainly: **deleting a
column does not hide its data.** The values stay under the column's uuid in every record's
`data`, `_columns_by_key` is live-only but `shape_values` passes unrecognised keys through, and
`ListItemRead.data` is an unfiltered dict — so a deleted column's values keep coming back in
every record payload. The field disappears from the UI, which renders off the columns list, and
that is the whole of it. Anyone deleting a column to get rid of sensitive values needs
`…/permanent`, which is the only surface that removes them.

**Delete for good exists** for records, lists, workspaces and now these four: a `…/permanent`
route on each, valid only from the bin, so destroying something is always two deliberate steps
(`test_permanent_delete.py`, `test_list_config_bin.py`). Each clears its target's grants itself,
because nothing else can once the row is gone (gap 3).

**Records now have a bin.** Each list page has a "Deleted records" surface
(`DeletedRecordsDialog`) backed by `GET /lists/{uuid}/items/deleted` and
`POST /lists/{uuid}/items/{uuid}/restore` (`ListItemService.list_deleted`/`restore`). It lists
soft-deleted rows still inside the retention window and restores them. Two things settled the
shape and are worth keeping in mind before extending it:

- **Authority is the list, not the record.** A soft-deleted item resolves to *no* permission
  context (`_security_context` returns `None` for a deleted resource), so there is nothing to
  check per record. The bin is gated on list-level `delete`; the workspace owner always
  qualifies via the owner short-circuit. This is why the trash is **per-list and owner/grant
  driven**, not superuser-driven — `is_superuser` is orthogonal to data access.
- **Restore's one live edge is uniqueness.** A `unique` column may have gained a duplicate while
  the row sat in the bin, so `restore` runs `_enforce_unique` and can legitimately 409. The
  other two edges are free: inbound references were kept intact at delete time so they just
  resolve again, and a stale `multiple` shape is absorbed by `coerce_shape` on read (the same
  tolerance `reconcile_shapes` converges).

What is still missing:

- **Principals (users/groups) have no bin.** These are genuinely superuser identity
  administration (unlike records, which are the workspace owner's), so a principal trash is a
  *separate* surface with a *different* authority — `require_superuser`, not list ownership —
  and probably its own page. Not folded into the per-list records bin.
- **The views/forms/metrics/columns bin has no UI.** It is API-only; `DeletedRecordsDialog`,
  `DeletedListsDialog` and `DeletedWorkspacesDialog` have no counterpart for these four.
- **Lists cascade-scrub on delete, and restore does not undo that.** A list restore brings back
  its columns, records, views, forms and metrics (`_LIST_CHILD_MODELS`, matched on the shared
  `deleted_at`), but the *inbound* lookup references scrubbed at delete time do not come back
  (see "Deleting a list is the deliberate exception").

Only targets still *within* the retention window belong in the bin; once purged they are
unrecoverable and simply gone from it.

---

## 7. Nested lookups: stale pairs survive, and the picker filter is advisory

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
  filter column cascade-clears the pairing (`_clear_dependent_nesting`), but if a stale one
  is ever encountered anyway (a race, a hand-edited config), the picker, the record dialog
  and the write validation all treat the column as a plain lookup rather than erroring.

## 8. Rich text is HTML in a cell, and some things treat it as text

A `text` column in `rich` mode stores HTML. Sanitising is on the **write** path
(`ListItemService._validate_data` -> `columns/richtext.py`), so it covers direct API
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

## 9. Metrics are recomputed per request, and bounded

A metric (data-model §9) stores a formula, never a number: `GET
/lists/{uuid}/metrics/values` scans the records in scope and answers every card in one
pass. That buys the same thing compute-on-read buys a calculated column — a total is never
stale, and nothing has to be backfilled when a record, a formula or a filter changes — at
costs worth knowing about.

- **The same card shows different numbers to different people, on purpose.** The scan runs
  through `ListItemService.visible_stmt`, so it honours `item_view_filter`. A reader who
  cannot see a record must not learn its value from a total, which means "Total hours" is
  the total of *your* hours, not the list's. `row_count` in the response is the count after
  permissions, filters and search, so it is the number to show if you want to explain a
  figure — it will not match the list's record count and is not meant to.
- **It is a scan, and it is capped at `METRIC_ROW_LIMIT` (50,000).** Past that the answers
  describe the first 50,000 records and `truncated` is set; the card renders a note rather
  than a number that quietly means something else. Refusing outright was the alternative and
  leaves the reader nothing. The scan streams `data` alone (not ORM instances) in batches of
  1,000 and skips the view's sort, which an aggregate has no use for.
- **Nothing is cached, and the frontend re-asks on the live interval.** The values query
  rides `useLiveList`, so a large list plus a short `VITE_LIVE_REFRESH_SECONDS` is where
  this will first hurt. What keeps that in hand today is that **the strip is hidden until
  the toolbar's Metrics button reveals it**, and `visible` gates the query rather than
  just the markup — a list nobody is totalling costs nothing, and the interval only runs
  for someone actually looking at a total. Keep those two facts attached: making the strip
  default to shown, or rendering it hidden with the query still enabled, quietly turns
  every open list into a repeating full scan. The fix when it does hurt is a SQL fast path
  for the common shape — a
  bare `AGG(stored_column)` compiles to one `SELECT SUM(CAST(data->>'uuid' AS numeric))` —
  which is why the row limit exists in the meantime. It cannot be the *general* path:
  formulas take arbitrary row expressions, and a `calculated` column has no stored value to
  aggregate at all (the same wall as §5).
- **A renamed column breaks its metric — but says so.** Formulas reference columns by name
  and names are editable (§5), and there is still no reverse index. The difference is that a
  metric checks its refs against the live columns before scanning and returns an `error`
  for that card, because a broken `SUM` rendering `0` is the one wrong answer nobody would
  catch. The break costs its own card, never the request.
- **The values query key is nested under the items key** (`["items", listUuid,
  "metrics", …]`), so every `invalidateQueries(["items", listUuid])` already in the app
  refreshes the cards. Keep it that way: a sibling key would need a second invalidation
  beside each of the existing ones, which is correct the day it is written and wrong six
  call sites later.

Two things a metric deliberately does **not** do: it has no `permissions_inherit` (unlike
views and forms — it carries no permissions of its own, so list `view` reads and list
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
- **`TimezoneSync` has no test**, because the frontend has no test runner at all. Its loop
  terminates by inspection (PATCH → cache update → `local === stored` → early return, with
  the `pushed` ref covering the in-flight window), which is not the same as a green test.
- **Display of instants ignores the setting.** The three `toLocaleString()` call sites (the
  deleted-workspaces, deleted-lists and deleted-records dialogs) use the browser's zone,
  which is right in almost every case and inconsistent with the account preference in the
  rest. Centralising them behind one `formatInstant()` would make that a one-line choice.
- **`zoneinfo` reads the OS tz database**, which Windows does not have — hence the
  unconditional `tzdata` dependency. If it ever goes missing, `available_timezones()`
  returns empty, every zone looks unknown, writes 422, and `today` quietly reverts to UTC.

## Behavior that surprises people

**`is_superuser` and the permission model are orthogonal.** A superuser administers
*identities* — creating users, promoting them, editing group membership. It does **not**
grant access to anyone else's workspaces, lists, or records; that is entirely the grant
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

**"Unique" permissions are opt-out, not opt-in.** A record leaves the list's permission
umbrella only when someone flips its `permissions_inherit` to `false`. New records default
to inheriting, so a row added later to a list with carefully-locked-down records is
immediately visible to everyone holding list `view`. There is no "every new row in this
list starts unique" mode. If per-record access is meant to be a list's default posture,
that needs building.

**Breaking inheritance is reversible.** Flipping `permissions_inherit` back to `true`
re-joins the parent chain, and grants made while the child was independent are kept and
simply re-union with the inherited ones.

**`ListRead.workspace` exposes only a uuid, deliberately.** A list can be granted without
its workspace, so serializing the workspace's `display_name` would leak the name of a
workspace the caller was specifically not given access to. This is why the "Shared with me"
section shows the list's own slug as its subtitle rather than "in «Workspace»".

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
`lists.primary_column_id → columns.id` is circular (`columns.list_id → lists.id`), so the
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
filters (`workspace_view_filter`, `list_query_filter`, `item_view_filter`). So an
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
`from src.permissions.services import ...` in `workspaces/services.py` or `lists/services.py`
creates an import cycle. The deferred function-local imports in `list_visible`, `ListService.list`,
`ListItemService.list`/`query`, `ViewService.list`, and `FormService.list` are load-bearing.

**Moving a list between workspaces must assign the `workspace` relationship, not the
`workspace_id` FK.** `ListService.get_any_by_uuid` eager-loads `List.workspace`
(`selectinload`), and SQLAlchemy's unit of work treats a *loaded* many-to-one as
authoritative: on flush it writes the FK from the relationship, **silently overwriting a
bare `lst.workspace_id = …` assignment** and leaving the list where it was. Both
`ListService.update` (the cross-workspace move) and `transfer_to_user` therefore do
`lst.workspace = target`. This was latent for the whole life of the move feature — it had no
UI and no test, so nothing ever exercised it — and `tests/test_list_transfer.py` now pins it.
Any new code that re-parents a list has the same trap.

**Ownership transfer is the "email" model: a list is pushed, not offered.** Handing a list to
another user (`POST /lists/{uuid}/transfer`) moves it into that user's **immutable `incoming`
workspace** (`is_system`, minted lazily by `WorkspaceService.get_or_create_inbox`, name
reserved so an ordinary workspace can't occupy the slot). There is deliberately **no
accept/consent step** — anyone who *owns* a list can drop it on anyone, exactly as email lets
anyone write to you; the recipient's recourse is to move it out or delete it. The authorization
is owner-only *on the source* (ownership flows through the workspace, so the caller must own the
list's current workspace) — **not** `configure` on the target, which is why transfer is a
separate endpoint from the generic move (nobody holds `configure` on a stranger's inbox). The
inbox is guarded like a system group: `WorkspaceService._guard_system_workspace` refuses to
rename or delete it (its *lists* stay ordinary, so the recipient can still move or delete them).
Existing grants survive the move (they key on the list's `resource_id`, unchanged), so the
giver's previously-shared users keep access; only the giver's *implicit owner* access ends. A
name clash on landing auto-suffixes rather than 409ing.

**Cloning a list remaps column uuids; get it wrong and records render blank.** `POST
/lists/{uuid}/clone` (`ListService.clone`) copies a list into the same workspace — structure
always, records only when `with_records` is set. The clone's columns are new rows with new
uuids, so record `data` is keyed against columns that no longer exist unless every reference
is rewritten through an old→new column-uuid map: the `data` keys, `primary_column_id`, and
the column-uuid refs inside view configs (`filters`/`sorts`/`group_by`/`hidden_columns`/
`column_order`) and form configs (`fields[].column_uuid`). Cell *values* are copied verbatim
— choice labels and lookup/user/group target uuids stay valid — and a **self-referencing
lookup is deliberately left pointing at the original list** (config and cells), so the clone
"reaches into" its source rather than needing a row-uuid remap; pointing it at the clone
instead is the deferred alternative. Two easy-to-miss rules: cloning must **not** call
`_provision_defaults` (it copies the source's real columns/views/forms, so seeding defaults
would duplicate them), and it must **not** copy grants (the clone is a new resource the cloner
owns, private like any new list). `tests/test_list_clone.py` pins the remap.

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
frontend's own claim about those blobs, and are the one thing left that can quietly go stale.
Change a config's shape in the backend and nothing will tell you; change it here too.

The endpoints are still untyped, too: `api.get<T>(path)` takes the caller's word for both.
A typed client (`openapi-fetch`) would close that, at the cost of touching every call site.

**Permission-gated controls are hidden while their query is in flight.** `useEffectiveActions`
returns an empty set until the request resolves, so buttons appear a beat after page load
rather than showing and then vanishing (which would let someone click a control they don't
have). Grant and inheritance changes invalidate the cached `["effective"]` queries.

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

**Every read path over records must apply `item_view_filter`.** Per-record permissions are
only as strong as the weakest path that returns records. Export used to miss this — it
streamed rows filtered on `list_id` alone, so a list-viewer could export records whose
inheritance was broken and were never granted to them, even though the grid hid them
correctly. Fixed, and `test_http_export_respects_item_permissions` guards it. Any *new*
endpoint that returns record data needs the same filter; a list-level `view` check is not
sufficient.

**"Live records" is polling, and its refresh must stay a permissioned re-read — never a
data push.** An open list refetches its records every `VITE_LIVE_REFRESH_SECONDS` (default
5, `0` disables) through the `useLiveList` hook (`frontend/src/lib/useLiveList.ts`), paused
while the tab is hidden **and** after 5 minutes with no user activity on a still-visible tab
(so a list left open on screen stops polling until touched). It works precisely *because*
each refresh is an ordinary
`GET /items/`, so it inherits the `item_view_filter` above and can never surface a row the
viewer can't see — the client is told nothing except "look again." Two things follow that
are easy to get wrong when this grows into server push (planned-features #8): a live event
must carry **only** "list changed," never record data (a fan-out of rows would bypass the
per-record filter every subscriber is entitled to differently); and the future SSE stream
must hold **no pooled DB connection** while open (see the two-connection rule below), or a
long-lived subscriber pins a session for its whole life. One operational note: on Render's
free tier a focused, open tab polling keeps the single instance awake — fine for a demo,
but it spends free hours.

**A `user` cell must never resolve to a person's real name.** `/auth/users` is readable by
any authenticated user (the Share dialog needs to list principals), but person records are
private — owner or superuser only. So `user` columns label by **username**, in both
`PrincipalControls.tsx` and the export formatter. Swapping in "first + last name" because it
reads nicer would hand every viewer of a list the personal details of everyone referenced
from it.

**Every reference column exports its label, and the two sides must agree on what the label
is.** `lookup`, `user` and `group` all store a uuid and export the human-readable label
(row label / username / group name); import resolves it back. The load-bearing detail is
that export and import both call `display_column_key` to decide *which* column of the target
list supplies a row's label — if they ever picked differently, a file this app exported would
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
lookup one goes through `GET /lists/{list}/columns/{column}/options`, which is server-side
precisely because only the server knows a row's label (`display_column_key`) — and it applies
`lookup` on the target list plus the per-record filter, so a picker can never offer a row its
user cannot read. Still to convert: `GroupDialog` (member picker) and `PeoplePage`
(person-name annotations).

**A lookup can target any list its creator can `lookup`, and cross-workspace targets make
"degrade to uuid stubs" a routine state, not a rare one.** The config-time check was
relaxed from "a list you own" to "a list you can `view`" (`ColumnService._validate_config`),
and then to "a list you can `lookup`" — which `view` confers, so that second step widened
the set without changing any existing answer. A lookup may therefore point at another of
your workspaces, at a list shared to you, or at one shared *only* as a lookup target.
Nothing about resolution changed — it re-checks the *reader's* access to the target
per-caller (the note below), which is what keeps a cross-owner target from leaking. What
*does* change: a target you don't own can have your access revoked or be deleted entirely
at any time, independent of the referencing list. When that happens the cells fall back to
uuid stubs (`LookupCell`, `retry: false`) exactly as they always could — it just stops
being exceptional. A target the creator cannot read is refused indistinguishably from one
that doesn't exist, so the `target_list_uuid` field can't be used to probe for a stranger's
lists. `tests/test_lookup_options.py` pins all three: own-other-workspace, shared-in, and
refused-when-unviewable.

**Resolving a label means reading the target list, so it obeys the *target's* permissions,
not the exported/edited list's.** `view` on a list must not hand over the contents of a
different list it happens to point at. The options endpoint enforces this, and so does export
(`_lookup_row_labels`) — a caller who cannot read the target gets uuids instead of labels,
which is what they saw before labels existed. Any new surface that resolves a lookup label
needs the same check; `test_export_does_not_leak_labels_from_an_unreadable_target` pins it.

**The `lookup` action is that check's other half: a list can be shared *as a lookup target
only*.** Granting `lookup` lets other people's lookup columns resolve the list's labels
while the list stays out of `GET /lists/`, out of "Shared with me", and refused by
`GET /lists/{it}` — a reference list ("Schools", "Departments") without handing it over.
Three things this rests on, all easy to break:

- **The collection filters must test actions, not grant existence.** `list_query_filter`
  and `workspace_view_filter` now require `CONFERRED_BY[view]` via jsonb `?|`. Revert
  either to matching any grant row and the lookup-only list reappears in "Shared with me",
  silently undoing the grant. (This also settled a pre-existing oddity: a grant with an
  empty action list used to list the resource while permitting nothing on it.)
- **`item_view_filter` takes the action being exercised.** It short-circuits on the
  list-level check, so at the default (`view`) a lookup-only grantee falls through to
  "only items granted individually" — an empty picker. The three label paths (options,
  export, search by linked label) pass `lookup`. Per-record grants still apply either way:
  `lookup` relaxes the list-level gate, not row-level ones.
- **The target picker needs `GET /lists/?lookup_targets=true`.** A list you cannot name is
  a list you cannot point a column at. That flag is the single deliberate exception to
  "lookup-only lists are invisible", it is off by default, and `ColumnDialog`'s shared-lists
  query is its only caller. The schema stays hidden even then — `GET /lists/{it}/columns/`
  is still refused, so the dialog hides the label-column dropdown (`targetSchemaHidden`) and
  the server's fallback picks the label. `tests/test_lookup_grants.py` pins all of it.

**A row's label falls back: the lookup's `display_column_uuid`, else the target's primary
column, else its *first* column.** That last step is load-bearing — `primary_column_id` is
only ever set by an explicit PATCH and is nulled when that column is deleted, so most lists
have none. Drop it and every lookup at such a list silently degrades to uuid stubs *and* its
picker's search matches nothing.

Three traps worth knowing if you touch these hooks.

**A cell must subscribe to its own label query — never take a resolved map as a prop.**
PrimeReact's `DataTable` re-renders its body only when `value` (the rows) changes. Labels
arrive *after* that, so a map computed in the page and passed down freezes at whatever was
resolved when the rows last changed: assign a user to a record and the cell sits on `…`
forever, even though the page-level hook has the name. `PrincipalCell`/`LookupCell` therefore
take the page's *uuid list* and call the hook themselves. Every cell of a column passes the
same list, so react-query serves them all from one request — the batching survives.

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
of the target list, so it takes `view` on that list plus the per-record filter — otherwise
search becomes a way to probe the contents of a list you were never given. A caller without
that access simply gets no match on those columns, and
`test_search_by_lookup_label_obeys_the_target_lists_permissions` pins it. A label resolving to
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

- Values go through the shared `renderCell`; ordering through the shared `sortRows`. Both
  were duplicated once and the copies drifted — the grid sorted with PrimeReact's comparator
  (blanks float to the top on a descending sort, `"item10"` before `"item9"`) and the cards
  with ours, so the same list came out in a different order depending on the width of the
  screen. The grid now sorts through each column's `sortFunction`, which is the only way to
  make `DataTable` use someone else's comparator without going `lazy` (which would take its
  paging away too).
- Reference cells sort by the **uuid** they hold, not the label they show — both layouts
  alike. Sorting those meaningfully means sorting server-side by the resolved label.
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

What makes that safe is that the mismatch is absorbed in exactly **three** places, and nowhere
else has to know:

- **Reads** — `coerce_shape` is applied as records leave `ListItemService` (and in the export,
  which streams raw JSONB and bypasses that path). A single column returns the array's first
  element; a multiple column returns a bare value as a one-element list. Put this in the UI
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
  binning one row makes every record naming it unsaveable — a fine way to lock a list.
- **The clear happens once, in `_references.clear_expired`**, driven by the same retention
  window. Shape-agnostic (`?|` plus a `CASE`), set-based (one UPDATE per column, not per row).

**Deleting a *list* is the deliberate exception** and still scrubs. Every value in a lookup
pointing at it goes stale at once; the permission resolver returns *no access* for a deleted
resource, so those labels could not even be authorised; and restoring a list would mean
un-cascading its columns, records, views and forms — a feature that does not exist.

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
  workspaces section, the mobile burger, the dashboard hero, the first workspace and list
  card, New workspace, New list, Deleted, the list header's Settings, ⋯ and Add record, the
  list toolbar, the guide's contents nav, and the help button itself.
- **Step wording has to survive its element being absent**, since that is what a new account
  with no workspaces already sees. Write the body so it reads as an explanation, not as a
  caption for something under a spotlight.
- **`width: "wide" | "narrow"` on a step is the 1024px rail/burger split** (`DESKTOP_SCREEN`).
  Move something across that boundary and the step that names it needs the other variant, or
  it will point at nothing on one of the two layouts.
- **Renaming a visible label means editing prose in two files.** The tours and the guide both
  quote labels verbatim (`Add record`, `Save view`, `Nest under`). `COLUMN_TYPE_OPTIONS` is
  the one place this is already automatic — the guide's type list is generated from it — and
  it is the pattern to reach for when a list of labels starts being duplicated.
- **A genuinely new area of the app wants a tour of its own**, not a tenth step bolted onto
  `basics`. `pageTourFor` matches on the route; adding an entry there and a `Tour` object is
  the whole of it.

Nothing here is enforceable by a test, which is why it is written down. Treat the guide and
the tours as part of the UI's surface area rather than as documentation about it.
