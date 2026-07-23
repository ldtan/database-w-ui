# Planned features

Five capabilities that have been asked for and are worth building next. Each section
says what it is, what already exists to build on, the load-bearing implementation
details, and the decisions still open. Nothing here is built yet unless a subsection
says otherwise.

These share two roots in the existing model, so read them once here rather than five times below:

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
read, converge in the background" pattern is the right instinct — a synchronous UPDATE over every
row inside a web request is measured at ~25s per 1M rows holding row locks, which is not
acceptable. But a type conversion **cannot** be tolerated on read the way a shape mismatch can:
`"42"` and `42` are the same value in two shapes, whereas `"May 4, 2026"` and the date
`2026-05-04` are not — one has to be *computed* from the other. So conversion needs an explicit,
chunked, killable backfill job (model it on `reconcile_shapes.py`), not a read-time coercion.

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

**Needs a decision:**
- **Which pairs are supported?** A conservative v1 is the safe conversions above plus
  text↔number; everything else deferred. The alternative is a full matrix with per-pair rules.
- **Unconvertible-cell policy.** When some cells convert and some don't (`text` → `number` over a
  column with a stray `"n/a"`), do we (a) refuse the whole conversion, (b) blank the offenders, or
  (c) best-effort and return a per-row error report? Import already does (c) — reuse its
  `{created, errors}` summary shape.
- **Sync or async.** Small lists could convert in the request; large ones need the background job.
  Picking one boundary (row count) keeps a single code path.

**Recommended shape:** this feature is best delivered *through* feature 2, not as an in-place
mutation — add a new column of the target type, migrate/convert the data into it, verify, then
drop the old column. That makes every conversion reversible until the user commits, and it means
type conversion and data migration share one engine.

---

## 2. Column data migration (copy one column's data into another)

Bulk-populate a target column from a source column across every record in a list — the
"add a column, move the data over, delete the old one" workflow, and the safe delivery
vehicle for feature 1.

**What it is mechanically.** For each record, read the value under the source column's uuid and
write it under the target's. If source and target are the **same type**, that's a straight copy
of the JSONB value. If they differ, each cell goes through feature 1's conversion — which is why
these two features share an engine and should ship together.

**The same performance boundary as feature 1.** A copy over a large list is the same
per-row UPDATE over JSONB, so it belongs in the same chunked, committed-per-batch, killable job
as `reconcile_shapes` — not a web request. Include soft-deleted records or not per the same
reasoning `reconcile_shapes` uses (it does, so a restore doesn't resurrect a stale shape).

**Two edges that will bite:**
- **`unique` on the target.** Copying a source with repeats into a `unique` target creates
  duplicates. `_enforce_unique` will 409 — decide whether that aborts the whole migration or
  skips-and-reports, same question as feature 1's unconvertible cells.
- **`multiple` shape mismatch.** A single→multiple copy wraps each value in a one-element list; the
  reverse takes the first element. `coerce_shape` already expresses exactly this — call it, don't
  re-derive it.

**Needs a decision:**
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

## How these compose

- **1 + 2** share one conversion-and-backfill engine; type conversion is best *delivered as* a
  guided data migration (add column → migrate → drop), which makes it reversible.
- **3 + 4** shipped together: the same move, split by whether the target workspace has a
  different owner. Same-owner is a plain workspace picker (`configure` on the target); cross-owner
  is a transfer into the recipient's immutable inbox (owner-only, no target `configure` needed).
- **5** is independent, but its uuid-remapping discipline is the same one **3/4 get for free**
  (a move keeps uuids; a clone must not).
