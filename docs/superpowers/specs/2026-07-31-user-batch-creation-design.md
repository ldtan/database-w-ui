# Batch user creation from a CSV file

**Date:** 2026-07-31
**Scope:** a superuser uploads one CSV and gets many accounts — each with an optional
person record, group membership and activation state — instead of working the Add
user dialog once per person.

---

## Why this exists

Onboarding a roster today means opening `UserDialog` once per person and typing
username, email, password and six profile fields by hand. The app already knows how
to take a file and turn it into many rows — that is what `POST /tables/{uuid}/import`
does for table records — so the shape of the answer is settled. What is new is that
the rows being created are *accounts*, which are heavier than records: they carry
credentials, they can be administrators, and they sit underneath the permission
model rather than inside it.

## Decisions taken before designing

Five choices shaped everything below.

1. **Passwords are optional in the file and generated when absent.** There is no
   SMTP, no invite token, no password-reset mail anywhere in the backend, so a new
   account's password has to come from either the file or the response. Both are
   allowed: fill the cell to set a known password, leave it blank and the server
   mints a strong one.

2. **Generated passwords leave exactly once, as a downloadable CSV.** They are in
   the response body and nowhere else. Transcribing forty rows off a screen is not a
   real workflow; storing them server-side to re-issue later would be worse than
   either.

3. **Bad rows are skipped and reported, matching table import.** The app already
   teaches `{row, message}` per-row errors, and the mistake is recoverable — the
   users bin restores a deleted account whole.

4. **The file may carry `groups`, `is_active` and `is_superuser`.** Group membership
   is what makes a roster import worth doing. `is_superuser` is not an escalation
   path — only a superuser can call this at all — but it is sharp enough to be
   surfaced explicitly in the result rather than passing silently.

5. **Reuse the account services rather than re-implementing them.** A batch-created
   account must be indistinguishable from a form-created one. The failure this
   avoids is drift: batch quietly stopping to bootstrap a personal workspace, or
   hashing differently, months after anyone remembers there are two paths.

## Approaches considered

**Generalize the table-import engine and share it.** Rejected. The honest shared
surface between the two importers is about thirty lines of loop-with-error-
collection; everything substantial in `tables/transfer/services.py` is bound to
`Column`/`TableRecord` semantics — `_parse_cell` branches on `ColumnType`, then
`_validate_data` and `_enforce_unique` run. It is a risky refactor of working code
for a modest gain, and the two importers do not even want the same transaction
shape.

**Loop the existing single-user API from the browser.** Rejected. No backend work,
but N round trips over the network rather than in-process, password generation in
the browser, a closed tab leaving the job half-done — and `frontend/src/lib/csv.ts`
is a writer only, so a parser is needed either way.

**A dedicated batch module reusing the existing services.** Chosen. It follows the
pattern the codebase already established for import and inherits account-creation
semantics instead of copying them.

---

## The CSV contract

Twelve columns. Headers are spelled exactly as the API fields, the same convention
table import uses (headers are column `name`s, not display names).

| Column | Required | Notes |
|---|---|---|
| `username` | yes | |
| `email` | yes | validated as an email address |
| `password` | no | blank → the server generates one |
| `is_active` | no | `true`/`false`/`1`/`0`/`yes`/`no`; default `true` |
| `is_superuser` | no | same coercion; default `false` |
| `groups` | no | `;`-separated **existing** group names: `Teachers;Year 9` |
| `name_prefix` | no | Dr, Ms, … |
| `first_name` | conditional | required if the row has a profile |
| `middle_name` | no | |
| `last_name` | conditional | required if the row has a profile |
| `name_suffix` | no | Jr, III, … |
| `birth_date` | no | `YYYY-MM-DD`, not in the future |

The `;` separator and the boolean spellings are taken from
`tables/transfer/services.py::_parse_cell`, so a person who has used the table
importer already knows them.

**The profile rule.** A row with every person field blank creates an account with no
person record — the same outcome as leaving "Connect a person record" unchecked in
`UserDialog`. A row with *any* person field filled but missing `first_name` or
`last_name` is a row error. Creating the account and silently dropping the profile
would be the worst of the three options, because nothing would report it.

**Unknown headers are rejected, not ignored.** This diverges from table import
deliberately. There, ignoring unknown headers is correct: a table's columns are
user-defined and arbitrary, so a header this table does not have may well be a header
another one does. Here the column set is closed and known, so an unrecognized header
is a mistake — `frist_name` would silently drop every name in the file, and
`is_superadmin` would silently create no administrators. The request fails with a
400 naming the offending headers, before a single account exists.

**Missing `username` or `email` headers are likewise a 400**, for the same reason
read the other way: without them every row fails identically, and a thousand copies
of the same row error describe a file-level problem far worse than one sentence
does.

**Duplicate usernames inside one file** need no special handling. The first row
commits; the second hits the same unique constraint any conflicting import would and
is reported as a row error.

**A 1000-row cap** on data rows (the header does not count), rejected up front with a
400. Each row is several round trips, so an unbounded file is a request that never
returns.

**A file with headers and no data rows** is not an error: it comes back
`{created: [], errors: [], superusers_created: 0}`. Nothing was asked for and
nothing went wrong.

**CSV only.** Table import also accepts JSON because export emits it; there is no
user export here, so there is nothing for JSON to round-trip with.

## The template

Built client-side from `toCsv` + `downloadFile`, exactly as `ImportExportDialog`
builds the table template. One header row plus one example data row — a bare header
line does not convey that `groups` is `;`-separated or that `birth_date` wants
`YYYY-MM-DD`.

---

## Backend

A new package `backend/src/auth/users/batch/` — `schemas.py`, `services.py`,
`routers.py` — mirroring the layout of `tables/transfer/`. Its router carries no
prefix of its own and is included into `users_router` in `auth/users/routers.py`,
which supplies `/users`; the app factory's `API_PREFIX` and the `/auth` prefix in
`auth/routers.py` supply the rest.

```
POST /api/auth/users/import    multipart: file    superuser-only    → BatchUserSummary
```

Nothing else claims that path — the only other POSTs under `/users` are `/` and
`/{uuid}/restore`, neither of which matches its shape — but it is declared before the
`/{uuid}` routes anyway, following the convention `list_deleted_users` already
documents.

Superuser-only via the existing `Depends(require_superuser)`, matching every other
account write.

### The file is parsed whole before anything is written

Buffering the file (bounded by the 1000-row cap) rather than streaming it buys three
things a row-at-a-time loop cannot have:

- the header check fails before any account exists;
- the row cap is enforced before any account exists;
- every distinct group name in the file resolves in **one** query, not one per row.

### Per row, in this order

1. **Validate in memory.** A `BatchUserRow` Pydantic model carries the email format,
   the not-in-the-future birth date, the boolean coercion and the profile rule.
   Nothing has touched the database yet, so a row that is going to fail fails before
   it has created anything. This ordering is what makes step 7 nearly unreachable.
2. **Generate the password** if the cell was blank — `secrets.token_urlsafe(12)`,
   which is 16 URL-safe characters carrying 96 bits of entropy. Long enough that it
   never needs a strength policy, short enough to read off a page and type.
3. **Create the account** — `UserService.create`, inheriting the argon2 hashing, the
   conflict message and the personal-workspace bootstrap.
4. **Create the profile** — `PersonService.create`, if the row has one.
5. **Attach groups** by assigning `user.groups` from the pre-resolved map. This is
   the one place batch touches the ORM directly rather than going through a service.
   `UserGroupService.update` is not usable here: it sets a group's *entire*
   membership from a uuid list, so calling it per row would erase everyone already
   in the group. Assigning the fresh user's own (empty) collection cannot.
6. **Promote**, if `is_superuser` — `UserService.update(…, UserUpdate(is_superuser=
   True))`. `UserCreate` has no such field on purpose ("an account cannot be *born* a
   superuser"), so this mirrors the follow-up PATCH `UserDialog` already does. Last
   in the sequence deliberately: it keeps step 7 clear of `_guard_last_superuser`.
7. **Roll the row back** if 4–6 failed after the account existed: soft-delete the
   account and report the row, so "a reported row created nothing" holds. Step 1
   makes this nearly unreachable — the residual cases are infrastructure failure and
   a group deleted concurrently with the import — but it is the invariant the
   summary implies, and it is five lines. The account lands in the users bin like any
   other deleted account, with its personal workspace, exactly as a deleted
   form-created account does.

Because `UserService.create` and `WorkspaceService.create` each commit internally,
every row is already its own transaction. That is precisely the isolation
skip-and-report wants, so no savepoint machinery is needed here — unlike table
import, which inserts into one session and commits in batches for speed.

### Response

```python
class BatchUserError(BaseModel):
    row: int
    message: str

class CreatedUser(BaseModel):
    row: int
    uuid: UUID
    username: str
    # Present only when the server minted it. Returned exactly once, never stored.
    generated_password: Optional[str] = None

class BatchUserSummary(BaseModel):
    created: list[CreatedUser]
    errors: list[BatchUserError]
    superusers_created: int
```

`created` is a table where `ImportSummary` has a bare count, because the credentials
have to come back somehow and the row number is what ties one to its line in the
file. `superusers_created` exists so a typo'd column is visible in the result rather
than silent.

**This response body is the only place a generated password exists in plaintext.**
It must not be logged, and nothing persists it server-side.

---

## Frontend

`BatchUserDialog.tsx` in `features/people/`, opened by an "Import users" button
beside "Add user" in the Users section header of `PeoplePage` — superuser-only, like
its neighbours.

Simpler than `ImportExportDialog`: no tabs, because there is nothing to export.
Explanatory text, "Download template" in the footer, a `FileUpload` in `basic`
`customUpload` mode, and a result panel reusing that dialog's `Message` +
scrollable error-list pattern verbatim.

The result panel adds two things:

- a warn `Message` when `superusers_created > 0`, naming the count;
- when any password was generated, a prominent "Download credentials" button that
  builds a `username,password` CSV in the browser with `toCsv`. Its copy states
  plainly that closing the dialog loses them for good, and that a forgotten one is a
  password reset in the Edit user dialog.

On success it invalidates the `["users"]` and `["people"]` query keys, matching
`UserDialog`.

After the schemas land, `npm run gen:api` regenerates `lib/api-schema.ts`; the new
response types are re-exported through `lib/types.ts` like the rest.

---

## Testing

`backend/tests/test_user_batch_import.py`, in the style of `test_transfer.py`:

- **happy path** — accounts, person records and personal workspaces all exist
  afterwards
- **a generated password actually logs in**, and so does a supplied one
- **duplicate username inside one file** — row 2 errors, row 1 survives
- **unknown header** → 400 and nothing created
- **missing `username` header** → 400, not a thousand row errors
- **headers with no data rows** → 200, everything empty
- **profile with `first_name` but no `last_name`** → row error, no account created
- **unknown group name** → row error; a known one attaches
- **`is_superuser` promotion** takes effect
- **non-superuser caller** → 403
- **over-cap file** → 400
- **birth date in the future** → row error

Frontend: a vitest for the credentials-CSV builder as a pure function, matching
`recordValues.test.ts`. The dialog itself is verified by hand — `frontend/` has no
component-rendering test setup, and PrimeReact in jsdom is a maintenance cost this
feature does not justify taking on.

## What this does not do

- No dry-run/preview step. Skip-and-report plus the users bin covers the mistake.
- No update-existing-users semantics. A row whose username exists is a conflict, not
  an edit; changing accounts stays in the Edit user dialog.
- No email. Nothing is sent to anyone; the admin distributes credentials themselves.
- No JSON upload, and no user export to round-trip with one.
