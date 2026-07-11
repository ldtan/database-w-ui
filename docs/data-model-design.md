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

### D3 — Choice options carry stable IDs
A `choice` column stores its options as `[{id, label, color}]`. A record stores the
**option `id`**, not the label. Renaming/recoloring an option never touches records.

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

---

## 2. Entity overview

```
User
 └─ owns ─▶ Workspace
              └─ contains ─▶ List ──┬─▶ Column    (schema: the fields)
                                    ├─▶ ListItem  (records: JSONB keyed by column uuid)
                                    ├─▶ View      (saved sort/filter/group/paging)
                                    └─▶ Form       (field layout for create/update)
```

`Workspace` is the tenancy boundary: a user owns workspaces, and a list's ownership
flows through its workspace (§3). It is also the natural boundary for future sharing.

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
  default `create`/`update` forms (§7, §8).
- Lists are created with a `workspace_uuid` in the body and can be moved between
  workspaces via update.

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
| `text`        | `string`                                   | `min_length?`, `max_length?`, `pattern?` |
| `long_text`   | `string`                                   | `min_length?`, `max_length?`, `pattern?` |
| `number`      | `number`                                   | `min?`, `max?`, `precision?`, `integer_only?`, `number_format?` (`plain`/`currency`/`percent`) |
| `checkbox`    | `boolean`                                  | — |
| `choice`      | option `id` (or `[id, …]` if multi)        | `choices?` (shorthand) **or** `options: [{id, label, color}]`; `multiple?` |
| `lookup`      | target row `uuid` (or `[uuid, …]` if multi)| `target_list_uuid`, `multiple?` |
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
- `choice` — options carry stable `id`s (D3); records store the id. The `choices`
  shorthand (`"Todo;Doing;Done"`) is expanded server-side into `options` with generated
  ids; on update, ids for **unchanged labels are preserved** so records stay valid.
  Explicit `options` (with colors) override the shorthand. A choice `default` may be given
  as an option label and is stored as the id.
- `lookup` — stores target-row `uuid`s (D4); the label to display comes from the
  target list's `primary_column_id`. A lookup column cannot have a `default`.
- **Config updates merge:** a `PATCH` to a column's `config` merges onto the stored
  config — settings you don't resend are preserved (rather than the whole config being
  replaced). To change a choice column's option set, resend `choices` or `options`.
- Temporal `min`/`max` and the value must share a timezone convention (both aware or both
  naive); a mismatch is a `400`, not a server error.
- `date` / `time` / `datetime` — stored as ISO-8601 strings and validated via Python's
  `fromisoformat`. The three separate types replace a single date type with an
  `include_time` flag.
- The set is extensible: adding e.g. `email`, `url`, `attachment`, `rating` is just a
  new enum member + `config` convention, no migration.

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
    "options": [
      {"id": "opt_todo",  "label": "To do", "color": "#gray"},
      {"id": "opt_doing", "label": "Doing", "color": "#blue"},
      {"id": "opt_done",  "label": "Done",  "color": "#green"}
    ]
  }
}
```

The same column can be created with the shorthand instead — the server expands it into
`options` with generated ids (`todo`, `doing`, `done`):

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

Controls which fields appear when creating/updating a record, their order, and whether
each is enabled. Field layout is JSONB (D5).

| Field        | Type            | Notes |
|--------------|-----------------|-------|
| `id`, `uuid`, timestamps, `deleted_at` | — | from `BaseModel` |
| `list_id`    | FK → `lists.id` | Owner list. |
| `name`       | `str(120)`      | Code-facing slug (D6). Unique **per list**. e.g. `quick_add`. |
| `display_name` | `str(255)`    | UI label (D6). e.g. "Quick add". |
| `type`       | `enum` `FormType` (`create`/`update`/`custom`) | Distinguishes the two defaults. |
| `is_default` | `bool`          | The default create/update form for the list. |
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
- **Empty `fields` means "all columns"** — auto-provisioned default forms start with
  `fields: []`, which consumers interpret as every column included in `position` order.
  Once the layout is customized, columns absent from a non-empty `fields` list are treated
  as not included.

---

## 9. Future / deferred (documented, not built yet)

- **`list_item_links` junction table:** the D4 upgrade path — real FKs, referential
  integrity, cheap reverse lookups, clean many-to-many links. Adopt when soft references
  become a correctness problem. (Orphan cleanup on delete is implemented in the meantime,
  see §6.)
- **Sharing / permissions:** per-list or per-workspace roles. The `workspaces` tenancy
  boundary (§3) is now in place, so this can be layered on without restructuring.
- **Computed / rollup columns:** derive values from lookups (count, sum of linked rows).
- **Additional column types:** `email`, `url`, `attachment`, `rating`, `user` (assignee).

Implemented since the first draft: the `workspaces` tenancy layer (§3), default
view/form auto-provisioning on list creation (§3), and lookup orphan cleanup on delete (§6).

---

## 10. Integrity rules summary (app layer)

Because the DB does not enforce record-level constraints, the service layer MUST:

1. Validate each `data` value against its column `type` and `config` on write.
2. Enforce `required`, `unique`, `min`/`max`, `max_length` where configured.
3. Resolve/validate `choice` values against current option ids.
4. Validate `lookup` uuids exist in the target list; clean up stale refs on delete.
5. Validate `name` against the `NameSlug` pattern and scope all uniqueness checks —
   list `name` per owner; column/view/form `name` per list — excluding soft-deleted rows.
6. Filter `deleted_at IS NULL` on every read path.
