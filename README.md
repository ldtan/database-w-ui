# Database with UI

A self-hostable app for building flexible databases through a UI: create **lists**
(tables), add typed **columns**, store **records**, and configure **views**, **forms** and
**metrics** (totals, averages and counts) over each list — a minimal, self-hosted take on
Airtable/NocoDB.

A list's schema is fully dynamic — records are stored as `JSONB` keyed by column UUID, so
adding, renaming, or removing a column never requires a database migration.

## Repository layout

| Directory | Description |
|-----------|-------------|
| [`backend/`](backend/) | FastAPI + SQLAlchemy (async) + PostgreSQL API. See its [README](backend/README.md) to run it. |
| [`frontend/`](frontend/) | React + TypeScript + PrimeReact SPA. See its [README](frontend/README.md) to run it. |
| [`docs/`](docs/) | Design docs, including the [data-model specification](docs/data-model-design.md). |

## Getting started

Run the backend and frontend together:

```bash
# backend — http://localhost:5000 (docs at /docs)
cd backend
uv sync
uv run alembic upgrade head    # the app does not create tables; it verifies them
uv run python main.py

# frontend — http://localhost:5173 (proxies /api to the backend)
cd frontend
npm install
npm run dev
```

See [`backend/README.md`](backend/README.md) and [`frontend/README.md`](frontend/README.md)
for prerequisites, environment configuration, and the full API/UI overview.

## Database migrations

Alembic owns the schema. The app **verifies** it on startup and refuses to boot if the
database is behind, printing what is missing — it never creates or alters anything itself
(that would race across workers). Deploys run `alembic upgrade head` before starting.

After changing a model:

```bash
cd backend
uv run alembic revision --autogenerate -m "what changed"
#  ^ then READ the generated script before applying it (see the caveats below)
uv run alembic upgrade head
```

Two things autogenerate gets wrong, both of which have already bitten this project:

- **It cannot see new enum values.** Adding a member to `ColumnType` produces a migration
  whose `upgrade()` is `pass`, and the app then 500s with
  `invalid input value for enum column_type: "..."`. Write it by hand:

  ```python
  with op.get_context().autocommit_block():   # ADD VALUE cannot run in a transaction
      op.execute("ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'time'")
  ```

- **It omits `use_alter` foreign keys** (the circular `lists.primary_column_id → columns.id`),
  so they must be added with an explicit `op.create_foreign_key`.

Add a new model? Register it in `src/infrastructure/model_registry.py` — the single import
list shared by the app, the tests, and Alembic. A model missing from it makes autogenerate
emit a `DROP TABLE` for its table.

`tests/test_migrations.py` builds a database *through the migrations* and asserts it matches
the models, so a missing or wrong revision fails CI rather than production.

## Deployment

Each target gets a directory under [`deploy/`](deploy/), holding its config *and* the runbook
for it — so a second platform is a new folder, not an edit to this file.

| Target | Config | Notes |
|--------|--------|-------|
| [Render](deploy/render/) | [`deploy/render/render.yaml`](deploy/render/render.yaml) | Free tier: Python web service + static site + free Postgres. **The free database is deleted after ~30 days**, and the app goes down with it. |

Two facts that hold whatever the target is, because they come from the app rather than the
host:

- **`alembic upgrade head` must run before the app starts**, as its own step where the platform
  offers one. The app refuses to boot against a database that is behind (it verifies, never
  migrates — that would race across workers), so a deploy that skips this fails loudly instead
  of half-working.
- **The database URL must say `postgresql+asyncpg://`**, and any SSL parameter is `ssl=…`, not
  `sslmode=…`. SQLAlchemy picks its driver from the scheme, and asyncpg is not libpq — get
  either wrong and the app dies at connect time with an error that looks unrelated to the URL.

## Documentation

- [Data model design](docs/data-model-design.md) — the six models (lists, columns,
  list_items, views, forms, metrics), the JSONB record strategy, column types, and the
  decisions behind them.
- [Known gaps, gotchas, and follow-ups](docs/known-gaps.md) — what is deliberately
  unfinished, behavior that surprises people, and the traps waiting for whoever edits the
  code next. **Read before touching permissions, theming, the schema, or any control the
  guide and the in-app tours describe by name.**
- [Planned features](docs/planned-features.md) — nine asked-for capabilities: list
  transfer, cross-workspace moves, cloning, nested and cross-workspace lookups, live
  records, metrics, and column type conversion (including duplicating a column) are
  built; copying into an *already existing* column is not. What exists to build on, the
  load-bearing details, and the decisions still open.
