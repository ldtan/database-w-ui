# Database with UI — Frontend

React + TypeScript SPA for the lists/tables app, talking to the FastAPI backend.

## Stack

| Concern | Choice |
|---------|--------|
| Framework | React 19 + TypeScript |
| Build/dev | Vite |
| UI + data grid | PrimeReact (DataTable, form controls) + PrimeIcons |
| Layout utilities | Tailwind CSS v4 (utilities only, no preflight) |
| Server state | TanStack Query |
| Routing | React Router |
| Forms | React Hook Form + Zod |
| Drag & drop | dnd-kit (for future column/field reordering) |
| API types | `openapi-typescript` (generate from the backend's OpenAPI schema) |

## Getting started

```bash
npm install
npm run dev      # http://localhost:5173
```

The dev server proxies `/api` to the backend at `http://localhost:5000`, so run the
backend (see `../backend/README.md`) alongside it. Log in with the backend's seeded
user (`FIRST_SUPERUSER_USERNAME` / `FIRST_SUPERUSER_PASSWORD`).

Other scripts:

```bash
npm run build    # typecheck (tsc --noEmit) + production build
npm run preview  # serve the production build
npm run gen:api  # regenerate typed API bindings from http://localhost:5000/openapi.json
```

## Structure

```
src/
  lib/            # api client (fetch + auth/refresh), auth context, query client, types, slug helper
  components/     # shared layout
  features/
    auth/         # login
    workspaces/   # workspace list + create
    lists/        # lists in a workspace, and a list's records in a DataTable
```

Each backend `ColumnType` will map to a cell renderer/editor (grid) and a field input
(forms), both driven by the column's `config`. Run `npm run gen:api` to keep the
TypeScript types in sync with the Python models instead of the hand-written
`src/lib/types.ts`.

## Notes

- Auth: access + refresh tokens are stored in `localStorage`; the API client attaches the
  bearer token and refreshes once on a 401. (For a hardening pass, consider moving the
  access token to memory.)
- Tailwind is imported without its preflight reset so it doesn't fight PrimeReact's
  component styles; use it for layout utilities only.
