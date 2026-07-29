# Database with UI — Backend

A FastAPI backend for a flexible "database as a UI" app. Users create **lists**
(tables), define **columns** (typed fields) on them, store **records**, and configure
**views** (saved sort/filter/group/paging) and **forms** (field layouts) over each list —
think a self-hostable, minimal Airtable/NocoDB.

Records are stored as `JSONB` keyed by column UUID, so a list's schema is fully dynamic:
adding, renaming, or removing a column never requires a database migration.

## Tech stack

| Concern        | Choice |
|----------------|--------|
| Web framework  | FastAPI |
| ORM            | SQLAlchemy 2.0 (async) |
| Database       | PostgreSQL (via `asyncpg`) |
| Auth           | JWT (RS256), Argon2 password hashing |
| Packaging      | [uv](https://docs.astral.sh/uv/) (`pyproject.toml` + `uv.lock`) |
| Python         | ≥ 3.14 |

## Features

- **Workspaces** — the tenancy boundary: a user owns workspaces, and each list belongs to
  one. List ownership flows transitively through the workspace. Every new user (including
  the seeded bootstrap account) is auto-provisioned a `personal` workspace so they can
  create lists immediately.
- **Lists** — tables (scoped to a workspace) with settings and a designated primary/display
  column. Creating a list auto-provisions a default grid view and create/update forms.
- **Columns** — typed fields: `text` (one line or several, per `config.text_mode`),
  `number`, `checkbox`, `choice`,
  `lookup` (link to another list's rows), `link` (a URL, restricted to safe schemes),
  `user` / `group` (reference an account or a group), `date`, `time`, `datetime`.
  Type-specific settings live in a per-column JSONB `config`.
- **Records** (`list_items`) — stored as JSONB keyed by column UUID; every value is
  validated against its column's type and config on write (required, choice options,
  lookup existence, etc.).
- **Views** — saved sort / filter / group / page-size, stored as structured JSONB.
- **Forms** — per-list field layouts (order, included, enabled) for create/update.
- **Two identifiers everywhere** — every list/column/view/form has a code-facing `name`
  (a slug, unique per scope) and a free-text `display_name` for the UI.
- **Soft deletes** and **owner-scoped access** on every resource.

See [`../docs/data-model-design.md`](../docs/data-model-design.md) for the full data-model
specification and the reasoning behind these choices.

## Project structure

```
backend/
├── main.py                  # App factory, lifespan (DB init + seed), router mounting
├── keys/                    # RSA keypair for JWT signing (private.pem / public.pem)
└── src/
    ├── core/                # config, base model, mixins, shared schemas, exceptions, utils
    ├── infrastructure/      # async engine, session, Base, get_db, init_db
    ├── auth/                # authentication domain
    │   ├── users/           # users + groups (models/schemas/services/routers)
    │   └── tokens/          # login / refresh tokens
    ├── workspaces/          # tenancy layer: a user owns workspaces; lists live in them
    └── lists/               # the lists feature (aggregate root = List)
        ├── models.py schemas.py services.py routers.py dependencies.py
        ├── columns/         # Column + ColumnType enum
        ├── items/           # ListItem (record data + validation)
        ├── views/           # View
        ├── forms/           # Form + FormType enum
        └── metrics/       # Metric + the aggregate formula language
```

Each resource follows the same four-file convention: `models.py`, `schemas.py`,
`services.py`, `routers.py`. Business logic and validation live in services; routers map
`ServiceError` subclasses (`ConflictError`, `UnknownReferenceError`, `ValidationError`)
onto HTTP status codes.

## Getting started

### Prerequisites

- Python ≥ 3.14 and [uv](https://docs.astral.sh/uv/)
- A running PostgreSQL instance

### 1. Install dependencies

```bash
uv sync
```

### 2. Provide JWT signing keys

The app signs tokens with RS256 and reads the keypair from the paths in `.env`
(`keys/private.pem` and `keys/public.pem` by default). A pair is already checked in for
local development; to generate your own:

```bash
openssl genrsa -out keys/private.pem 2048
openssl rsa -in keys/private.pem -pubout -out keys/public.pem
```

### 3. Configure environment

Create a `.env` file in `backend/` (see the table below). Minimal example:

```dotenv
SECRET_KEY=change-me
AUTH_SALT=change-me
RSA_PRIVATE_KEY_PATH=keys/private.pem
RSA_PUBLIC_KEY_PATH=keys/public.pem
DATABASE_URI=postgresql+asyncpg://user:password@localhost:5432/database_w_ui
ALLOWED_ORIGINS=http://localhost:3000
DEBUG=true

# Bootstrap account, created on startup only when the users table is empty
FIRST_SUPERUSER_USERNAME=admin
FIRST_SUPERUSER_EMAIL=admin@example.com
FIRST_SUPERUSER_PASSWORD=change-me
```

### 4. Run

```bash
uv run python main.py
```

Run `uv run alembic upgrade head` first — Alembic owns the schema, and the app no longer
creates tables. On startup it **verifies** that the database matches the models and refuses
to boot if it is behind (printing what is missing), then seeds the bootstrap superuser if no
users exist. See "Database migrations" in the root README for the workflow and its two
autogenerate caveats.

- Interactive docs: `http://localhost:5000/docs`
- ReDoc: `http://localhost:5000/redoc`

## Configuration reference

All settings are read from `.env` (see `src/core/config.py`).

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | — (required) | App secret. |
| `AUTH_SALT` | — (required) | Password hashing salt/pepper. |
| `RSA_PRIVATE_KEY_PATH` | — (required) | Path to the JWT signing private key. |
| `RSA_PUBLIC_KEY_PATH` | — (required) | Path to the JWT verification public key. |
| `JWT_ALGORITHM` | `RS256` | JWT signing algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access-token lifetime. |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Refresh-token lifetime. |
| `API_PREFIX` | `/api` | Prefix for all routes. |
| `DATABASE_URI` | — (required) | Async SQLAlchemy URL (`postgresql+asyncpg://…`). |
| `ALLOWED_ORIGINS` | `""` | Comma-separated CORS origins. |
| `DEBUG` | `false` | Debug mode. |
| `DISABLE_AUTH` | `false` | Dev-only: bypass auth and act as the first user. |
| `FIRST_SUPERUSER_USERNAME` | `admin` | Bootstrap user username. |
| `FIRST_SUPERUSER_EMAIL` | `admin@example.com` | Bootstrap user email. |
| `FIRST_SUPERUSER_PASSWORD` | `changeme` | Bootstrap user password. |
| `PURGE_DELETED_AFTER_DAYS` | `30` | How long a deleted record is recoverable before the purge job may destroy it. |

## API overview

All routes are mounted under `API_PREFIX` (`/api`) and, except login, require a bearer
token. Obtain one from `POST /api/auth/tokens/login`.

| Area | Endpoints |
|------|-----------|
| Auth | `POST /api/auth/tokens/login`, refresh; `…/users`, `…/groups` CRUD; `PATCH …/users/me/settings` (UI preferences, merged onto the stored blob) |
| Workspaces | `GET/POST /api/workspaces`, `GET/PATCH/DELETE /api/workspaces/{workspace_uuid}` |
| Lists | `GET/POST /api/lists`, `GET/PATCH/DELETE /api/lists/{list_uuid}` |
| Columns | `…/lists/{list_uuid}/columns` CRUD; `…/columns/{uuid}/options` (a lookup's selectable rows, searched server-side) |
| Records | `…/lists/{list_uuid}/items` CRUD |
| Views | `…/lists/{list_uuid}/views` CRUD |
| Forms | `…/lists/{list_uuid}/forms` CRUD |
| Metrics | `…/lists/{list_uuid}/metrics` CRUD; `…/metrics/values?view_uuid=&q=` (every metric's value over the records the caller can see through that view) |

Creating a list requires a `workspace_uuid` (owned by the caller) in the body; the list
collection can be filtered with `?workspace_uuid=…`. Child resources are scoped under a
list; the `get_list_or_404` dependency resolves the list through its workspace and enforces
ownership, so an unowned list (and all its children) returns `404`.

### Identifiers: `name` vs `display_name`

Lists, columns, views, forms and metrics each require **both**:

- `name` — code-facing slug, `^[a-z][a-z0-9_]*$`, unique per scope, editable.
- `display_name` — free-text UI label.

```jsonc
POST /api/lists/{list_uuid}/columns
{
  "name": "due_date",          // slug; 422 if missing or not a valid slug
  "display_name": "Due Date",  // shown in the UI
  "type": "date",
  "config": { "required": true }
}
```

**A name works in the URL wherever a child's uuid does.** These address the same column:

```
GET /api/lists/{list_uuid}/columns/019f52df-…
GET /api/lists/{list_uuid}/columns/due_date
```

…and likewise for views, forms, and the view an item query applies
(`/items/?view_uuid=open_only`). A slug can never parse as a uuid, so the two are
unambiguous; one resolver — `src/lists/refs.py::get_by_ref` — tries the uuid and falls back
to the name, matching **live rows only** (the unique index on `name` is partial, so a
deleted `notes` column may sit beside a live one).

Two caveats:

- **The uuid stays canonical.** Responses carry it, the frontend holds it, and renaming
  moves the name-URL out from under anyone using it. Store uuids; type names.
- **Lists themselves are uuid-only.** A list's name is unique per *workspace*, which
  `/lists/{x}` does not carry, and a guessable identifier there would turn the 403-vs-404
  answer into an enumeration oracle. (A view or form can be restricted more tightly than
  its list, so those two routes answer **404, not 403**, to a caller who cannot see them.)

## Maintenance jobs

All are chunked, commit per batch, and are safe to kill and re-run. Run them from cron or a
systemd timer. All take `--dry-run`, which reports without changing anything.

```bash
uv run python -m src.maintenance.reconcile_shapes            # value shapes -> column config
uv run python -m src.maintenance.purge_deleted_workspaces    # workspaces past the window, and all under them
uv run python -m src.maintenance.purge_deleted_lists         # lists past the window, and all under them
uv run python -m src.maintenance.purge_deleted_items         # empty the bin past the window
uv run python -m src.maintenance.purge_deleted_list_config   # views/forms/metrics binned on their own
uv run python -m src.maintenance.purge_deleted_principals    # finalize deleted accounts
uv run python -m src.maintenance.sweep_orphan_grants         # grants for things that are gone
```

Order matters where they overlap: the workspace and list purges own everything *under* a
deleted parent, so they run before the jobs that sweep individual children, and the grant
sweep runs last because the purges are what orphan grants in the first place.

**`reconcile_shapes`** — toggling a column's `multiple` deliberately leaves its records alone
(on a large list that rewrite is a multi-minute lock inside a web request), so the service
tolerates both shapes and this job converges the stored data afterwards. Nothing depends on it
having run: records in the old shape read, write, export and scrub correctly either way.

**`purge_deleted_items`** — deleting a record only sets `deleted_at`, so it can be recovered
and nothing that pointed at it breaks. This destroys records deleted longer ago than
`PURGE_DELETED_AFTER_DAYS` (default 30; `--days N` overrides, `--days 0` empties the bin now).
It also deletes those records' permission grants, which **nothing else does** — grants point at
their resource with no foreign key, so a hard delete would otherwise leave them orphaned — and
clears the `lookup` cells pointing at them. That clearing is *not* done at delete time: a soft
delete destroys nothing, so it can be undone, and the cells render as "(deleted)" meanwhile.

**`purge_deleted_list_config`** — the same, for a list's views, forms and metrics, which have
their own bin at `/lists/{uuid}/bin/{kind}`. It touches only children of a **live** list: those
binned by a list delete share their list's `deleted_at` and belong to `purge_deleted_lists`, and
leaving them there is what stops a `--days 0` run here from stripping the views off a list still
sitting restorable in its bin. Views and forms can be grant targets, so it deletes their grants;
a metric never is. Columns are not covered — hard-deleting one has to strip its key from every
record's `data`, which is a decision about data rather than config.

**`purge_deleted_principals`** — the same, for users and groups past the window: clears the
`user`/`group` cells naming them and deletes their grants. It does **not** delete the rows. A
user owns workspaces (a real foreign key), so destroying the row means answering "who owns their
data now?" — a question with no safe default and no UI to ask it. The soft-deleted row is inert
and keeps every foreign key honest; proper account purging needs an ownership-transfer story
first.

**`sweep_orphan_grants`** — grants point at their resource with no foreign key (`resource_id`
means a different table per `resource_type`), so nothing cascades and they outlive the lists,
views and accounts they were about. This deletes the ones that can never apply again: resource
or principal gone, or in the bin past the window. A grant on something still *inside* the window
is left alone — that resource can still be restored, and its sharing should come back with it.

## Development

```bash
uv run pytest          # run the test suite (pytest + pytest-asyncio)
```

The tests run against a real PostgreSQL database (the models depend on JSONB, GIN and
partial indexes, and advisory locks, so SQLite isn't an option). They connect to the same
server as `DATABASE_URI` and auto-create a `<db>_test` database (override with the
`TEST_DB_NAME` env var); the whole suite skips cleanly if Postgres isn't reachable. Each
test gets a freshly created/dropped schema, and requests run with auth disabled as a
single seeded user. `tests/test_unique_concurrency.py` verifies the per-list advisory lock
by asserting a concurrent record `create` blocks while the lock is held.

Model registration: a new SQLAlchemy model must be added to
`src/infrastructure/model_registry.py` — the one import list shared by the app, the tests,
and Alembic's `env.py`. Leave it out and autogenerate will emit a `DROP TABLE` for it.

## Notes & roadmap

The schema deliberately trades database-enforced record constraints for a fully dynamic,
migration-free schema — all record-level validation lives in the service layer. Deferred
items (documented in the design doc): a `list_item_links` junction table for
referential-integrity-backed lookups, sharing/permissions (the workspace boundary is
already in place), rollup columns, and additional column types.
