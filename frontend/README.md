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
| QR scanning | html5-qrcode (camera, with an image fallback) |
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
npm run gen:api  # regenerate src/lib/api-schema.ts from the backend's OpenAPI document
```

## API types are generated

`src/lib/types.ts` re-exports from `src/lib/api-schema.ts`, which `npm run gen:api`
generates from the backend's own OpenAPI document — so the frontend's idea of a
`ColumnRead` *is* the Pydantic model. **Run it after any backend schema change**; whatever
the frontend now gets wrong stops compiling instead of failing at runtime.

It needs neither a running server nor a database: the script asks the app for its schema
directly (`src.infrastructure.export_openapi`), which is why refreshing types is one
command and not a ceremony.

Two things stay hand-written, on purpose:

- **The `config` blobs.** A column's/view's/form's `config` is a `dict` to the backend —
  its shape depends on the column type — so OpenAPI can only say "object". `ColumnConfig`,
  `ViewConfig` and `FormConfig` in `types.ts` say what the UI actually reads, and are the
  one thing here that can still drift.
- **`Response<T>`.** OpenAPI marks a field optional when its model gives it a default, but
  FastAPI serializes response models in full, so those fields are always present. The
  helper makes them required rather than littering the app with `?? false`.

## Structure

```
src/
  lib/            # api client (fetch + auth/refresh), auth + theme + toast contexts,
                  # themeSync (ties the theme to the account), query client, hand-written
                  # API types, and small shared helpers: links (safeHref), temporal, slug,
                  # people, useMediaQuery, useDebounced
  components/     # app shell: AppLayout, NavDrawer (mobile nav), AccountMenu + UserAvatar
                  # (the header's account control), AppDialog, NameFields, Breadcrumbs,
                  # ThemeSwitcher
  features/
    auth/         # login (the only unauthenticated page)
    workspaces/   # workspaces + the lists shared with you directly
    lists/        # lists of a workspace; a list's records; the List settings dialog
    columns/      # add/edit a column, and the columns panel in List settings
    records/      # the record form, the mobile card layout, renderCell, sortRows,
                  # and the QR scanner (QrScanDialog + qrFill)
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

## Adding a record from a QR code

"Add record" is a `SplitButton`: the button adds a record by hand, and its menu offers
**Fill using QR code** and **Submit using QR code**. Both read one QR code holding a JSON
object, keyed by **column name** — the same key a CSV/JSON import uses, so one convention
covers every way a record arrives from outside the app, and a code stays writable by a
person (a column uuid works as a key too, for a code a machine generated):

```json
{ "full_name": "Ana Cruz", "age": 30, "status": "Active", "owner": "admin" }
```

- **Fill** opens the Add-record form with those values in it, for the user to check.
- **Submit** posts the record immediately, *invalid values and all*. A rejection is not a
  dead end: the values land in the form with the server's own message above them, so the
  scan gets fixed rather than discarded.

**`qrFill.ts` does not validate, deliberately.** The backend validates every value on write
(`src/lists/columns/validation.py`) and is the only thing that does — restating "what a
link's scheme may be" or "what this choice column's options are" in the browser would be
two rulebooks to keep in step, and the frontend's is the one that would silently fall
behind. A bad value is therefore submitted and comes back as the server's error, which is
better worded than anything the client would have invented.

What `qrFill` *does* do is translate JSON's types into the form's, which is a shape and not
a rule: `"30"` into a number for the `InputNumber`, `"yes"` into a boolean for the
`Checkbox`, an ISO string into the `Date` a `Calendar` holds (via the same `deserialize`
the form uses for a stored value), and a person's name into the uuid a reference cell
stores, so its picker can render it. The first two mirror the backend's *importer*
(`_parse_cell`), which is where a QR's laxness comes from; the last is only for display,
since the item service resolves reference names on write by itself. **Every translation
falls back to the raw value** rather than dropping it — an unresolvable name is still
submitted, because the server resolves names too and will say precisely why it cannot.

The one thing a code is refused is a field this form does not have: it cannot reach a
column the **create form** excludes, or write one it marks read-only. That is a fact about
the form, not a judgement about a value.

A **choice** column is written by the option itself — the same label the UI shows
("In progress") — since a choice cell stores the label and the backend validates by
exact membership. A QR can therefore be authored straight off the screen.

Scanning uses `html5-qrcode`, with **an image fallback that is not a nicety**:
`getUserMedia` needs a camera and a secure context (HTTPS, or localhost), so on a desktop
— or on a LAN deployment served over plain HTTP — the camera path does not exist, and a
scanner that only scanned would be a dead button. Both paths end in the same place: the
code's text, handed to `qrFill`.

`QrScanDialog` is **lazy-loaded** (`React.lazy` + `Suspense` in `ListDetailPage`): the
decoder is ~110 kB gzipped, a quarter of the whole bundle, and most sessions never open
the scanner. Import it that way, not directly. `qrFill` is small and pure, and stays in
the main bundle.

## Layout on small screens

The breakpoint is **640px**, in one place: `SMALL_SCREEN` in `lib/useMediaQuery.ts`.
Below it:

- **Records become cards** (`features/records/RecordCards.tsx`) instead of a `DataTable`.
  Twelve columns across a phone is twelve slivers; a card is one record, label beside
  value. It is a second *layout*, not a second implementation — both render values with
  `renderCell` and order them with `sortRows`, and they share selection, page size and
  row actions. A value or an order that differed between them is a bug.
- **The header navigation becomes a drawer** (`components/NavDrawer.tsx`): your account
  at the top — profile, then Log out — followed by workspaces, their lists (fetched
  lazily, per expanded workspace), and the lists shared with you. The header's account
  avatar (`components/AccountMenu.tsx`) is dropped below the breakpoint rather than
  duplicated: the drawer already carries that band.
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
- The theme is stored **against the account** (`users.settings`), so it follows the user
  between devices. `localStorage` is still written, but only as this device's cache: the
  inline script in `index.html` needs something synchronous to read before first paint,
  and a fetched value cannot arrive that early. `lib/themeSync.tsx` reconciles the two —
  the account's copy is applied once on sign-in, and every later change here is pushed
  up. It lives outside `ThemeProvider` because that provider wraps `AuthProvider` and so
  cannot ask who is signed in.
- Server state is TanStack Query with **shared keys** (`["items", listUuid]`,
  `["columns", listUuid]`, `["person", "me"]`…). Two components asking the same question
  make one request, and a dialog's `invalidateQueries` refreshes the page behind it.
  Retries and refetch-on-focus are off on purpose — see `lib/queryClient.ts`.
- A `link` cell's URL must go through `safeHref` (`lib/links.ts`) before it reaches an
  `href`. The backend already restricts schemes, but rows predating that check, or
  arriving by import, mean a raw `href={value}` is stored XSS.
- **Next step for type safety: a typed client.** The *schemas* are now generated (above),
  but the *endpoints* are not: `api.get<ListItem[]>(\`/lists/${uuid}/items/\`)` is a string
  and a type argument the caller supplies, so a wrong path or a stale response type still
  compiles. Adopting `openapi-fetch` over `api-schema.ts` would check the path, its
  parameters and its response against the OpenAPI document at compile time. It touches
  every call site, which is why it has not been done yet.
