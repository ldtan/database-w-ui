# Known gaps, gotchas, and follow-ups

Things that are deliberately unfinished, behave in a way that surprises people, or will
bite whoever touches the code next. Ordered by how much they matter.

---

## 1. Export bypasses item-level permissions (data leak)

**Verified.** `TransferService.export_rows` selects records with only
`ListItem.deleted_at IS NULL AND ListItem.list_id == list_id` — it never applies
`PermissionService.item_view_filter`, which every other read path does.

So a user with `view` on a list exports **every row in it**, including records whose
inheritance was broken (`permissions_inherit = false`) and which were never granted to
them. Reproduced against the dev DB: the grid correctly showed such a user 1 of 2 rows,
while `POST /lists/{uuid}/export` handed them both.

Anything that relies on per-record permissions is only as strong as its weakest read
path, and export is currently that path. **Fix:** apply `item_view_filter` in
`export_rows` the way `ListItemService.list`/`query` do. Small change; the filter already
exists.

## 2. User and group management has no role gating

`/api/auth/users` and `/api/auth/groups` are protected by `get_current_user` and nothing
else. **Any authenticated user can create, rename, and delete users and groups** —
including adding themselves to a group that holds grants, which is a privilege-escalation
path into other people's data.

There is no role/superuser concept in the model yet; `FIRST_SUPERUSER_*` only seeds the
bootstrap account. Adding one is the prerequisite for gating these routes and the
People & Groups page.

## 3. A record shared without its list can't be viewed in the UI

The API supports this on purpose — item routes use `resolve_list_or_404` (existence only)
rather than `get_list_or_404` (which requires list `view`). Verified: a user with `edit`
on one record and *no* grant on the list gets 200 on that record and sees only it in the
list's records.

The UI can't render it. `ListDetailPage` is the only place a record appears, and on mount
it fetches `/lists/{uuid}` and `/lists/{uuid}/columns/`, both gated on list `view`. Both
403 and the page blanks.

The blocker is the **column schema**: records are JSONB keyed by column uuid, so a record
is unrenderable without the column definitions — and that endpoint requires exactly the
permission an item-only grantee lacks. Closing this needs a backend change either way:
let an item grant confer read access to the list's column schema (field names and types,
not its records). That is a deliberate widening, not a no-op.

On top of that, pick one:
- **Degrade `ListDetailPage`** when `/lists/{uuid}` 403s (smaller, one page to maintain).
- **A dedicated record page/permalink** (cleaner separation, a second page to maintain).

Both also need two fallbacks that are easy to miss: `RecordFormDialog` renders from a Form
(`/lists/{uuid}/forms/` is *also* gated on list `view`), and `LookupCell` resolves a value
by fetching a **different** list — so lookups must degrade to an unresolved value when the
target list isn't visible.

## 4. Grants are not scrubbed on hard delete

`permission_grants` points at resources polymorphically via `(resource_type, resource_id)`
with **no foreign key**, so nothing cleans up a grant when its resource goes away. This is
harmless today because resources only ever soft-delete and the resolver walks the live
hierarchy (a deleted resource resolves to no context, so its grants are inert). It becomes
a real problem the moment anything hard-deletes, or if ids are ever reused.

---

## Behavior that surprises people

**The workspace owner is an implicit superuser.** `effective_actions` short-circuits to all
actions when `user.id == workspace.owner_id`, before any grant lookup. Owners cannot lock
themselves out of their own data no matter how the grants are arranged, and no grant can
*reduce* an owner's access. This is also why the whole pre-existing test suite stayed green
when permissions landed: `DISABLE_AUTH=True` resolves the caller to the seeded owner.

**"Unique" permissions are opt-out, not opt-in.** A record leaves the list's permission
umbrella only when someone flips its `permissions_inherit` to `false`. New records default
to inheriting, so a row added later to a list with carefully-locked-down records is
immediately visible to everyone holding list `view`. There is no "every new row in this
list starts unique" mode. If per-record access is meant to be a list's default posture,
that needs building.

**Breaking inheritance is reversible.** Flipping `permissions_inherit` back to `true`
re-joins the parent chain, and grants made while the child was independent are kept and
simply re-union with the inherited ones.

**`ListRead.workspace` exposes only a uuid, deliberately.** A list can be granted without
its workspace, so serializing the workspace's `display_name` would leak the name of a
workspace the caller was specifically not given access to. This is why the "Shared with me"
section shows the list's own slug as its subtitle rather than "in «Workspace»".

---

## Gotchas for whoever edits this next

**There is no Alembic.** The schema comes from `Base.metadata.create_all` over the models
imported in `main.py`'s lifespan and `tests/conftest.py`. A new model needs importing in
*both*. Adding a column to an existing table means **recreating the dev DB by hand** —
nothing migrates it for you.

**Never lazy-load a relationship after a commit.** The session factory sets
`expire_on_commit=False` precisely because a lazy reload under asyncpg raises
`MissingGreenlet`. In particular, `PermissionService` never touches `user.groups` — it
queries `user_group_association` directly, because the `DISABLE_AUTH` user isn't loaded
with groups eager-loaded and the lazy load would blow up outside the greenlet.

**`PermissionService` is imported lazily inside functions, on purpose.** `permissions.services`
imports from `auth.users.services` → `workspaces.services`, so a module-level
`from src.permissions.services import ...` in `workspaces/services.py` or `lists/services.py`
creates an import cycle. The deferred function-local imports in `list_visible`, `ListService.list`,
`ListItemService.list`/`query`, `ViewService.list`, and `FormService.list` are load-bearing.

**The PrimeReact theme hardcodes its colors.** `lara-light-cyan/theme.css` defines
`--primary-color` / `--surface-*` and then never references them (zero `var(--primary-*)`
usages in the file), so overriding those variables recolors nothing. Theming works only
because `themes.css` is a custom, **unlayered** override sheet imported **last** in
`main.tsx` — unlayered rules beat `@layer`ed Tailwind utilities, and last-import wins on
source order. Reordering those imports silently breaks every theme.

**`frontend/src/lib/types.ts` is maintained by hand.** Generated API types were considered
and deferred, so backend schema changes do not propagate — update `types.ts` yourself.

**Permission-gated controls are hidden while their query is in flight.** `useEffectiveActions`
returns an empty set until the request resolves, so buttons appear a beat after page load
rather than showing and then vanishing (which would let someone click a control they don't
have). Grant and inheritance changes invalidate the cached `["effective"]` queries.

**`POST /permissions/effective` is capped at 500 resources per call** (`max_length` on
`EffectiveActionsQuery.resources`). A page showing more rows than that needs to chunk.
