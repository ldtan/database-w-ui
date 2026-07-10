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
| `frontend/` | UI (not yet implemented). |
| [`docs/`](docs/) | Design docs, including the [data-model specification](docs/data-model-design.md). |

## Getting started

The backend is the runnable part today:

```bash
cd backend
uv sync
uv run python main.py   # serves on http://localhost:5000 (docs at /docs)
```

See [`backend/README.md`](backend/README.md) for prerequisites, environment configuration,
and the full API overview.

## Documentation

- [Data model design](docs/data-model-design.md) — the five models (lists, columns,
  list_items, views, forms), the JSONB record strategy, column types, and the decisions
  behind them.
