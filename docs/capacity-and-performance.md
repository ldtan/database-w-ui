# Capacity and performance

What this app costs to run, measured rather than guessed, and what that means for the
deployment described in `deploy/render/README.md`.

Measured 2026-08-01 against the seeded school demo (`src.demo.school`: 9 lists, ~740
records, 16 users) on an AMD Ryzen 7 5700U. That core is roughly a cloud vCPU and
probably a little faster, so **treat every number here as a best case** — the same code
on Render is likely 10–30% worse.

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

## Re-indexing a calculated column

The one operation whose cost scales with the *list* rather than the page. A calculated
column's value is stored so Postgres can filter and sort on it, and a schema change — a
formula edited, or a column one reads renamed or deleted — leaves that stored copy behind
until `POST /lists/{uuid}/columns/recompute` or the nightly `recompute_calculated` rebuilds
it. Nothing a person reads depends on it: reads recompute and overwrite the stored value, so
a stale index misplaces a row in a sorted or filtered view but never shows a wrong number.

Measured 2026-08-04, same machine and method, on a synthetic list with two calculated
columns per record (`qty * price` and a `CONCAT`), every row forced to rewrite:

| | 20,000 records | 50,000 records |
|---|---|---|
| App CPU | 0.67 s | 1.83 s |
| **CPU per record** | **0.034 ms** | **0.037 ms** |
| Peak Python memory over baseline | 2.8 MB | 2.8 MB |
| Longest contiguous event-loop block | — | 23 ms |

Three things that table is meant to settle:

- **It is linear**, so the per-record figure extrapolates. 5,000 records (the
  `FORMULA_INLINE_MAX_RECORDS` threshold) is ~0.2 s of CPU; 500,000 — what a 1 GB database
  holds — is ~18 s of CPU, about three minutes of wall time on the free tier's 0.1 CPU.
- **Memory is flat, not proportional.** 500-row batches are read, rewritten and released,
  so a list ten times longer costs the same 2.8 MB. This is why there is no row ceiling:
  a big list makes a slow job, not a failing one.
- **The event loop is never blocked long.** Every batch awaits the database, so the longest
  stretch of uninterrupted Python is ~23 ms — comfortably inside the health check, so the
  instance is not restarted underneath a running re-index.

A pass over a list that is *already* current costs ~0.026 ms per record and writes nothing,
because rows whose values already agree are skipped rather than rewritten with identical
bytes. That is what makes running the nightly sweep unconditionally cheap.

Two measurement traps worth recording, since both produced numbers that were wrong by an
order of magnitude before they were caught. `tracemalloc` traces every allocation and
inflates CPU roughly 10×, so memory and timing cannot be sampled in the same run. And an
event-loop watchdog built on `await asyncio.sleep(0)` is a busy spin: it re-schedules
immediately, saturates the process, and `process_time()` then charges all of it to the job
being measured. Sleep a real interval and measure the overshoot instead.

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
