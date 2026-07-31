# Planned features

Nine capabilities that have been asked for. Each section says what it is, what already
exists to build on, the load-bearing implementation details, and the decisions still
open. Most are now **implemented** — the heading says which; only §1 (column type
conversion) and §2 (column data migration) remain unbuilt.

These share two roots in the existing model, so read them once here rather than nine times below:

- **Records are JSONB keyed by column `uuid`** (data-model D2). A rename is free because the
  key never changes; a **type change, a data copy, or a clone is not free**, because it touches
  the values or the keys themselves. This is the difference that makes four of these five
  features harder than they look.
- **There is no per-list owner.** Ownership flows through `workspace.owner_id`
  (data-model §3), so anything about "who owns a list" is really about which workspace it
  sits in.

Cross-references: [data model](data-model-design.md) · [known gaps](known-gaps.md).

---

## 1. Column type conversion

Change a column's `type` after it has records — `text` → `number`, `choice` → `text`,
`date` → `datetime`, and so on.

**Why it's not a config edit.** `type` is **immutable after creation** today (data-model §4,
§5), and deliberately so. Every record stores a value under the column's uuid whose *shape and
meaning* are the current type's: a `choice` cell holds an option label, a `lookup` holds a
target-row **uuid**, a `number` holds a JSON number, a `date` holds an ISO string. Changing the
type reinterprets every one of those stored values, and most cross-type pairs need the bytes
themselves rewritten, not just the declared type.

**The precedent to copy — and where it stops.** Toggling a column's `multiple` is O(1) and
*never* rewrites records: the mismatch is absorbed on read by `coerce_shape` and converged later
by the `reconcile_shapes` cron (known-gaps: "Toggling a column's `multiple`"). That "tolerate on
read, converge in the background" pattern looks like the right instinct, and the ~25s-per-1M
measurement gets quoted in support of it — but read that number carefully: it is `jsonb_set`
executing *inside Postgres*, one statement, no round trip. It is the cost of the **fastest**
option, not of a rewrite in general.

A type conversion still **cannot** be tolerated on read the way a shape mismatch can. `"42"` and
`42` are the same value in two shapes, whereas `"May 4, 2026"` and the date `2026-05-04` are not
— one has to be *computed* from the other. And the tolerance does not actually reach far enough
even for shape: filters and sorts read raw JSONB and absorb nothing, which for a type conversion
means hard query errors rather than merely wrong answers (see the decision below, and known-gaps).
So conversion needs an explicit, chunked, killable backfill (model it on `reconcile_shapes.py`),
never a read-time coercion.

**The conversion matrix is where the real work is.** A few are lossless and cheap; most are
not:

- **Widening temporal** — `date` → `datetime` appends a time; the reverse truncates it. Safe-ish.
- **`number` ↔ `text`** — `str(n)` always works; `text` → `number` is `_parse_cell`'s job and
  will reject non-numeric cells (per-cell failure, see the policy question below).
- **`choice` ↔ `text`** — leaving `choice` is free: the cell already holds the label, only
  the declared type changes. Entering `choice`, either mint an option per distinct text value
  or match against a provided option set and fail the stragglers (per-cell failure, see the
  policy question below).
- **Reference types (`lookup`/`user`/`group`)** — the cell holds a uuid. Converting *to* `text`
  means resolving the label (exactly what export's `_LabelResolver` does, permissions and all);
  converting *from* `text` means resolving a label back to a uuid (what import does). Reuse those
  two code paths — do not write a third resolver.
- **`calculated`** — stores no value at all (known-gaps gap 5). Converting *away from* calculated
  means computing each cell and persisting it; converting *to* calculated means dropping stored
  values and adding a `formula`. Treat these as their own cases, not part of the general matrix.

**`config` has to be rebuilt, not merged.** Config PATCHes merge (data-model §5), but a type
change invalidates the old type's keys wholesale — a `choice`'s `options`, a `lookup`'s
`target_list_uuid` — and requires the new type's. Validate the *new* config from scratch.

**Decided — the conversion runs into a *new* column, never in place.** Add a column of the target
type, convert into it, let the user read the report, then swap it in. The reason is not
tidiness: an in-place conversion has a window where the declared type and the stored bytes
disagree, and `filtering.py` reaches past every Python-side guard straight into the JSONB
(known-gaps: "Toggling a column's `multiple`"). Measured on the demo database, a `number` column
holding one text cell makes `apply_sorts` raise
`InvalidTextRepresentationError: invalid input syntax for type numeric`, so a half-converted
column does not degrade — every sort and range filter on it **500s** until the last row lands.
Writing into a separate column removes the window instead of trying to survive it: the old
column stays correct for every view, filter, form and formula until the moment of the swap; the
new one starts empty and only ever receives values that already passed the target type's
validation, so its invariant holds from the first row rather than eventually; and an unhappy
result is abandoned by deleting a column nobody was using. This is what makes a conversion
reversible until the user commits, and it is why #1 is delivered *through* #2 — they share one
engine.

**Decided — convert in Python, not SQL.** Postgres can rewrite the structural pairs far faster,
but it cannot resolve a reference label (that needs `PermissionService.item_view_filter`, a
Python-composed expression over the permission tables), cannot validate against the target
column's config, cannot name the cells that failed, and cannot mint `choice` options. Two
engines would also mean the same logical conversion behaving differently depending on which path
it took. Since the loop is in Python anyway, `_validate_value` comes free and enforces both the
type-level invariants the SQL layer depends on *and* the config-level ones (`max_length`,
`required`, `unique`) that it does not.

**Decided — inline, with a row-count ceiling; no job queue.** Free Postgres is 1GB, so the
million-row list the ~25s figure describes is not reachable on this deployment;
`METRIC_ROW_LIMIT` is already 50k and a Python backfill at that size is seconds. Convert in the
request, and above the ceiling refuse with "too large to convert interactively" and route the
caller to the maintenance workflow, which already runs against the same database on someone
else's compute. Keep the backfill chunked and committed per batch anyway (copy
`reconcile_shapes`) so the same function serves both callers unmodified. A queue would be
infrastructure the 512MB instance pays for and no reachable list needs.

**Note the storage cost:** mid-conversion every record holds both the old and the new value, so
the list's JSONB roughly doubles. Worth a size check before starting on a 1GB database.

**Still open:**
- **Which pairs ship in v1** — conservative (text hub + `date` ↔ `datetime`) or the full matrix.
- **What happens to cells that fail** — blank them, or block the swap until the user fixes them.
  The new-column model makes "show the report and let them choose" cheap, which it would not have
  been in place.
- **Swap mechanics** — the clean version gives the new column the old one's uuid and name at
  cutover so views, forms and formulas never need repointing, at the cost of one chunked SQL
  rekey of the record data (pure byte-moving, no conversion in it). The alternative is the
  old→new uuid remap already built for clone.
- **`Column` has no `hidden` flag**, so a scratch column would show in the grid mid-conversion.
  Either add one (small migration, honest semantics) or create it soft-deleted — `deleted_at`
  already hides a column from every reader while preserving its record values, but a stalled
  conversion could have its scratch column taken by the purge job.

---

## 2. Column data migration (copy one column's data into another)

Bulk-populate a target column from a source column across every record in a list — the
"add a column, move the data over, delete the old one" workflow, and the safe delivery
vehicle for feature 1.

**What it is mechanically.** For each record, read the value under the source column's uuid and
write it under the target's. If source and target are the **same type**, that's a straight copy
of the JSONB value. If they differ, each cell goes through feature 1's conversion — which is why
these two features share an engine and should ship together.

**The same delivery decisions as feature 1**, which are settled above: Python engine, chunked and
committed per batch like `reconcile_shapes`, run inline below a row-count ceiling and from the
maintenance workflow above it. Include soft-deleted records, per the same reasoning
`reconcile_shapes` uses (it does, so a restore doesn't resurrect a stale shape).

**Two edges that will bite:**
- **`unique` on the target.** Copying a source with repeats into a `unique` target creates
  duplicates. `_enforce_unique` will 409 — decide whether that aborts the whole migration or
  skips-and-reports, same question as feature 1's unconvertible cells.
- **`multiple` shape mismatch.** A single→multiple copy wraps each value in a one-element list; the
  reverse takes the first element. `coerce_shape` already expresses exactly this — call it, don't
  re-derive it.

**Needs a decision — but only for the standalone use.** When #1 drives it the answers are pinned:
a conversion always overwrites (the target column is new and empty) and always copies (the source
survives until the swap, which is what makes it reversible). What is still open is what a user
invoking #2 *directly* gets:
- **Overwrite policy.** Overwrite the target unconditionally, or only fill cells where the target
  is empty? Fill-empty-only is the safer default for merging two columns; overwrite is what a
  pre-drop migration wants.
- **Leave the source intact?** Copy (source kept) vs move (source cleared). Copy is safer and lets
  the user verify before dropping the source themselves.

---

## 3. List ownership transfer (hand a list to another user) — **implemented**

**Shipped** via the "Incoming workspace" model. `POST /lists/{uuid}/transfer` `{ user_uuid }`
moves a list into the recipient's immutable, lazily-created `incoming` workspace, making them
its owner. Owner-only; the giver's implicit access ends with the move; existing shares survive
(grants point at the list by `resource_id`, unchanged). A name clash on landing auto-suffixes
(`tasks` → `tasks_2`). The recipient files it by moving it out (feature 4) or rejects it by
deleting it. See `ListService.transfer_to_user`, `WorkspaceService.get_or_create_inbox`, and
`tests/test_list_transfer.py`; UI is the "Transfer to user" mode of `MoveOrTransferDialog`.

The decisions taken (from the three the design flagged): **email model** (anyone who owns a list
can send it; no accept flow), **owner-only** to give, **shares preserved** / giver loses access.
The consent tradeoff is documented in [known gaps](known-gaps.md). The design rationale below is
kept for context.

---

Give a single list to a different user, so it becomes theirs — they own it, it counts against
their storage, it shows up as their list, and the original owner keeps or loses access per the
decision below.

**Under the current model, "ownership" is workspace membership.** There is no list owner to
reassign; a list belongs to whoever owns its workspace. So handing list L to user B means
**moving L into a workspace B owns** — which is mechanically feature 4 (already built) pointed at
someone else's workspace. The whole feature is therefore about the *consent and access* layer on
top of a move, not the move itself.

**The consent problem is the crux.** `update_list` requires `configure` on the **target**
workspace (`routers.py:139-147`). So today you can only move a list into a workspace you can
already configure — you cannot *push* a list onto a stranger. That guard is correct (nobody
should be able to dump lists into your workspace), which means an ownership transfer needs one of:

- **A pull/accept flow** — the giver offers L; the recipient accepts it into a workspace of their
  choice. The accept is the recipient acting on *their* workspace, so the existing `configure`
  check already authorizes it. This is the clean design.
- **A dedicated transfer endpoint** that takes recipient consent as its authorization instead of
  the giver holding `configure` on the target. More surface, but no two-step handshake.

**What moves for free, and what doesn't:**
- **Children ride along.** Columns, records, views, and forms are keyed by `list_id`, so they
  follow the list with no work — same as feature 4.
- **Grants persist, and that's the subtle part.** A grant points at `(resource_type='list',
  resource_id)` with `resource_id` unchanged by a move — so **every existing share on L survives
  the transfer**, including any the *old* owner handed out. The old owner's own access, though, is
  not a grant: it was the implicit workspace-owner short-circuit, and it evaporates the instant L
  leaves their workspace (known-gaps: "The workspace owner is an implicit superuser"). New owner
  short-circuits to everything; old owner drops to whatever explicit grant they hold, if any.
- **Inheritance re-parents.** L's `permissions_inherit` now resolves against the **new**
  workspace's grants. A list that inherited broad workspace access in the old home may inherit
  nothing in the new one (or vice-versa) — flag this to the giver.
- **Name collision.** List `name` is unique per workspace; transferring into B's workspace can
  409 if B already has that name (same as a cross-workspace move today).

**Needs a decision:**
- **Consent model** — pull/accept vs dedicated transfer endpoint (above).
- **What happens to existing shares** on transfer. Keep them (grants persist by default), or scrub
  them so the new owner starts from a clean sharing slate? Scrubbing is the "it's mine now"
  expectation; keeping is the literal current behavior. If scrubbing, it's the same grant-cleanup
  the purge path does (known-gaps gap 3).

---

## 4. Move lists across workspaces — **implemented**

**Backend.** `PATCH /lists/{uuid}` with `workspace_uuid` moves a list to another workspace. The
router requires `configure` on **both** the source list and the target workspace
(`routers.py`), so you can only move between workspaces you're entitled to configure. Children
(columns, records, views, forms) follow by `list_id`; list-level grants survive because
`resource_id` is unchanged. A name already taken in the target workspace 409s.

> **Load-bearing fix.** This path was latently broken: `get_any_by_uuid` eager-loads
> `List.workspace`, and a loaded many-to-one **overwrites a bare `workspace_id` change on
> flush** — so setting the scalar FK silently left the list where it was. `ListService.update`
> (and the new transfer) now assign the **relationship** (`lst.workspace = target`), not the FK.
> Any future move must do the same. `tests/test_list_transfer.py` covers it end-to-end.

**Frontend.** The "Move to workspace" mode of `MoveOrTransferDialog`, reachable from each list's
controls on `ListsPage`, lists the workspaces the caller can `configure` (own + granted), minus
the current one and every immutable inbox, and PATCHes `workspace_uuid`.

**Same-owner is the common case** (reorganizing your own workspaces). **Cross-owner** is feature
3's job — pushing a list onto a stranger goes through `/transfer` (their inbox), because nobody
holds `configure` on someone else's workspace.

---

## 5. Clone a list (with or without its records) — **implemented**

**Shipped.** `POST /lists/{uuid}/clone` `{ with_records }` duplicates a list into its
own workspace. Structure (columns, views, forms) is always copied; records only when
`with_records` is true. The clone's columns are new rows with new uuids, and every
reference is rewritten through an old→new column-uuid map: the record `data` keys,
`primary_column_id`, and the column-uuid references inside view configs (`filters`,
`sorts`, `group_by`, `hidden_columns`, `column_order`) and form configs
(`fields[].column_uuid`). `choice` cell values and `calculated` formulas (which name
columns by *name*) copy verbatim. Grants are **not** copied and `_provision_defaults`
is **not** called — the clone copies the source's real columns/views/forms and starts
private, like any newly created list. The name auto-suffixes to a free slug
(`tasks` → `tasks_copy`, then `tasks_copy_2`). Reading the source needs `view`;
writing the copy needs `configure` on the workspace (the same gate as create). See
`ListService.clone` and `tests/test_list_clone.py`; UI is the "Duplicate" action on
`ListsPage` (a checkbox opts records in).

The decisions taken (from the three the design flagged): **structure-only by default**
(records opt-in), **same workspace** with an auto-suggested name (move it elsewhere
afterward with feature 4), and a self-referencing lookup **left pointing at the
original list** — so its config `target_list_uuid` and its cells copy verbatim, no
row-uuid remap. The design rationale below is kept for context.

---

Duplicate a list's structure — and optionally its data — into a new list.

**The central integrity rule: uuids must be remapped.** Records are keyed by column **uuid**
(data-model D2). A clone's columns are new rows with **new** uuids, so a straight copy of a
record's `data` dict would key every value under a column that doesn't exist in the clone. The
clone therefore has to build an **old-uuid → new-uuid map** for its columns and rewrite every
copied record's keys through it. This is the one thing that, done wrong, silently produces a
list whose records render blank. Everything else follows from it:

- **`primary_column_id`** is an FK to `columns.id` — repoint it at the *cloned* column via the map.
- **View and form config reference `column_uuid`** (data-model §7, §8): a view's `filters`,
  `sorts`, `group_by`, `hidden_columns`, `column_order`, and a form's `fields[].column_uuid` all
  have to be remapped through the same table, or the cloned views/forms point at the source's
  columns.
- **`choice` options** live in `config` and can be copied verbatim — records store the label,
  and the clone's records hold the same labels against the same (copied) options. No remap
  needed there.

**Lookups and self-references.** A `lookup` column stores `target_list_uuid` in config and
target-row uuids in cells.
- A lookup pointing at a **different** list: copy it unchanged — it still validly points at that
  list, records and all.
- A lookup pointing at **the list being cloned** (a self-reference): decide whether the clone's
  copy points back at the *original* (simple, but the clone "reaches into" its source) or at the
  *clone itself* (needs the row uuids remapped too, and only meaningful when records are cloned).

**Records on/off changes the hard cases:**
- **Without records** — copy structure only; self-lookup cells are moot (there are no cells).
- **With records** — copy `data` through the uuid map. `unique` columns are safe *because the
  clone is a fresh, empty list* (uniqueness is per-list), so a source that satisfied its unique
  constraints produces a clone that does too — no self-collision. Self-lookup cells need the
  row-uuid remap if you chose "point at the clone."

**Don't double-provision, don't copy grants.** `_provision_defaults` seeds a brand-new list with
default audit columns, a grid view, and forms — a **clone must not call it**, or it'll graft
default views/forms on top of the source's real ones. The clone copies the source's actual
columns/views/forms instead. And grants should **not** be copied: the clone is a new resource the
cloner owns (via its workspace), so it starts private, like any freshly created list.

**Needs a decision:**
- **Records default** — clone with or without records by default? (The request implies both are
  offered; pick the default.)
- **Destination** — same workspace (needs a new `name`, since list name is unique per workspace)
  or a chosen workspace. A "Duplicate" action in the same workspace is the common case and should
  auto-suggest a free name (`tasks` → `tasks_copy`).
- **Self-lookup target** — original vs clone, per above.

---

## 6. Nested lookup columns — **implemented**

**Shipped.** A lookup column B can nest under a sibling lookup A when B's target list
has a lookup column pointing back at A's target list (Cities have a Country; picking a
Country narrows the Cities — and chains compose, so Continent → Country → City works
pairwise with no extra machinery). The pairing lives in B's config as
`parent_column_uuid` (the sibling A) + `target_filter_column_uuid` (the lookup on B's
target list), validated in `ColumnService._validate_nesting`: both keys set together or
not at all, both live lookups in the right places, the filter column's target equal to
the parent's target, and no cycles. Matching is **any-overlap** throughout — a
`multiple` parent, child, or filter cell all work, using the same `?|` idiom the search
path uses — so toggling `multiple` later never invalidates a pairing.

The decisions taken:

- **The options filter is opt-in per request, not baked into the endpoint.**
  `GET .../options?parent_value=<uuid>` (repeatable) constrains a *search*; the server
  derives the filter column from stored config and ignores the param on an un-nested
  column. Resolve mode (`uuid=`) is deliberately unfiltered — a held value must always
  get its label — and so are the view-filter value picker and QR label resolution,
  which have no parent context.
- **Writes are the enforcement point.** `ListItemService._enforce_nested_lookups`
  (end of `_validate_data`, so import and QR fills inherit it) rejects values being
  **added** whose target row doesn't match the merged record's parent cell, and rejects
  an add while the parent is empty. Kept and untouched values pass — enabling nesting
  over existing data never locks a record (the choice-options precedent; see
  known-gaps).
- **Deleting or retargeting a referenced column cascade-clears the pairing**
  (`_clear_dependent_nesting`, global scan since filter references are cross-list),
  the same shape as `delete` nulling `primary_column_id`. Read/write paths tolerate a
  dangling pairing by degrading to a plain lookup.
- **Clone remaps `parent_column_uuid` through `col_map`** (the parent is a sibling,
  which was cloned) and leaves `target_filter_column_uuid` verbatim (it names a column
  of the target list, whose uuids don't change).

UI: ColumnDialog offers "Nest under" only for siblings with a qualifying filter column
and auto-picks the "Match on" column when only one qualifies; the record editor
disables a nested picker until its parent is set ("Select Country first"), filters
suggestions by the parent's *draft* value, and clears descendants transitively when a
parent changes. See `tests/test_nested_lookup.py`.

---

## 7. Cross-workspace lookup targets — **implemented**

**Shipped.** A `lookup` column may point at **any list the creator can `view`** — one in
another of their own workspaces, or one shared with them — not just a list they own. The
config-time gate in `ColumnService._validate_config` was relaxed from a same-owner
workspace join to `PermissionService.has(view)` on the target, with the acting user threaded
through `ColumnService.create`/`update`. A target the caller cannot view is refused
indistinguishably from one that doesn't exist, so `target_list_uuid` can't be used to probe
for a stranger's lists.

**Resolution was already permission-correct, so nothing there changed.** `lookup_options`,
export and search each re-check the **reader's** `view` on the target per-caller (data-model
D4; [known gaps](known-gaps.md): "Resolving a label means reading the target list"), so a
cross-owner target never leaks a label — a viewer who lacks target access sees uuid stubs,
exactly as before. Relaxing the *create-time* check simply lets a lookup point where
resolution could already safely follow. The one behavioral shift, documented in known gaps:
a target you don't own can have your access revoked or be deleted independently of the
referencing list, so "degrade to uuid stubs" becomes routine rather than exceptional.

UI: ColumnDialog gains a **Workspace** selector above **Target list** (defaults to the
current workspace, plus a "Shared with me" bucket), so lists sharing a name across
workspaces are unambiguous; on edit it resolves the stored target's workspace to open on the
right bucket. See `tests/test_lookup_options.py` (own-other-workspace, shared-in, and
refused-when-unviewable).

---

## 8. Live records (an open list reflects other people's changes) — **polling shipped, push planned**

**Shipped (polling).** An open list now refetches its records on an interval, so a row
someone else creates, edits or deletes shows up without a manual reload. It is one hook,
`useLiveList(listUuid)` (`frontend/src/lib/useLiveList.ts`), spread into the list's items
`useQuery` in `ListDetailPage`. Today its body hands react-query a `refetchInterval` of
`LIVE_REFRESH_SECONDS` (`lib/config.ts`, from `VITE_LIVE_REFRESH_SECONDS`, default **5s**,
`0` disables). Two guardrails hold the idle cost at zero: polling is **paused while the tab
is hidden** (`refetchIntervalInBackground: false`), *and* **paused after `IDLE_AFTER_MS`
(5 min) with no user activity on a still-visible tab** — a list left open on screen stops
refetching (and, on the free tier, stops keeping the instance awake) until the next
interaction.

**The load-bearing principle: a live update carries no record data.** Each refresh is a
normal, permissioned `GET /items/`, which re-applies `item_view_filter` — so a poll can
never surface a row the viewer can't see. This is not just a property of polling; it is the
*constraint the push design must also honour* (below). Per-record permissions are subtle
enough that the safe move is to never ship record data down a fan-out channel at all — you
signal "something changed," and each client re-reads through the endpoint that already knows
what it may see.

**Why polling first (the deliberate v1 trade).** On the free tier the API is a **single
uvicorn process** (deploy/render/render.yaml), and react-query, the 401→refresh flow, and
`keepPreviousData` are all already in place — so polling is ~an hour of work, is multi-worker
correct *for free* (every poll is an independent permissioned request; there is no fan-out to
get wrong), and sidesteps the auth friction below. The costs accepted: up to one interval of
latency, and a wasted query per *focused, active* tab. One caveat to know: on Render's free
tier an actively-used open tab polling **keeps the single instance awake** (it is traffic),
which is fine for a demo but spends free hours — the idle guardrail above caps this to tabs
someone is actually using, not ones merely left open.

**Planned (server push, near-instant).** When "live" needs to feel instant, or many viewers
watch one list (where polling's idle queries add up), replace the hook's body with a
Server-Sent Events subscription — **no call site changes**, which is the whole point of the
seam. The shape:

- **Transport is SSE consumed via `fetch`, not the native `EventSource`.** Auth is a Bearer
  token in `localStorage` (`api.ts`), and `EventSource` cannot set an `Authorization` header;
  a streamed `fetch` response can. This also lets a token expiring mid-stream reconnect
  through the existing refresh path rather than dropping silently.
- **A content-free endpoint**, `GET /lists/{uuid}/events`, **gated on list `view`** at
  connect, emitting only "list changed." It must **hold no pooled DB connection while
  streaming** — it waits on an in-memory queue, or the stream would pin a session for its
  whole life and exhaust the pool (see known-gaps on the two-connection rule). The client
  calls `queryClient.invalidateQueries(["items", listUuid])` per event, debounced so a
  200-row import fires one refetch, not 200.
- **Fan-out behind a two-method broker** (`subscribe(list_id)` / `notify(list_id)`), emitted
  from the one choke point every record write already passes through
  (`ListItemService.create`/`update`/`delete`/`bulk_delete`/`restore` + import). An
  **in-process asyncio broker is correct on today's single instance**; a paid/multi-worker
  deploy — or record-mutating crons running in a *separate* process (`reconcile_shapes`,
  purge) — needs **Postgres `LISTEN/NOTIFY`** instead: writers `NOTIFY` inside the write
  transaction (delivered on commit, so a rolled-back write signals nothing), and each worker
  holds **one dedicated listener connection created at startup, outside the request pool**.
  Building the broker interface now makes that a localized swap, the same way the deploy
  already defers the Alembic pre-deploy hook until it is paid for.

**Needs a decision (only when push is built):**
- **What else is "live."** Scoped to records here. The same channel extends to column, view
  and form changes; decide whether a schema change should also nudge open viewers or stay
  manual.
- **Debounce/coalesce window** for bursty writes (imports), and whether the originating
  client suppresses its own echo (it already invalidates locally on its mutation) via a
  client id, or just tolerates one redundant refetch.

---

## 9. List metrics (totals, averages and counts over the records) — **implemented**

**Shipped.** A list can now say something *about* its records rather than only showing
them. A **metric** is a first-class per-list resource — a sibling of views and forms, in
its own `metrics` table (data-model §9) — holding a name and a formula, rendered as a
strip of cards above the grid. `SUM(hours)`, `COUNT()`, `AVG(cost)`,
`SUM(revenue) - SUM(cost)`.

**What already existed to build on, and what didn't.** `columns/formula.py` was a complete,
audited expression language — no `eval`, an allow-list AST walker, bounded length/depth/text
— but strictly *row*-scoped: its `MIN`/`MAX` are varargs over one record. So the aggregate
language (`metrics/aggregate.py`) is **a second grammar layered on it, not a fork**: the
outer level is arithmetic over aggregate results, and an aggregate's *argument* is an
ordinary row expression checked and evaluated by `formula.py` itself. `SUM(qty * price)`
therefore works for free, and every safety property of that module holds unchanged. Five
names in it became public to carry the seam (`ROW_FUNCTIONS`, the three bounds, and two
`*_row_expression` wrappers).

**The two rules that make the levels unambiguous.** A bare column name at the outer level is
a parse error — `hours` is one value per record, a metric is one value in total, so the
only sensible reading is a missing aggregate. And `MIN`/`MAX`, which exist in both tables,
are told apart by arity: one argument is the aggregate, several is the row function picking
between the values written there. Nothing is lost, because a one-argument row `MIN` is its
own argument.

**Evaluation is a Python scan, deliberately.** `GET /lists/{uuid}/metrics/values` answers
every card in **one pass** — so the cards always agree with each other — over
`ListItemService.visible_stmt`, which was extracted out of `ListItemService.list` so the
cards and the grid cannot disagree about which records are on screen. It cannot be SQL
`SUM`: formulas take arbitrary row expressions, and a `calculated` column has no stored
value to aggregate. That last point is also the feature's nicest property — **a metric may
aggregate a calculated column**, which a calculated column may not reference, because a
metric is a *leaf*: nothing derives from one, so there is no cycle to detect.

**Load-bearing details.**

- **Permissions.** The scan runs through `item_view_filter`, so two readers with different
  grants correctly get different numbers. Pinned by
  `tests/test_metrics.py::test_two_readers_get_the_totals_of_what_each_can_see`.
- **Bounded.** `METRIC_ROW_LIMIT` (50,000), streaming `data` alone in batches of 1,000,
  with `truncated` in the response and a note on the card. A metric is recomputed per
  request *and* re-asked on the live-records interval, so an unbounded scan was a way to
  spend the whole server.
- **`_shape_for_read` was split** into `computed_columns` + a pure `shape_values`, so the
  scan shapes records exactly as the grid does without hydrating 50k ORM instances. The
  `set_committed_value` call stays where it was — see its docstring for why that matters.
- **A renamed column reports an error on its card, not `0`.** Refs are checked against the
  live columns before scanning; the break costs its own card, never the request.
- **Cascade/restore/clone.** `Metric` joined `_LIST_CHILD_MODELS`, which covers delete and
  restore in one line. Clone copies the rows **without remapping**: a formula names columns
  by *name* and a clone keeps every name — the opposite of the view/form config problem in
  §5.
- **Frontend.** `features/metrics/` — `MetricBar` (the strip), `MetricsPanel` (a new
  tab in list settings), `MetricDialog` (built on the `ColumnDialog` pattern). The values
  query key is nested **under the items key**, so every existing
  `invalidateQueries(["items", listUuid])` refreshes the cards for free.
- **The strip is hidden by default**, behind a `Metrics` toggle on the list toolbar
  (badged with how many the list has, so a hidden strip is distinguishable from a list with
  none). `visible` gates the *query*, not just the markup, which is what stops every open
  list from running a repeating full scan on the live interval. Page state, like the rest of
  that bar — the app has no per-list UI preferences to persist it in.

**Deliberately not built:** per-group subtotals (a view's `group_by` is a sort, not a
`GROUP BY`, and the strip sits above the grid), standalone metric sharing (no
`permissions_inherit`, so no new `ResourceType`), a SQL fast path, and any caching. See
[known gaps §9](known-gaps.md).

---

## How these compose

- **1 + 2** share one conversion-and-backfill engine; type conversion is best *delivered as* a
  guided data migration (add column → migrate → drop), which makes it reversible.
- **3 + 4** shipped together: the same move, split by whether the target workspace has a
  different owner. Same-owner is a plain workspace picker (`configure` on the target); cross-owner
  is a transfer into the recipient's immutable inbox (owner-only, no target `configure` needed).
- **5** is independent, but its uuid-remapping discipline is the same one **3/4 get for free**
  (a move keeps uuids; a clone must not).
- **8** stands alone and touches none of the data-shape work; its one shared idea is the
  permission boundary — a live signal never carries record data, so every client re-reads
  through the endpoint that already enforces `item_view_filter`.
- **9** inherits that same boundary — a total is a permissioned read of the records, which
  is why it goes through the grid's own statement — and rides **8**'s refresh interval, so
  the cards stay in step with the rows they describe. Its formula language extends the one
  `calculated` columns already had rather than adding a second, which is the reverse of the
  **1 + 2** relationship: those two share an engine because they do the same thing to
  different columns; this shares an engine because it does a *different* thing to the same
  expressions.
