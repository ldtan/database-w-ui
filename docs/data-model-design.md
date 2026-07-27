# Data Model Design — Lists / Tables App

Status: **Implemented (backend)**
Last updated: 2026-07-11

This document specifies the data model for the app: users create **lists** (tables),
define **columns** (fields) on them, store records as **list items**, and configure
**views** and **forms** over each list.

It builds on the existing stack and conventions:

- **Stack:** FastAPI + SQLAlchemy 2.0 (async) + asyncpg + PostgreSQL.
- **`BaseModel`** (`src/core/models.py`) already provides on every table:
  `id` (int PK), `uuid` (uuid7, unique), `created_at`, `updated_at`,
  `deleted_at` (nullable — soft delete).
- Field types that need enforcement (required, type, FK) are validated in the
  **Pydantic / service layer**, not by the database, because record data lives in JSONB.

---

## 1. Architecture decisions (read first)

These are the load-bearing choices. Everything else follows from them.

### D1 — Records are stored as JSONB, one row per record
`list_items.data` is a single `JSONB` column holding the whole record.
We are *not* creating a physical table per list, and *not* using EAV.

- **Pro:** fully dynamic schema, no migrations when columns change, simple to reason about.
- **Con (accepted):** the database will **not** enforce types, requiredness, defaults,
  or foreign keys inside `data`. All of that moves into the app layer.

### D2 — JSONB is keyed by column UUID, never by column name
`data` looks like `{"<column.uuid>": <value>}`, e.g.
`{"9d1c…": "Done", "a4f0…": 42}`.

Neither `name` nor `display_name` (see D6) is ever the key — the stable key is the
column's immutable `uuid`. Because of that, renaming a column (either identifier) is
free and never orphans data. This is the single most important integrity rule in the
system.

### D3 — Choice options are plain labels; a cell stores the label
A `choice` column stores its options as a list of label strings, and a record stores
the **label itself**. The value on screen is the value in the database — import, QR
codes and the API all speak the same string. The trade-off is deliberate: renaming an
option is a remove+add, and records holding the old label keep it (they display as-is
but must pick a current option on their next edit). An earlier design carried stable
per-option ids to make renames free, but the id was invisible everywhere in the UI
while being required on every out-of-band write — a worse trade.

### D4 — Links/lookups reference rows by `uuid`, with no DB-level FK
A `lookup` column stores the target row's `uuid` (or an array of uuids). There is no
foreign key, so **orphan cleanup on delete is the app's responsibility**. A junction
table is documented in §8 as the future upgrade path if referential integrity is needed.

### D5 — Presentation config (views, forms) is stored as structured JSONB
View sort/filter/group and form field layout are stored as JSONB blobs on the `views`
and `forms` rows rather than normalized child tables. They are presentation metadata,
rarely queried relationally, and this avoids extra joins.

### D6 — `lists`, `columns`, `views`, `forms` carry two identifiers: `name` + `display_name`
- **`name`** — the code-facing identifier. A slug matching `^[a-z][a-z0-9_]*$`
  (starts with a lowercase letter; lowercase letters, digits, underscores only,
  ≤ 63 chars). Both are **required and explicit** — the client always sends both; `name`
  is never auto-derived. `name` is **unique per scope** (list per owner; column/view/form
  per list) and is **editable** (re-validated + uniqueness-checked on update).
- **`display_name`** — free-text label the UI shows. Not unique.

The shared slug type lives in `src/core/schemas.py` as `NameSlug`. `list_items`
(records) have **no** name — they are pure data. Nothing in the system references these
resources by `name` internally (uuids do that job, per D2/D4), so `name` is purely a
handle for API/code consumers and is safe to rename.

**A name is accepted wherever a child's uuid is** — `/lists/{list}/columns/first_name`
is the same column as `/lists/{list}/columns/019f…`, and likewise for views, forms, and
the `?view_uuid=` an item listing takes. A slug can never parse as a uuid, so the two
are unambiguous; `src/lists/refs.py::get_by_ref` is the one resolver. It matches **live
rows only**, because the unique index on `name` is partial and a deleted `notes` may sit
beside a live one.

The uuid stays canonical: responses carry it, the frontend holds it, and a rename moves
the name-URL out from under anyone who bookmarked it. **Lists themselves are addressed
by uuid alone** — a list's name is unique only per *workspace*, which `/lists/{x}` does
not carry, and a guessable identifier would turn the 403-vs-404 distinction on that
route into an enumeration oracle. A column is already behind `view` on its list, so
naming one tells a caller nothing they could not already see.

---

## 2. Entity overview

```
User
 └─ owns ─▶ Workspace
              └─ contains ─▶ List ──┬─▶ Column    (schema: the fields)
                                    ├─▶ ListItem  (records: JSONB keyed by column uuid)
                                    ├─▶ View      (saved sort/filter/group/paging)
                                    └─▶ Form       (field layout for create/update/view)
```

`Workspace` is the tenancy boundary: a user owns workspaces, and a list's ownership
flows through its workspace (§3). It is also the natural boundary for future sharing.

`User` also carries a `settings` `JSONB` column (default `{}`) holding account-level UI
preferences — colour theme and light/dark today. Unlike the free-form
`workspaces.settings` / `lists.settings` blobs below, it is validated against a fixed
Pydantic schema (`UserSettings`), so it is published in the OpenAPI document and the
frontend's types are generated rather than hand-mirrored. Writes go through
`PATCH /auth/users/me/settings` and **merge**: only the keys sent are touched, so a client
that has never heard of a key cannot drop it. Preferences belong to the account rather
than the browser, which is what lets them follow a user between devices.

---

## 3. `workspaces` and `lists`

### `workspaces`

The tenancy container. A user owns workspaces; lists live inside them.

| Field          | Type            | Notes |
|----------------|-----------------|-------|
| `id`, `uuid`, timestamps, `deleted_at` | — | from `BaseModel` |
| `owner_id`     | FK → `users.id` | **Required.** Who owns the workspace. |
| `name`         | `str(120)`      | Code-facing slug (D6). Unique **per owner**, ignoring soft-deleted rows. |
| `display_name` | `str(255)`      | UI label (D6). Not unique. |
| `description`  | `Text` nullable | Optional. |
| `settings`     | `JSONB` default `{}` | Misc workspace-level settings. |
| `is_system`    | `bool` default `false` | Marks the per-user **"Incoming"** workspace that transferred lists land in (see the lists notes below). Immutable — cannot be renamed or deleted; minted lazily on the first transfer to a user. |

### `lists`

A table/list definition and its settings, scoped to a workspace.

| Field              | Type            | Notes |
|--------------------|-----------------|-------|
| `id`, `uuid`, timestamps, `deleted_at` | — | from `BaseModel` |
| `workspace_id`     | FK → `workspaces.id` | **Required.** The owning workspace; ownership flows through it. |
| `name`            | `str(120)`      | Code-facing slug (D6). Unique **per workspace**, ignoring soft-deleted rows. |
| `display_name`     | `str(255)`      | UI label (D6). Not unique. |
| `description`      | `Text` nullable | Optional. |
| `primary_column_id`| FK → `columns.id` nullable | The "display field" used to label a row when it is referenced by a lookup (Airtable's primary field). Nullable to break the chicken/egg on creation. |
| `settings`         | `JSONB` default `{}` | Misc list-level settings (color, icon, default view id, etc.). |

Notes:
- Ownership is transitive: list queries join `workspaces` and filter on
  `workspaces.owner_id`. The `get_list_or_404` dependency enforces this for all child
  resources, so an unowned (or wrong-workspace) list — and everything under it — is a 404.
- Uniqueness: partial unique indexes on `(owner_id, name)` for workspaces and
  `(workspace_id, name)` for lists, both `WHERE deleted_at IS NULL`.
- **Auto-provisioning (implemented):** creating a list seeds a default `grid` view and
  default `create`/`update`/`view` forms (§7, §8).
- Lists are created with a `workspace_uuid` in the body and can be **moved** between
  workspaces via update (`PATCH` with `workspace_uuid`; requires `configure` on both the
  list and the target workspace). A list can also be **transferred to another user** via
  `POST /lists/{uuid}/transfer`: ownership flows through the workspace, so the transfer
  moves the list into the recipient's immutable `is_system` "Incoming" workspace (minted on
  demand), making them the owner. Owner-only; existing grants survive (they key on the
  unchanged `resource_id`); the giver's implicit access ends. Both must assign the
  `workspace` **relationship**, not the `workspace_id` FK — see
  [known gaps](known-gaps.md). Details in [planned features §3–4](planned-features.md).
- A list can be **cloned** via `POST /lists/{uuid}/clone` `{ with_records }` — a copy in
  the same workspace, structure always, records optionally. Because records are keyed by
  column **uuid** (D2), the clone's new columns get new uuids and every reference is
  rewritten through an old→new column-uuid map: record `data` keys, `primary_column_id`,
  and the column-uuid refs in view/form configs. Cell *values* copy verbatim (choice ids,
  lookup/user/group uuids); a self-lookup is left pointing at the original list. Unlike a
  create, cloning does **not** run `_provision_defaults` (it copies the source's real
  columns/views/forms) and does **not** copy grants — the clone starts private. Details in
  [planned features §5](planned-features.md).

---

## 4. `columns`

Defines one field on a list. This is the schema.

| Field         | Type              | Notes |
|---------------|-------------------|-------|
| `id`, `uuid`, timestamps, `deleted_at` | — | from `BaseModel`. **`uuid` is the JSONB key** (D2). |
| `list_id`     | FK → `lists.id`   | Owner list. |
| `name`        | `str(120)`        | Code-facing slug (D6). Unique **per list** (ignoring soft-deleted). |
| `display_name`| `str(255)`        | UI label (D6). Not unique. |
| `type`        | `enum` (`ColumnType`) | See §5. Immutable after creation. |
| `position`    | `int`             | Ordering within the list's schema. |
| `config`      | `JSONB` default `{}` | Type-specific settings — see §5. |

`config` is where every per-type detail lives (options, precision, defaults,
required flag, lookup target). Keeping it in one JSONB blob means adding a new
column type never requires a migration.

Common `config` keys (all optional unless noted):

```jsonc
{
  "required": false,        // enforced in app layer
  "default": null,          // default value for new records
  "help_text": "",          // shown in forms
  "unique": false           // enforced in app layer
  // ...plus type-specific keys below
}
```

---

## 5. Column types (`ColumnType` enum)

| `type`        | Stored value in `data`                     | Type-specific `config` |
|---------------|--------------------------------------------|------------------------|
| `text`        | `string`                                   | `text_mode?` (`single`/`multiline`), `min_length?`, `max_length?`, `pattern?` |
| `number`      | `number`                                   | `min?`, `max?`, `precision?`, `integer_only?`, `number_format?` (`plain`/`currency`/`percent`) |
| `checkbox`    | `boolean`                                  | — |
| `choice`      | option label (or `[label, …]` if multi)    | `choices?` (shorthand) **or** `options: [label, …]`; `multiple?` |
| `lookup`      | target row `uuid` (or `[uuid, …]` if multi)| `target_list_uuid`, `multiple?`, `display_column_uuid?`, `parent_column_uuid?` + `target_filter_column_uuid?` (nested lookup, set together) |
| `link`        | URL `string`                               | `allowed_schemes?` (default `["http", "https"]`), `label?`, `max_length?` |
| `user`        | `users.uuid` (or `[uuid, …]` if multi)     | `multiple?` |
| `group`       | `user_groups.uuid` (or `[uuid, …]`)        | `multiple?` |
| `date`        | ISO-8601 date `string` (`YYYY-MM-DD`)      | `min?`, `max?` (ISO date); `default: "today"` |
| `time`        | ISO-8601 time `string` (`HH:MM[:SS]`)      | `min?`, `max?` (ISO time); `default: "now"` |
| `datetime`    | ISO-8601 date-time `string`                | `min?`, `max?` (ISO date-time); `default: "now"` |

Common keys on **every** type: `required`, `default`, `unique`, `help_text`, plus the
UI-only hints `placeholder` and `read_only` (stored, not enforced by the API).

`min`/`max` are shared keys interpreted per type: numbers for `number`, ISO strings for
the temporal types. `lookup` references its target list by **uuid** (`target_list_uuid`),
not internal id, so the config stays API-portable.

Notes:
- **Every constraint is enforced on write** (both when a record is saved and against the
  column's own `default`): text `min_length`/`max_length`/`pattern` (regex, full match),
  number range and `integer_only`, temporal range, and choice membership. Config is also
  validated when the column is created/updated (e.g. `min` ≤ `max`, well-formed dates and
  regex, a `default` that is itself a valid value).
- **`unique` is enforced:** on write, no other non-deleted record in the list may hold the
  same value for that column (checked with a JSONB `@>` containment query). Violations
  return `409`. Empty and multi-valued (list) values are skipped. The check and the insert
  are made atomic by a transaction-scoped Postgres advisory lock keyed on the list (taken
  only when the list has a unique column), so concurrent writers can't both pass the check.
  Note this guards writers going through the service layer, not arbitrary direct DB writes.
- **Dynamic defaults:** temporal columns accept `default: "today"` (date) or `"now"`
  (time/datetime), resolved to the current value at insert time rather than stored as a
  fixed literal.
- `choice` — options are plain labels and records store the label (D3). The `choices`
  shorthand (`"Todo;Doing;Done"`) is split server-side into `options`; explicit
  `options` (a list of strings) override the shorthand. Labels must be unique within
  the column, and a `default` must be one of them.
- `lookup` — stores target-row `uuid`s (D4); the label to display comes from the lookup's
  `display_column_uuid`, falling back to the target list's `primary_column_id`
  (`display_column_key`). A lookup column cannot have a `default`. Its **target may be any
  list the creator can `view`** — their own, one in another of their workspaces, or one
  shared with them — not just a list they own; the target that can't be viewed is refused
  like one that doesn't exist, so the field can't probe for a stranger's lists. Config-time
  validation checks the *creator's* `view`; resolving labels later re-checks the *reader's*
  `view` on the target (see known-gaps), so a cross-workspace target never leaks. Like `user`/`group`, a cell
  accepts a uuid **or** a label and stores the uuid — which is what lets an exported file,
  where the label is what's written, be imported straight back. A label matching more than one
  row is rejected (row labels are not unique), so ambiguity is an error, never a guess.
  A lookup may also **nest** under a sibling lookup (`parent_column_uuid` +
  `target_filter_column_uuid`, set together): the target list's filter column points back at
  the parent's target, and what the parent cell holds constrains which target rows the child
  may add (options filtering is opt-in per request; writes enforce). See planned-features #6.
- `link` — **the scheme is a security boundary, not a preference.** The value is rendered as
  an anchor, so a `javascript:` or `data:` URL would be stored XSS. A value must parse as an
  absolute URL whose scheme is in the column's `allowed_schemes`, and a column may only
  *narrow* that set — `SAFE_LINK_SCHEMES` (`http`, `https`, `mailto`, `tel`) is the ceiling,
  because whoever configures a column is an ordinary list editor, not a trusted admin.
  Enforced in `validate_scalar`, so import and the column's own `default` go through it too;
  the frontend re-checks before it builds an `href` (`lib/links.ts`). `label` is optional
  fixed anchor text shown instead of the raw URL.
- **Config updates merge:** a `PATCH` to a column's `config` merges onto the stored
  config — settings you don't resend are preserved (rather than the whole config being
  replaced). To change a choice column's option set, resend `choices` or `options`.
- Temporal `min`/`max` and the value must share a timezone convention (both aware or both
  naive); a mismatch is a `400`, not a server error.
- `date` / `time` / `datetime` — stored as ISO-8601 strings and validated via Python's
  `fromisoformat`. The three separate types replace a single date type with an
  `include_time` flag.
- `user` / `group` — **`lookup` with a fixed target**: same soft-reference shape (store a
  uuid, resolve the label on read), but pointing at the `users` / `user_groups` table instead
  of a list, so there is no `target_list_uuid`. An assignee field is the motivating case.
  Neither can have a `default` (it would name an account that later gets deleted), and both
  live in `REFERENCE_TYPES` — the set the pure validators skip because their values are
  checked against the database. Three things about them are load-bearing:
  - **The label is the `username` / group name, never the connected person's real name.**
    `/auth/users` is readable by any authenticated user precisely so the Share dialog can
    list principals — but **person records are private** (owner or superuser only, see §11
    and `src/people`). A cell rendering "Grace Hopper" would leak personal data to every
    viewer of the list. `PrincipalControls.tsx` and the export formatter both resolve to the
    username.
  - **Deleting a user or group scrubs the references.** No FK backs them (D4), so
    `UserService.delete` / `UserGroupService.delete` call
    `ListItemService.scrub_principal_references`, which nulls the cell (or drops the entry
    from a multi-valued one) — the same treatment `lookup` gets when a target row goes away.
  - **A cell accepts a uuid *or* a name, and always stores the uuid.** The UI sends uuids;
    accepting a name is what lets an exported CSV (which writes names, because a file of
    uuids is useless to a human) be imported straight back. `lookup` does the same with row
    labels.
- The set is extensible: adding e.g. `email`, `attachment`, `rating` is just a new enum
  member + `config` convention — no migration of the *records*, since `data` is JSONB.
  **The `ColumnType` enum itself does need a hand-written migration** — autogenerate is blind
  to enum labels; see `migrations/versions/b1f7c4a90e12_add_link_column_type.py` for the
  pattern, and the startup drift guard (`src/infrastructure/schema_check.py`) for the
  backstop when you forget.

A new type must be added to **every** frontend touchpoint or it half-works:
`lib/columnTypes.ts` (`COLUMN_TYPE_OPTIONS`), `features/records/renderCell.tsx` (how a value
*displays*), `RecordField` in `features/records/RecordFields.tsx` (how it is *edited*),
`FilterConditionsEditor.tsx`, and the export/import cell handling in
`src/lists/transfer/services.py`. Both record components were extracted out of
`ListDetailPage.tsx`/`RecordFormDialog.tsx` precisely so that there is one of each: the grid,
the phone cards, the bin and the view-form dialog all display through `renderCell`, and the
add/edit dialog and the QR session both edit through `RecordField`.

**Example — a "Status" choice column**

```jsonc
// columns row
{
  "uuid": "9d1c…",
  "name": "status",
  "display_name": "Status",
  "type": "choice",
  "config": {
    "required": true,
    "multiple": false,
    "options": ["To do", "Doing", "Done"]
  }
}
```

The same column can be created with the shorthand instead — the server splits it into
`options`:

```jsonc
{ "name": "status", "display_name": "Status", "type": "choice",
  "config": { "required": true, "choices": "To do;Doing;Done" } }
```

---

## 6. `list_items`

One record. All values live in a single JSONB blob keyed by column uuid (D2).

| Field       | Type            | Notes |
|-------------|-----------------|-------|
| `id`, `uuid`, timestamps, `deleted_at` | — | from `BaseModel` |
| `list_id`   | FK → `lists.id` | Owner list. |
| `data`      | `JSONB` default `{}` | `{"<column.uuid>": <value>, …}` |

Example, for the schema above plus a text "Title" column `a4f0…`:

```jsonc
{
  "uuid": "row-123…",
  "list_id": 7,
  "data": {
    "a4f0…": "Ship the design doc",
    "9d1c…": "opt_doing"
  }
}
```

Indexing & querying:
- Add a **GIN index** on `data` to support filtering.
- Filtering/sorting on JSONB is slower than native columns — acceptable for this tier;
  do not promise Airtable-scale performance on very large lists.
- When a column is deleted, its key can be left in `data` (harmless) or lazily pruned.
- When a lookup target row is deleted, referring records would otherwise hold a **stale
  uuid** (there is no FK, per D4). **Implemented:** deleting a `list_item` scrubs its uuid
  from every lookup column that targets its list — clearing single values and filtering it
  out of multi-value arrays.

---

## 7. `views`

A saved way of looking at a list: sort, filter, group, paging. Config is JSONB (D5).

| Field       | Type            | Notes |
|-------------|-----------------|-------|
| `id`, `uuid`, timestamps, `deleted_at` | — | from `BaseModel` |
| `list_id`   | FK → `lists.id` | Owner list. |
| `name`      | `str(120)`      | Code-facing slug (D6). Unique **per list**. e.g. `active_tasks`. |
| `display_name` | `str(255)`   | UI label (D6). e.g. "Active tasks". |
| `position`  | `int`           | Order in the view switcher. |
| `config`    | `JSONB`         | See shape below. |

`config` shape (column references use `column_uuid`):

```jsonc
{
  "filters": {
    "conjunction": "and",           // "and" | "or"
    "conditions": [
      {"column_uuid": "9d1c…", "operator": "is",  "value": "opt_doing"},
      {"column_uuid": "a4f0…", "operator": "contains", "value": "design"}
    ]
    // (nested groups can be added later by allowing a condition to be another group)
  },
  "sorts": [
    {"column_uuid": "a4f0…", "direction": "asc"}
  ],
  "group_by": {"column_uuid": "9d1c…"},   // or null
  "hidden_columns": ["a4f0…"],            // columns to hide; empty = show all
  "column_order": ["a4f0…", "9d1c…"],     // per-view order; empty = position order
  "page_size": 50
}
```

Operators are per-type (e.g. `is`, `is_not`, `contains`, `is_empty`, `>`, `<`).
Validate the operator against the referenced column's `type` in the app layer. Every
`column_uuid` referenced must belong to the list (enforced on write) — including the
entries in `hidden_columns` and `column_order`. An empty `hidden_columns` shows every
column; an empty `column_order` uses the list's default order.

**Column order.** `columns.position` is the list's *default* column order. A view may
override it with `column_order` (an ordered list of column uuids); columns absent from
that list fall after it in `position` order, so newly-added columns still appear. Forms
carry their own order independently, via `config.fields[].order` (see §8). This keeps
`columns.position` as the shared default while letting each view and form reorder freely.

---

## 8. `forms`

Controls which fields appear when a record is created, updated or read, their order,
and whether each is enabled. Field layout is JSONB (D5).

| Field        | Type            | Notes |
|--------------|-----------------|-------|
| `id`, `uuid`, timestamps, `deleted_at` | — | from `BaseModel` |
| `list_id`    | FK → `lists.id` | Owner list. |
| `name`       | `str(120)`      | Code-facing slug (D6). Unique **per list**. e.g. `quick_add`. |
| `display_name` | `str(255)`    | UI label (D6). e.g. "Quick add". |
| `type`       | `enum` `FormType` (`create`/`update`/`view`/`custom`) | Distinguishes the three defaults. |
| `is_default` | `bool`          | The default create/update/view form for the list. |
| `config`     | `JSONB`         | See shape below. |

`config` shape (fields reference `column_uuid`):

```jsonc
{
  "fields": [
    {"column_uuid": "a4f0…", "included": true,  "enabled": true,  "order": 0, "label_override": null},
    {"column_uuid": "9d1c…", "included": true,  "enabled": true,  "order": 1},
    {"column_uuid": "b7e2…", "included": false, "enabled": true,  "order": 2}
  ]
}
```

- `included` — field appears on the form at all.
- `enabled` — field is editable vs. read-only/disabled.
- `order` — display order (array order can also carry this; keep `order` explicit to
  avoid ambiguity).
- **Empty `fields` means "all columns"** — a form with `fields: []` is interpreted as
  every column included in `position` order. The auto-provisioned `view` form starts
  this way; `create` and `update` instead start by naming the four audit columns as
  `included: false`, since nobody types "created by".
- **A column absent from a non-empty `fields` list is appended, not excluded.** It sits
  after the configured fields, in `position` order — the same rule a view's
  `column_order` follows (§7). Exclusion is `included: false`, which is what the form
  editor writes: it saves back *every* column, so absence can only mean the column was
  created after the last save. Treating absence as exclusion is exactly what used to
  leave the Add/Edit dialog on a freshly provisioned list empty ("Add a column first"),
  since its seeded config names the audit columns and nothing else.

**`view` forms are display-only, and two rules follow from it.** A view form lays a
record out for *reading* — it exists so a grid view can stay narrow (a dozen columns is
a dozen slivers) while every field stays one click away. So it **may include
`calculated` columns**, which `create`/`update` may not: a calculated value is computed
on read (§5, known-gaps §5) and rejected on write, so it is never an input but is
exactly what someone opens a record to see. And its fields' **`enabled` flag means
nothing** — nothing on it is editable. `resolveFields`
(`frontend/src/features/records/recordValues.ts`) takes an `includeCalculated` option
for the first; `RecordViewDialog` ignores `enabled` for the second, and the form editor
hides the checkbox rather than saving a setting no one reads.

---

## 9. Future / deferred (documented, not built yet)

- **`list_item_links` junction table:** the D4 upgrade path — real FKs, referential
  integrity, cheap reverse lookups, clean many-to-many links. Adopt when soft references
  become a correctness problem. (Orphan cleanup on delete is implemented in the meantime,
  see §6.)
- **Computed / rollup columns:** derive values from lookups (count, sum of linked rows).
- **Further column types:** `email`, `attachment`, `rating`. (`link`, `user` and `group` are
  built — see §5.)
- **Client-streamed export downloads:** export streams server-side (§ items), but the
  browser still buffers the whole file in memory before saving (an SPA with bearer auth
  can't stream a plain link to disk). For very large exports, stream to a temp file /
  object storage and hand back a short-lived signed URL the browser downloads directly.
- **Incremental (streaming) import parsing:** bulk import inserts row-by-row with batched
  commits, but reads the whole uploaded file into memory first. For very large uploads,
  parse the stream incrementally (e.g. chunked CSV, or JSON Lines) so memory stays flat.

See [Planned features](planned-features.md) for the larger asked-for capabilities that build
on this model. List ownership transfer, cross-workspace moves, and list cloning are now
**built**; column type conversion and column data migration remain, each with its
load-bearing details and open decisions.

Implemented since the first draft: the `workspaces` tenancy layer (§3), default
view/form auto-provisioning on list creation (§3), lookup orphan cleanup on
single-record delete (§6), cascade soft delete for lists/workspaces with
cross-list lookup-reference scrubbing (§10), and sharing / permissions (§11).

---

## 10. Integrity rules summary (app layer)

Because the DB does not enforce record-level constraints, the service layer MUST:

1. Validate each `data` value against its column `type` and `config` on write.
2. Enforce `required`, `unique`, `min`/`max`, `max_length` where configured.
3. Validate `choice` values against the current option labels (exact match).
4. Validate `lookup` uuids exist in the target list; clean up stale refs on delete.
5. Validate `name` against the `NameSlug` pattern and scope all uniqueness checks —
   list `name` per owner; column/view/form `name` per list — excluding soft-deleted rows.
6. Filter `deleted_at IS NULL` on every read path.
7. Cascade soft delete: deleting a list stamps `deleted_at` on its columns,
   records, views, and forms; deleting a workspace cascades to its lists and all
   their descendants — all in one transaction (bulk `UPDATE`s, no per-row loop).
   Lookup columns on *surviving* lists that referenced the deleted rows have that
   key dropped from their records (a lookup targets one list, so deleting it makes
   every value in the column stale).
8. Enforce permissions on every access path: reads are filtered to visible
   resources and writes require the matching action (see §11). The workspace owner
   is an implicit superuser over their own data.

---

## 11. Permissions / authorization

Access is granted to **users or groups** and can reach down to individual records,
forms, and views. See `src/permissions/`.

### `permission_grants`

One row per `(resource, principal)`. Grants point at a resource **polymorphically**
via `(resource_type, resource_id)` — no FK, because `resource_id` refers to a
different table per type. Existence is validated when a grant is created; the
resolver only ever walks live resources.

| Column | Notes |
| --- | --- |
| `resource_type` | `workspace` \| `list` \| `view` \| `form` \| `item` |
| `resource_id` | integer id within that type's table |
| `user_id` / `group_id` | exactly one is set (DB `CHECK`); FKs to `users`/`user_groups` |
| `actions` | JSONB list of action strings |

Partial unique indexes keep one grant per `(resource, user)` and per `(resource, group)`.

### Actions and implication

`view, create, edit, delete, configure, share, manage, lookup`.
`view/create/edit/delete` act on records and on reading/deleting the resource;
`configure` covers list settings, columns, forms, and views; `share` manages a
resource's own grants; `manage` is full control. A granted action expands to what it
implies — `manage → all`; `configure/share/edit/create/delete → {self, view, lookup}`;
`view → {view, lookup}` — and a principal's effective set is the union over all
applicable grants.

`lookup` is the one action that does **not** open the resource. It lets other
people's lookup columns resolve the list's row labels while the list itself stays
invisible: absent from `GET /lists/`, absent from "Shared with me", and refused by
`GET /lists/{it}` and everything beneath it. It is what a shared reference list
("Schools", "Departments") wants, and it is why the collection filters
(`workspace_view_filter`, `list_query_filter`) test the stored actions against
`CONFERRED_BY[view]` rather than matching on the existence of a grant row. Because
`view` confers `lookup`, every grant written before the action existed keeps
resolving labels unchanged.

Two consequences worth stating plainly:

- **What `lookup` discloses** is the display column's value for every row the
  per-record filter still allows — enumerable and searchable through the options
  endpoint (`?q=`). It is "read one or two columns of every row", not a peephole.
  It deliberately does *not* expose the target's schema: `GET /lists/{it}/columns/`
  stays refused, so the label column falls back to the target's own default.
- **The target picker needs an exception.** A list you cannot name is a list you
  cannot point a column at, so `GET /lists/?lookup_targets=true` widens the query
  from `view` to `lookup`. It is off by default and the column dialog is its only
  caller.

### Inheritance and the "unique" break

`item`, `view`, and `form` inherit their `list`'s grants; a `list` inherits its
`workspace`'s. Each of `lists`, `list_items`, `views`, `forms` carries a
`permissions_inherit` boolean (default `true`). When `false`, the resource is
**unique**: the resolver stops walking up and it stands on its own grants only —
this is how a single form/view is targeted at one group. The flag is reversible.

The resolver builds the chain of `(type, id)` nodes whose grants apply (stopping
where inheritance breaks), then unions the expanded actions from every grant on
those nodes that matches the user or one of their groups. The owning workspace's
owner short-circuits to all actions.

### Enforcement

- Reads are filtered to visible resources: `WorkspaceService.list_visible`,
  `ListService.list`, `ListItemService.list`/`query` (SQL conditions from
  `PermissionService`), and `ViewService.list`/`FormService.list` (per-row).
- Writes call `PermissionService.require(...)`: item CRUD → `create/edit/delete`;
  list/workspace settings, columns, forms, views → `configure`; deleting a
  list/workspace → `delete`; import → `create`+`edit`, export → `view`; changing
  any `permissions_inherit` flag → `share`.
- Item routes resolve the list without a list-level `view` check (an item can be
  shared directly), then enforce per item.
- `PermissionDeniedError` is mapped to HTTP 403 by an app-level handler.

Grant management lives under `/api/permissions`, alongside two endpoints the UI uses
to decide which controls to render:

- `GET /permissions/effective` — the caller's actions on one resource.
- `POST /permissions/effective` — the same for a batch of resources. Permissions vary
  per row (a record or view can carry its own grants), so the UI would otherwise need
  one request per row to gate a table. The batch resolver loads each type in a single
  query — which also warms the identity map for the chain walk — and fetches the
  caller's group membership and grant rows once for the whole batch.

User and group CRUD already exist under `/api/auth/users` and `/api/auth/groups`.

Exports stream through `item_view_filter` too, so they hide exactly the records the grid
hides.

Not yet done: admin-gating of user/group management and grant scrubbing on hard delete.
See [Known gaps](known-gaps.md) for these and the rest.
