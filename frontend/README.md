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
  lib/            # api client (fetch + auth/refresh), auth + theme + toast contexts,
                  # query client, hand-written API types, and small shared helpers:
                  # links (safeHref), temporal, slug, people, useMediaQuery, useDebounced
  components/     # app shell: AppLayout, NavDrawer (mobile nav), AppDialog, NameFields,
                  # Breadcrumbs, ThemeSwitcher
  features/
    auth/         # login (the only unauthenticated page)
    workspaces/   # workspaces + the lists shared with you directly
    lists/        # lists of a workspace; a list's records; the List settings dialog
    columns/      # add/edit a column, and the columns panel in List settings
    records/      # the record form, the mobile card layout, renderCell, sortRows
    views/        # the view bar, filter/group/column controls, filter conditions
    forms/        # per-list form layouts
    permissions/  # Share dialog/panel and the effective-actions hooks
    people/       # accounts and groups (identity administration)
    profile/      # your own account and profile
    transfer/     # CSV/JSON import + export
    lookups/      # lookup cells, fields and filter inputs
    principals/   # user/group cells, fields and filter inputs
    references/   # ReferencePicker — the typeahead all of the above share
```

A column's `type` and `config` drive everything about it: `renderCell` picks how it
displays, `RecordFormDialog` picks its input, `FilterConditionsEditor` picks its filter
control. Adding a column type means teaching those three, not writing per-list code.

## Layout on small screens

The breakpoint is **640px**, in one place: `SMALL_SCREEN` in `lib/useMediaQuery.ts`.
Below it:

- **Records become cards** (`features/records/RecordCards.tsx`) instead of a `DataTable`.
  Twelve columns across a phone is twelve slivers; a card is one record, label beside
  value. It is a second *layout*, not a second implementation — both render values with
  `renderCell` and order them with `sortRows`, and they share selection, page size and
  row actions. A value or an order that differed between them is a bug.
- **The header navigation becomes a drawer** (`components/NavDrawer.tsx`): workspaces,
  their lists (fetched lazily, per expanded workspace), the lists shared with you, your
  profile at the top and Log out at the foot.
- **Sorting moves to the toolbar.** Cards have no column header to click, so a "Sort by"
  dropdown drives the same state the grid's headers do.
- **Dialogs go near-fullscreen.** Every dialog is `components/AppDialog.tsx`, which
  applies the breakpoints and caps the body height so its buttons stay reachable —
  import it, not `primereact/dialog`.

Above the breakpoint the grid still has to survive a narrow window: its table carries a
minimum width of ~10rem per column so it *scrolls* rather than crushing its columns.

Run `npm run gen:api` to keep the TypeScript types in sync with the Python models
instead of the hand-written `src/lib/types.ts`.

## Notes

- Auth: access + refresh tokens are stored in `localStorage`; the API client attaches the
  bearer token and refreshes once on a 401. (For a hardening pass, consider moving the
  access token to memory.)
- Tailwind is imported without its preflight reset so it doesn't fight PrimeReact's
  component styles; use it for layout utilities only.
- Colors come from the theme, never from Tailwind: `themes.css` defines every palette
  against `data-theme` / `data-mode` on `<html>`, so reach for `var(--brand)`,
  `var(--text-muted)`, `var(--border)` — a `text-blue-500` will not follow the theme.
- Server state is TanStack Query with **shared keys** (`["items", listUuid]`,
  `["columns", listUuid]`, `["person", "me"]`…). Two components asking the same question
  make one request, and a dialog's `invalidateQueries` refreshes the page behind it.
  Retries and refetch-on-focus are off on purpose — see `lib/queryClient.ts`.
- A `link` cell's URL must go through `safeHref` (`lib/links.ts`) before it reaches an
  `href`. The backend already restricts schemes, but rows predating that check, or
  arriving by import, mean a raw `href={value}` is stored XSS.
- **Recommended (not yet adopted): generate API types instead of hand-writing them.**
  `src/lib/types.ts` is currently a hand-maintained mirror of the backend Pydantic
  schemas, so every backend field change must be copied over by hand — a silent
  drift risk. `npm run gen:api` already exists and generates exact types from the
  backend's OpenAPI schema into `src/lib/api-schema.ts`. When the API surface grows
  or more people touch it, switch `types.ts` to re-export the generated types (level 1),
  and optionally adopt a typed client (e.g. `openapi-fetch`) so endpoints and their
  request/response shapes are checked at compile time (level 2). Trade-off: the
  generated file is more verbose, and `gen:api` must be re-run against a running
  backend after schema changes.
