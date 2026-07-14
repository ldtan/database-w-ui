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

## Deployment (Render)

[`render.yaml`](render.yaml) is the whole deployment: **New → Blueprint** in Render reads it
and creates the API (a plain Python web service), the SPA (a free static site), a free
Postgres, and the environment group they share. It is written for the **free tier**; every
paid upgrade is marked `PAID:` in the file and is a one-line edit.

**No container.** The app needs Python 3.14 — every entity's public id comes from `uuid.uuid7`
(`src/core/mixins.py`) — and Render's native runtime installs any released version, so
`backend/.python-version` is enough. A Dockerfile would only add build time.

**The free database expires, and the app goes down with it.** Render *deletes* a free Postgres
about 30 days after it is created. It is not emptied — it is gone, so `DATABASE_URI` names a
host that no longer exists, `alembic upgrade head` fails in the start command, and the service
never reaches uvicorn. **Down, not blank.** Recovery is manual and takes minutes: create a new
database, paste its internal URL into `DATABASE_URI`, redeploy — the start command migrates it
from empty and the superuser is seeded again. Accept this only for a deployment you are willing
to rebuild; `plan: basic-256mb` (~$7/mo) removes the expiry and adds backups, and is the
**first** thing to buy when the data starts mattering — before the web service.

**`DATABASE_URI` is set by hand, not by `fromDatabase`.** Render emits a plain `postgresql://`
URL, and SQLAlchemy picks its driver from that scheme — so the app would reach for psycopg2,
which is neither installed nor async. Take the database's **internal** connection string and
rewrite the scheme to `postgresql+asyncpg://`. The internal network needs no SSL parameter; if
you use the external URL (the maintenance workflow must), the parameter is **`ssl=require`**,
never `sslmode=require` — asyncpg is not libpq, and raises an unexpected-keyword `TypeError`
from a URL that looks perfectly correct.

**The SPA and the API share an origin, by rewrite.** `frontend/src/lib/api.ts` calls a relative
`/api/...` and has no configurable base URL, so the static site rewrites `/api/*` to the API
service (and `/*` to `/index.html`, because React Router owns the paths). CORS therefore never
enters the picture. If the rewrite ever has to go, the fallback is a `VITE_API_BASE` env var in
the client plus `ALLOWED_ORIGINS` set to the site's URL.

**The RSA keys are uploaded as Render Secret Files**, not generated per deploy: RS256 signs
every token with them, so a fresh pair silently logs out everyone holding one. `AUTH_SALT` is
likewise permanent — it is mixed into every password hash. And `FIRST_SUPERUSER_PASSWORD` seeds
a real account on first boot: on a public URL, `changeme` is a public login.

**The migration runs in the start command**, which is the one compromise here. It belongs in a
pre-deploy hook, because at boot every instance races every other instance to run it — but the
free plan has no such hook and runs exactly one instance, so there is no race to lose. It is
the first thing to move when the service is paid for or scaled; `render.yaml` says so at the
point of change.

**The four maintenance jobs run in GitHub Actions**
([`.github/workflows/maintenance.yml`](.github/workflows/maintenance.yml)), because Render's
Cron Job is a paid service type. They are idempotent housekeeping, so a missed night costs
nothing — which is what makes a free scheduler an honest home for them. The workflow needs one
secret, `DATABASE_URI`, and it must be the **external** connection string with `?ssl=require`:
GitHub cannot route to Render's internal network. That is the one place the API's own URL will
not work.

Expect a cold start of up to a minute on the first request after a quiet spell: Render spins a
free web service down after ~15 minutes idle.

## Documentation

- [Data model design](docs/data-model-design.md) — the five models (lists, columns,
  list_items, views, forms), the JSONB record strategy, column types, and the decisions
  behind them.
- [Known gaps, gotchas, and follow-ups](docs/known-gaps.md) — what is deliberately
  unfinished, behavior that surprises people, and the traps waiting for whoever edits the
  code next. **Read before touching permissions, theming, or the schema.**
