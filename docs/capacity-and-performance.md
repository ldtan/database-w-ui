# Capacity and performance

What this app costs to run, measured rather than guessed, and what that means for the
deployment described in `deploy/render/README.md`.

Measured 2026-08-01 against the seeded school demo (`src.demo.school`: 9 lists, ~740
records, 16 users) on an AMD Ryzen 7 5700U, and extended 2026-08-03 with the one operation
the demo is too small to time (see "Changing a formula" below). That core is roughly a cloud
vCPU and probably a little faster, so **treat every number here as a best case** — the same
code on Render is likely 10–30% worse.

## Why CPU-time, and not latency

Latency on a laptop tells you nothing about a 0.1-CPU instance. **CPU-milliseconds per
request** does, because a plan with N CPU delivers N×1000 ms of CPU per wall second, and
the platform meters exactly that. A request costing X ms of CPU therefore cannot complete
faster than `X / (1000 × N)` seconds no matter how idle the box is, and the sustained
ceiling is `N × 1000 / X` requests per second.

The method, if you need to redo it after a change: start the app, then sample the uvicorn
process's `TotalProcessorTime` before and after a run of N identical requests and divide.
Do not measure wall time — argon2 runs on four threads by default, so it burns roughly
3× more CPU than its latency suggests, and that gap is the whole story below.

## What each operation costs

| Operation | App CPU |
|---|---|
| `GET /health` | ~0 ms |
| `GET auth/users/me` | 6 ms |
| `GET lists/{u}/columns/` | 14 ms |
| `GET lists/{u}/views/` | 20 ms |
| `GET lists/{u}/items/?limit=25` | 23 ms |
| `GET lists/{u}/items/?limit=100` | 29 ms |
| `GET lists/{u}/metrics/values` | 28 ms |
| `GET lists/{u}/items/?q=…` (search) | 106 ms |
| **Opening a list** (list + columns + items + views + forms) | **98 ms** |
| **`POST auth/tokens/login`** | **717 ms** |

Reading is cheap and scales the way you would hope: `limit=100` costs only 26% more than
`limit=25`, because records are one JSONB row each and a page is one query — the per-row
work is real but small. Search is the expensive read, at ~4× a plain page, because
`_reference_matches` resolves labels in other tables before it can match cells.

Everything else is a rounding error next to login.

## Login was 30× a page load, and most of it was waste

The 717 ms breaks into three parts:

- **argon2 verify — 316 ms.** `PasswordHasher()` with library defaults: t=3, m=64 MiB,
  p=4. Expensive on purpose; that is what a password hash is for.
- **RS256 sign — 272 ms.** Not on purpose. `create_access_token` passed `PRIVATE_KEY` —
  the PEM *string* — to `jwt.encode`, so python-jose re-parsed a 4096-bit RSA key on
  every single call. Handing it a `jose.backends.RSAKey` built once at import costs
  **6 ms** instead. Same signature, same algorithm, 45× cheaper.
- **~129 ms** of session, DB round trips and framework.

The second one also charged every **token refresh**, which each active user does every
`ACCESS_TOKEN_EXPIRE_MINUTES` (15). So a logged-in user who never touched anything still
cost ~290 ms of CPU per quarter hour.

## Changing a formula is the one request whose cost is the list's size

Every operation above costs roughly the same whatever the data looks like — a page is a
page. Editing a `calculated` column's formula is the exception: its values are stored in
each record (data-model §5), so the edit has to rewrite every one of them, in the request
that made it. It is the most expensive thing the app does, and the only thing that gets
worse as a list grows rather than as traffic grows.

**Measured at ~0.11 ms of app CPU per record** — 20,000 records of ~600 bytes cost 2,172 ms
of CPU and 3.2 s of wall clock. Taken 2026-08-03 on the same machine as everything above, but
against a synthetic list rather than the school demo: at ~740 records the whole job finishes
in under a tenth of a second, which is the answer this section exists to put in context, not
one you can time. Since a 0.1-CPU instance delivers 100 ms of CPU per wall second:

| records | chunks | app CPU | PATCH duration, free (0.1 CPU) | Starter (0.5) | Standard (1) |
|---|---|---|---|---|---|
| 300 | 1 | 33 ms | ~0.3 s | ~0.07 s | ~0.03 s |
| 5,000 | 10 | 0.55 s | ~5 s | ~1 s | ~0.6 s |
| 20,000 | 40 | 2.2 s | ~22 s | ~4 s | ~2 s |
| 100,000 | 200 | 11 s | ~110 s | ~22 s | ~11 s |

**Above `FORMULA_INLINE_MAX_RECORDS` (5,000) the request stops paying for it.** The table
above is what the *work* costs; who waits for it is a separate decision. Under the threshold
the recompute happens in the request and the response is already correct when it goes out.
Over it, the work is handed to a FastAPI background task, the PATCH returns immediately, and
the columns report `recompute_pending` until it finishes. The threshold does not cap the work
— a deferred run still does the whole list — it only decides whether anyone is waiting.

**What the 500-row chunking bounds, and what it does not.** It bounds *memory* — peak Python
allocation is flat at ~3 MB whatever the list size, because a batch is read, rewritten and
released — and it bounds *lock duration*, since each batch commits on its own rather than
holding row locks across the job. It does **not** bound the total, because the batches run
one after another inside one request, and nothing else does either: there is deliberately no
row ceiling (known-gaps §5), so a big list makes a slow request rather than a refused one.

What it also buys is responsiveness. Every batch awaits the database, so the event loop is
never blocked for more than one batch's Python — **41 ms measured, ~0.4 s at 0.1 CPU** — which
is comfortably inside the health check. This is the one place where failure mode 1 below would
otherwise repeat itself at a much larger scale: a 22-second synchronous rewrite would fail
`/health` and have Render restart the instance out from under a half-finished backfill.

**The two shapes this takes.** The config is committed *before* the rewrite starts, so between
those two moments the column is live with a new formula and the records still hold the old
formula's answers. Under the threshold that window is a fraction of a second and nobody is
inside it: the response returns with `recompute_pending: false` and the edit is
indistinguishable from renaming a column. Over it the window is real and deliberate — the PATCH
returns straight away, the background task works through the list, and every viewer's columns
query (which rides the live-refresh interval) reports `recompute_pending: true`, so the grid
warns and the columns panel marks the column while rows update in waves of 500. Either way the
values are *uniformly* one version behind rather than half rewritten, so a filter or a sort
over the column keeps answering coherently while it waits.

**If it never finishes** — a redeploy, an instance restart, a cancelled request — committed
chunks stay, no stamp is written, and the column reports pending until something finishes it.
That is what the flag is for; nothing else would say so.

The nightly maintenance run includes `recompute_calculated` for exactly this, and it is cheap
to schedule because it skips any list whose stamp already matches its schema *without reading a
record* — on a healthy database the whole step is a handful of queries. By hand it is
`uv run python -m src.maintenance.recompute_calculated`, with `--dry-run` to size it first,
`--list <uuid>` to narrow it, and `--force` to rescan regardless of what the stamps claim. It
resumes rather than restarts: rows that already agree are not rewritten.

**Re-editing is no longer the expensive mistake it was.** Each formula PATCH starts its own
complete scan, and they overlap rather than queue — but a scan that has been superseded now
notices at its next chunk and abandons itself, rather than running to completion writing
answers that are already wrong. So three impatient re-edits cost roughly one full scan plus
two partial ones, not three full ones. The scans still overlap for as long as they run, so a
per-list lock is still the change that would make them strictly queue.

## Two failure modes that arrive before you run out of CPU

**1. A login froze the whole process.** `verify_password` is synchronous and was called
from an `async def` route, so argon2 ran *on the event loop*. Uvicorn runs one worker, so
there is no second thread to absorb it. Measured, with an unrelated cheap request
(`GET auth/users/me`) running alongside:

| Concurrent logins | p50 of the unrelated request |
|---|---|
| 0 | 34 ms |
| 1 | **459 ms** |
| 2 | 639 ms |
| 4 | **1250 ms** |

One person signing in made everyone else's app 13× slower. On 0.1 CPU, multiply again.

**2. Concurrent logins exhaust the RAM before the CPU.** The process idles at ~146 MB,
and every in-flight argon2 hash adds **64 MiB** on the default parameters. Six at once
exceeds 512 MB and the instance is killed. This matters more than it sounds: Render's
Free *and* Starter instances are both 512 MB, so paying $7 does not buy a way out of it.
Only the argon2 parameters, or a 2 GB Standard instance, actually move this ceiling.

## What this means per plan

Wall-clock floor per request with no contention at all:

| | Free (0.1 CPU) | Starter (0.5 CPU) | Standard (1 CPU) |
|---|---|---|---|
| Open a list | ~1.0 s | ~0.20 s | ~0.10 s |
| Poll / cell edit | ~230 ms | ~46 ms | ~23 ms |
| Login — before the fixes | ~7.2 s | ~1.4 s | ~0.72 s |
| Login — after the fixes | **~0.67 s** | ~0.13 s | ~0.07 s |
| Steady-state active users | ~50 | ~245 | ~490 |

"Active user" means a tab open on a list: one poll per `VITE_LIVE_REFRESH_SECONDS` (60 in
production), a navigation every couple of minutes, a token refresh every 15 — about
1.5 ms of CPU per second each, taken at a 60% utilisation target because a single event
loop queues badly above that.

That steady-state row was never the real limit, though. **The limit was a login burst.**
Thirty people arriving at once cost 30 × 717 ms = 21.5 s of CPU, which on the free tier
meant **3.6 minutes** during which the app served nobody. After the fixes below the same
burst costs 2.0 s of CPU — about **20 seconds** on the free tier, and nobody else's
requests queue behind it. That single change is worth more than either paid upgrade.

## Importing is the other request whose cost is a file's size

Measured on the same machine as the table above, against a local Postgres, per record:

| Import shape | Per row |
|---|---|
| Text + number | 2.53 ms |
| Ten text columns | 2.43 ms |
| One lookup column (a label resolved per cell) | 3.60 ms |
| One `unique` column | **4.06 ms** |

Two things worth reading off it. **Width is nearly free** — ten columns cost less per row
than two, because the per-row overhead is the savepoint and the insert, not the cells.
**A `unique` column is the expensive one**, at +60%: `_enforce_unique` is a SELECT per row,
and it is the one cost that grows with the list rather than with the file.

None of that is app CPU alone; it is app plus a round trip on the same box. On the free
instance the CPU part multiplies by ~10 and each round trip goes from tenths of a
millisecond to a few, which lands a row nearer 15–25 ms. That number is the whole reason
`IMPORT_MAX_ROWS` is 2,000 rather than 5,000: at 5,000 the request runs past two minutes,
and the interesting part is not the wait but what a timeout leaves behind, since rows are
committed every 500. See known-gaps §12.

**An import does not block anybody else, and it is worth knowing why.** The same
experiment as the login one above — a cheap unrelated read driven in a loop, once against
an idle app and once alongside a 2,000-row import, both on the one event loop:

| | p50 | p95 | max |
|---|---|---|---|
| Idle | 8.2 ms | 11.9 ms | 16.2 ms |
| During a 2,000-row import | **10.6 ms** | 16.2 ms | 108.6 ms |

The import took 16.5 s and the unrelated request was served **1,457 times** while it ran.
That is the opposite of what a login used to do (34 ms → 459 ms on one concurrent sign-in),
and for a structural reason: argon2 was synchronous CPU sitting on the loop with no second
thread behind it, whereas every import row awaits a database round trip, so the loop yields
thousands of times a second and everything interleaves. The 108 ms outlier is a batch
commit. Nor does it starve the pool — one connection out of 5 plus 10 overflow — or hold
locks, since it commits every 500 rows.

Read the 1.3× as a floor rather than a forecast. This machine has cores to spare, so the
import's CPU barely competes; on a shared 0.1 CPU there is one small allowance and an
import wants effectively all of it for its whole duration, so co-running requests lose
roughly the share the import takes. That is degradation spread evenly and ending when the
import does, not the head-of-line stall the login had — which is why the import cap is
about the *importer's* wait and what a timeout leaves behind, and not about protecting
everyone else.

**Cloning a list with its records costs ~0.93 ms per record** and, since the walk was
chunked, a flat amount of memory: 5,000 records peaked at **4.0 MB** of Python allocation
against **30.6 MB** before, and the old figure was ~6 kB per record with nothing bounding
it. That is the one that mattered — 50,000 records would have been ~300 MB on an instance
that idles at 146 MB with 512 MB to spend. The time is not capped; see known-gaps under
cloning for why not.

**Bulk-deleting 500 records went from 1,503 round trips and 3.89 s to 7 and 0.22 s**, and
the 7 no longer grows with the selection. Three things were stacked: the router checked
permissions with one `has()` per uuid, the service then re-fetched each record before
setting `deleted_at`, and — the one worth remembering — `effective_actions_bulk` was not
actually batching, because the identity map it warmed holds weak references and dropped
everything before the chain walk could use it. That last one also charged
`POST /permissions/effective`, which the grid calls on every page load to decide which
per-row buttons to show. See known-gaps under permissions.

The measurement above is the *cheap* path: the caller is the workspace owner, so
`effective_actions` short-circuits before it looks at grants. A non-owner deleting the same
selection paid two further queries per record.

## Data volume is not a constraint and probably never will be

Records are ~**1.9 kB each** including indexes (the JSONB `data` column itself averages
808 bytes). The entire school demo is **11 MB**. A 1 GB database — what both the free
plan and Basic-256mb give you — therefore holds on the order of **500,000 records**.

So the paid database plan buys persistence and backups, not headroom: the free instance's
30-day deletion (see the `databases` block in `deploy/render/render.yaml`) is a
reliability problem, never a capacity one. Storage will not be what makes you upgrade.

## The frontend needs nothing

The SPA is a static site on a CDN — already free, and no paid tier makes it faster. The
build is ~374 KB of gzipped JS, 36 KB of CSS and ~726 KB of fonts, so roughly 1.2 MB on a
cold visit and effectively zero on repeat visits, since Vite's hashed asset names are
immutably cacheable. Against 100 GB/month that is ~85,000 first-time visits.

The tempting "consolidation" — serving `dist/` from FastAPI with `StaticFiles` to run one
service instead of two — is a **downgrade**. It moves work that a CDN does for free onto
the one resource that is actually scarce, and it gives up the edge caching. The two-service
split in the blueprint is deliberate.

## What was fixed

Three changes, all in the auth path, none of which alter behaviour:

1. **Build the signing key once** (`src/auth/helpers.py`). The PEM is parsed at import into
   a `RSAKey`; `create_access_token` and `decode_access_token` use it. 272 ms → 6 ms per
   token issued, on logins and refreshes alike.
2. **Hash off the event loop.** `verify_password_async` / `get_password_hash_async` wrap
   the synchronous primitives in a threadpool, and the async request paths use them, so
   argon2 no longer blocks every other request in the process. The sync versions remain
   for non-request callers.
3. **Argon2 tuned to the OWASP minimum** (t=2, m=19 MiB, p=1): 316 ms → 44 ms of CPU and
   64 MiB → 19 MiB of peak memory.

Measured after the change, same method and same machine: a login costs **67 ms** of CPU,
down from 717 — a **10.7× reduction**, and rather better than the ~180 ms the parts
predicted, because argon2 at `p=4` was also burning CPU on thread synchronisation that
never showed up in an isolated measurement. `GET auth/users/me` fell from 5.9 ms to
2.7 ms as a side effect, since verification uses the prepared key too.

The blocking is gone outright. Re-running the table above:

| Concurrent logins | p50 before | p50 after |
|---|---|---|
| 0 | 34 ms | 33 ms |
| 1 | 459 ms | **30 ms** |
| 4 | 1250 ms | **36 ms** |

Peak memory per concurrent sign-in also drops 64 MiB → 19 MiB, which is what moves the
six-concurrent-logins OOM ceiling on a 512 MB instance.

Change 3 has a wrinkle worth remembering: **argon2 stores its parameters inside the hash**,
so an existing password keeps verifying at the old, expensive cost forever. That is why
`authenticate` calls `check_needs_rehash` after a successful verify and re-hashes in place
— the cost only actually falls as people sign in. It also means the change is safe: nobody
is locked out, because old hashes are still verifiable on their own terms.

## What is still true after the fixes

- **One instance, one worker.** The login throttle counters
  (`src/auth/tokens/throttle.py`) and the `alembic upgrade` in `startCommand` are both
  correct *only* because of that. Scaling the service breaks both; see the comments in
  `deploy/render/render.yaml`.
- **The free tier still spins down** after 15 minutes and takes about a minute to come
  back. A pinger every 10 minutes avoids it — Render allows 750 free instance-hours a
  month and always-on is ~730, so it fits, but it consumes nearly the whole workspace
  budget.
- **Search is still the expensive read** at ~106 ms. It has not been a problem at demo
  scale; it is the first thing to look at if list views get slow.
- **The free database is still deleted 30 days after creation**, which remains the single
  largest reliability risk in the deployment and the first thing worth paying for.
