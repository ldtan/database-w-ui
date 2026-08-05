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
    │   │   └── batch/       # many accounts from one uploaded CSV
    │   └── tokens/          # login / refresh tokens
    ├── workspaces/          # tenancy layer: a user owns workspaces; lists live in them
    ├── demo/                # demo scenarios: a whole plausible tenant, seeded
    └── lists/               # the lists feature (aggregate root = List)
        ├── models.py schemas.py services.py routers.py dependencies.py
        ├── columns/         # Column + ColumnType enum
        ├── items/           # ListItem (record data + validation)
        ├── views/           # View
        ├── forms/           # Form + FormType enum
        └── metrics/       # Metric + the aggregate formula language (+ its SQL pushdown)
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

Both, and the `/openapi.json` they read, are served only while `DEBUG` is true — so they
are there locally and absent from a deployment, where they would publish the whole route
inventory to anyone with the URL. A deployed `/docs` answers `404`, not `401`: the route is
not registered at all. Refreshing the frontend's types does not go through them (see
"API types are generated" in `../frontend/README.md`), so nothing needs them to be reachable.

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
| `DEBUG` | `false` | Debug mode. Also the switch for `/docs`, `/redoc` and `/openapi.json`, which are served only when it is true, and the flag `DISABLE_AUTH` must be paired with. |
| `DISABLE_AUTH` | `false` | Dev-only: bypass auth and act as the first user. Requires `DEBUG=true` — the app refuses to start otherwise, because on a reachable URL it hands every route to anyone, as the superuser, with no token. |
| `LOGIN_THROTTLE_WINDOW_MINUTES` | `15` | How long a failed sign-in is counted for. |
| `LOGIN_MAX_FAILURES_PER_USERNAME` | `10` | Failed sign-ins per username per window before login answers `429`. A success clears the tally. |
| `LOGIN_MAX_FAILURES_PER_IP` | `50` | The same per source address, to catch one host spraying across many usernames. `0` disables it — set that if a proxy in front of the API makes every user arrive as one address. |
| `FIRST_SUPERUSER_USERNAME` | `admin` | Bootstrap user username. |
| `FIRST_SUPERUSER_EMAIL` | `admin@example.com` | Bootstrap user email. |
| `FIRST_SUPERUSER_PASSWORD` | `changeme` | Bootstrap user password. |
| `PURGE_DELETED_AFTER_DAYS` | `30` | How long a deleted record is recoverable before the purge job may destroy it. |

## API overview

All routes are mounted under `API_PREFIX` (`/api`) and, except login, require a bearer
token. Obtain one from `POST /api/auth/tokens/login`.

The API is public by design and protected by that token, not by its network position: the
SPA runs in a browser, so anything it can call, any HTTP client can call. `ALLOWED_ORIGINS`
is not an access control — CORS only governs what other *browser pages* may read, and is
ignored entirely by scripts. The unauthenticated surface is exactly `/health` and the three
routes under `…/auth/tokens`; everything else is `401` without a token. `/docs`, `/redoc`
and `/openapi.json` join that list when `DEBUG` is true, which is a development machine and
not a deployment.

Login is therefore the one route an attacker can reach, and it is rate-limited on failures —
per username, and per source address — returning `429` with a `Retry-After` header once a
budget is spent. See `src/auth/tokens/throttle.py` for the thresholds, the reasoning, and
the single-instance assumption the counters rest on.

| Area | Endpoints |
|------|-----------|
| Auth | `POST /api/auth/tokens/login`, refresh; `…/users`, `…/groups` CRUD; `PATCH …/users/me/settings` (UI preferences, merged onto the stored blob); `POST …/users/import` (many accounts from one CSV, superuser-only — see below) |
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

### Batch account creation (`POST /api/auth/users/import`)

Superuser-only, `multipart/form-data` with one `file` field. A closed set of twelve CSV
headers: `username`, `email`, `password`, `is_active`, `is_superuser`, `groups`, plus the
six person fields (`name_prefix`, `first_name`, `middle_name`, `last_name`, `name_suffix`,
`birth_date`). `;` separates group names and `true/false/1/0/yes/no` are the booleans —
both borrowed from list import so there is one file convention, not two.

**Two kinds of failure, kept apart.** A bad *file* — an unrecognised header, a missing
`username`/`email` header, over 1000 rows, not UTF-8 — is a `400`, and nothing was created.
A bad *row* comes back inside the summary as `{row, message}` while the rows around it
commit, exactly as list import behaves.

Unknown headers are **rejected** here, where list import ignores them. That inversion is
deliberate: a list's columns are user-defined, so a header it lacks may be another list's
column; this set is fixed, so `frist_name` is a file that would silently leave every name
blank.

Each row is created through `UserService.create` and `PersonService.create` rather than by
inlining the inserts, so a batch-created account is indistinguishable from a form-created
one — same hashing, same conflict message, same auto-provisioned `personal` workspace.
Those services commit internally, which is why every row is already its own transaction and
there is no savepoint machinery here.

A blank `password` cell mints one (`secrets.token_urlsafe`) and returns it in the response
as `generated_password`. **That response body is the only place it ever exists** — nothing
persists it and there is no way to ask again, so it must not be logged. The frontend turns
those into a one-time credentials CSV; a lost one is a password reset.

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

**URLs take a uuid, never a name.** A column, view, form or metric is addressed one way:

```
GET /api/lists/{list_uuid}/columns/019f52df-…
```

…and likewise for the view an item query applies (`/items/?view_uuid=…`). A name used to
be accepted here as well. It was withdrawn because names and the literal segments the
routes need compete for the same slot, and they had already collided: `/metrics/values`
is declared ahead of `/{uuid}`, so a metric called `values` was creatable and permanently
unreachable by name. `/views/deleted` would have been the second case. Reserving words as
they accrue is a validation rule people trip over, bought for a convenience the UI never
used — it has always sent uuids.

Two things that did not change:

- **Names are still the vocabulary off the URL.** A metric formula names its columns, and
  a `lookup`/`user`/`group` cell may be *written* as a name and is stored as a uuid.
- **Views and forms answer 404, not 403**, to a caller who cannot see them — they can be
  restricted more tightly than their list, and `list_views`/`list_forms` already omit what
  the caller cannot see, so one route calling a view absent while another calls it
  forbidden would help nobody.

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

**`purge_deleted_workspaces` / `purge_deleted_lists`** — destroy a parent past the window along
with everything under it: records, columns, views, forms, metrics, and the grants of each, since
nothing cascades to those. Both are chunked twice. Batching by list bounds nothing on its own —
a list holds a handful of views and however many records somebody put in it — so the records are
drained in batches of their own (`src/lists/purge.py`), each clearing its own item grants before
the rows go, because the subquery that finds them stops matching once the first batch is gone.

That inner loop is shared with the `permanent` routes, which pass `commit=False`. The difference
is not a preference: a job works on rows past the window, which the bin already refuses to hand
back, so a half-drained list is unreachable and the next run finishes it. The routes work while
the parent is still restorable, where committing per batch would let an interrupted purge return
a list short of records with nothing to say so — so they take the bounded statements and keep the
one transaction.

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

The clearing is one pass per referencing column rather than one per purged row — the difference
between a handful of statements and millions — but each pass works through its records in
batches too, since a single column's stale references can span a whole list.

**`purge_deleted_list_config`** — the same, for a list's views, forms, metrics and columns,
which have a bin on their own collections (`/lists/{uuid}/views/deleted` and so on). It touches
only children of a **live** list: those binned by a list delete share their list's `deleted_at`
and belong to `purge_deleted_lists`, and leaving them there is what stops a `--days 0` run here
from stripping the views off a list still sitting restorable in its bin. Views and forms can be
grant targets, so it deletes their grants; metrics and columns never are.

A column also takes its record data with it. Its values live under its uuid in every record's
`data` and the delete never touched them, so each batch strips those keys before dropping the
rows — grouped by list and removing an array of keys, since two doomed columns of one list in
the same batch would otherwise leave one key behind. This is the only thing that ever frees
that data: once the column row is gone, nothing names those keys again.

That strip is chunked on its own (`src/lists/purge.py` again, shared with the column bin's
permanent delete), and the batch of columns does not bound it: one column's values are spread
over as many records as its list holds. Rewriting a row is heavier than deleting one, so it
matters here at least as much as it does for the list purges.

**`purge_deleted_principals`** — the same, for users and groups past the window: clears the
`user`/`group` cells naming them and deletes their grants. It does **not** delete the rows, and
still deliberately: it runs unattended, and a user owns workspaces through a real foreign key, so
destroying the row means answering "who owns their data now?" — which needs a person. The
soft-deleted row is inert and keeps every foreign key honest.

Destroying one by hand is possible now, through the principal bin (`src/auth/users/bin.py`):
`…/permanent` on `/auth/users` and `/auth/groups`, superuser-only, refusing while any workspace
still names the account. Every account owns a personal workspace from creation, so in practice
that means dealing with its workspaces first — which is the point.

**`sweep_orphan_grants`** — grants point at their resource with no foreign key (`resource_id`
means a different table per `resource_type`), so nothing cascades and they outlive the lists,
views and accounts they were about. This deletes the ones that can never apply again: resource
or principal gone, or in the bin past the window. A grant on something still *inside* the window
is left alone — that resource can still be restored, and its sharing should come back with it.
Chunked like the purges: there is a grant per share, so a busy list's worth is not a small
number, and the predicate that finds them is a `NOT EXISTS` against five tables.

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

### Demo data

An empty database is a poor place to look at any of this. `src/demo/` seeds a whole
plausible tenant — a school — through the real services, so everything it writes is
validated exactly as the API validates it:

```bash
uv run python -m src.demo.school                      # ~740 records, a few seconds
uv run python -m src.demo.school --password letmein   # every seeded account shares one
```

It builds 16 accounts with person profiles, 6 groups, 5 workspaces, 17 lists using every
`ColumnType`, and the views, forms, metrics and permission grants over them — including
the states a tenant only reaches by being *used*: records and a column and a list in their
bins, and a list transferred into another user's "Incoming" workspace.

**Idempotent**: each resource is matched on its natural handle (a username, a `name` slug
within its scope) and created only when missing, so a re-run fills the gaps left by one
that died halfway and changes nothing else. Records are the exception — a record has no
natural key — so a list that already holds records is left alone rather than doubled.

Point it at a scratch database rather than your working one if you want it on its own:

```bash
DATABASE_URI='postgresql+asyncpg://…/mydb_demo' uv run alembic upgrade head
DATABASE_URI='postgresql+asyncpg://…/mydb_demo' uv run python -m src.demo.school
```

The scenario's own docstring is the place to read *why* it is shaped as it is — in
particular why one user owns four of the five workspaces, which is a constraint the app
imposes on lookups rather than a story about the school.

There is a second, much smaller scenario:

```bash
uv run python -m src.demo.guide                       # ~40 records, two accounts
```

`src/demo/guide.py` builds the *Sales* workspace and *Contacts* list that the frontend's
guide page walks a reader through, because that page's screenshots are photographs of it
(`npm run capture:guide` in `frontend/`). Its nouns are load-bearing: the capture script
clicks "Contacts" and "Customers only" by name, so renaming something here breaks that run.
Both scenarios can live in one database — they share no names.

## Notes & roadmap

The schema deliberately trades database-enforced record constraints for a fully dynamic,
migration-free schema — all record-level validation lives in the service layer. Deferred
items (documented in the design doc): a `list_item_links` junction table for
referential-integrity-backed lookups, sharing/permissions (the workspace boundary is
already in place), rollup columns, and additional column types.
