# Database with UI

A self-hostable app for building flexible databases through a UI: create **lists**
(tables), add typed **columns**, store **records**, and configure **views** and **forms**
over each list — a minimal, self-hosted take on Airtable/NocoDB.

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

## Documentation

- [Data model design](docs/data-model-design.md) — the five models (lists, columns,
  list_items, views, forms), the JSONB record strategy, column types, and the decisions
  behind them.
- [Known gaps, gotchas, and follow-ups](docs/known-gaps.md) — what is deliberately
  unfinished, behavior that surprises people, and the traps waiting for whoever edits the
  code next. **Read before touching permissions, theming, or the schema.**
