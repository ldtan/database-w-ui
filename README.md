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
uv run python main.py

# frontend — http://localhost:5173 (proxies /api to the backend)
cd frontend
npm install
npm run dev
```

See [`backend/README.md`](backend/README.md) and [`frontend/README.md`](frontend/README.md)
for prerequisites, environment configuration, and the full API/UI overview.

## Documentation

- [Data model design](docs/data-model-design.md) — the five models (lists, columns,
  list_items, views, forms), the JSONB record strategy, column types, and the decisions
  behind them.
- [Known gaps, gotchas, and follow-ups](docs/known-gaps.md) — what is deliberately
  unfinished, behavior that surprises people, and the traps waiting for whoever edits the
  code next. **Read before touching permissions, theming, or the schema.**
