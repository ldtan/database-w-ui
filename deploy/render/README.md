# Deploying to Render (~$13/mo)

[`render.yaml`](render.yaml) is the whole deployment. In Render: **New → Blueprint** → connect
the repo → branch `main` → **Blueprint path: `deploy/render/render.yaml`** (the file does not
have to sit at the repo root; `rootDir` inside it stays relative to the repo root regardless).

It creates four things: a Postgres on the smallest paid instance (`dbui-db`, `basic-256mb`, $6/mo
plus storage), the API as a Python web service on `starter` (`dbui-api`, $7/mo, 0.5 CPU / 512 MB,
always-on), the SPA as a free static site (`dbui-web`), and the environment group they share
(`dbui`). Around **$13/mo** all told, plus storage and any bandwidth past the 100 GB included per
service. The static site is the only free thing left, and it has no paid tier to move to.

**Connect the repo through your GitHub account**, not the "public Git repository" URL option:
Render only auto-deploys — and only auto-syncs a Blueprint — for a connected account. A pasted
URL means every future deploy is a button you have to remember to press. Repo visibility does
not matter; a private repo deploys fine once the account is connected.

## First deploy, step by step

1. **Blueprint path** `deploy/render/render.yaml`, branch `main`.
2. Render asks for the values marked `sync: false`. **`DATABASE_URI` cannot be known yet** —
   the database does not exist until the Blueprint applies — so put a placeholder in it and
   **expect the first `dbui-api` deploy to fail**. That is not a bug; step 3 fixes it. Set a
   real `FIRST_SUPERUSER_EMAIL` and `FIRST_SUPERUSER_PASSWORD` here: that account is seeded on
   first boot, on a public URL.
3. Open **`dbui-db` → Internal Database URL**, and rewrite its scheme:
   `postgresql://` → **`postgresql+asyncpg://`**. Paste that into the `dbui` env group as
   `DATABASE_URI`. No `ssl` parameter — the internal network does not need one.
4. On **`dbui-api` → Environment → Secret Files**, upload `backend/keys/private.pem` and
   `backend/keys/public.pem` under exactly those names (the env group points at
   `/etc/secrets/…`). Use the **existing** pair: RS256 signs every token with it, so a fresh
   key silently logs out everyone holding one.
5. Redeploy `dbui-api`. The log should show the Alembic revisions running, *then*
   `Uvicorn running`.
6. In **GitHub → Settings → Secrets → Actions**, add `DATABASE_URI` for the maintenance
   workflow — the **external** URL this time, with `?ssl=require` (see below).

## The things that will bite

**`DATABASE_URI` is set by hand, and cannot use `fromDatabase`.** Render emits a plain
`postgresql://` URL, and SQLAlchemy reads its driver out of that scheme — so the app reaches
for psycopg2, which is neither installed nor async (`ModuleNotFoundError`). One string edit is
the whole fix.

**Two different URLs.** The API uses the **internal** connection string (no SSL parameter).
The maintenance jobs run on GitHub's runners, which cannot route to Render's internal network,
so they need the **external** string — and therefore SSL. The parameter is **`ssl=require`**,
never `sslmode=require`: asyncpg is not libpq, and raises an unexpected-keyword `TypeError`
from a URL that looks perfectly correct.

**The database is paid, and that is deliberate.** A *free* Render database is deleted — not
emptied, deleted — about 30 days after it is created, and the app goes down with it:
`DATABASE_URI` names a host that no longer exists, `alembic upgrade head` fails in the start
command, and the service never reaches uvicorn. **Down, not blank.** `plan: basic-256mb` ($6/mo
for the instance, plus storage at roughly $0.30/GB-month) ends that clock and adds managed
backups. It was the **first** thing to buy — before the web service — because the web service
being slow is survivable and the data vanishing is not. Recovery from a lost database, if it
ever comes to that, is still manual and takes minutes: create one, paste its internal URL into
`DATABASE_URI`, redeploy — the start command migrates it from empty and the superuser is seeded
again.

`plan` is one of the few database fields Render *does* apply to an existing database on
blueprint sync — most of the rest (`databaseName`, `user`, `postgresMajorVersion`) are fixed at
creation. So the upgrade is this one-line edit plus a sync, and the data and the connection
strings both survive it: nothing to re-paste into `DATABASE_URI`, which is just as well, since
it is `sync: false` and hand-set. Render also allows a *lapsed* free database to be upgraded
within 14 days of its expiry, which is the escape hatch if the clock is missed.

**The migration runs in `preDeployCommand`**, which is the reason the API is on a paid plan as
much as the always-on is: Render does not offer the hook on free instance types, and it used to
sit in `startCommand` where every booting instance races every other one to run it. Now it runs
after the build, before anything is deployed, on a separate instance — and if it fails, the
*deploy* fails and the currently running version carries on serving. A bad migration is a red
deploy instead of an instance that cannot boot. Two consequences worth knowing: the hook has a
30-minute timeout, and because it runs on a separate instance, nothing it writes to the
filesystem reaches the service — it may only touch the database.

**One instance is still load-bearing.** `numInstances` is unset, and the login throttle counters
in `backend/src/auth/tokens/throttle.py` are per process, so they are exact only while that
holds. Scaling is now possible where it was not before; that counter is the first thing to fix
if it happens, and the migration is already in the right place for it.

**`VITE_LIVE_REFRESH_SECONDS` is now only a fallback.** It has been demoted twice. On the free
plan it did double duty — a focused tab polling kept the instance from spinning down — and
`starter` ended that. Then live records became a **server push**: an open list holds
`GET /lists/{uuid}/events` and refetches when told to, so on a healthy deployment this interval
is not used at all. It is what the app falls back to when the stream cannot be held open, which
is worth keeping set rather than zeroed for exactly that case.

**`SSE_ENABLED` is the kill switch, and it lives on the API on purpose.** Setting it `False`
makes the events route answer `503`, which every client reads as "fall back to polling". It is
an environment change plus an API restart; putting it in the SPA's build-time config would have
made turning push off a rebuild and a redeploy of the static site. `SSE_MAX_STREAM_SECONDS`
(300) is also **the revocation window** — authorization is checked at connect and never again,
so losing `view` on a list stops the signals at the subscriber's next reconnect and not before.

**`GEOCODE_USER_AGENT` is not set here, and address search is anonymous to Nominatim as a
result.** Their policy asks to be able to contact the caller, and the default value literally
says "set GEOCODE_USER_AGENT" — being anonymous is what gets a User-Agent blocked. The failure
is graceful when it comes (`available: false` and a sentence; a `location` column still takes a
typed address and a dropped pin), but it should be set. `GEOCODE_URL` empty disables the search
outright, which is a supported way to run.

**The SPA and the API share an origin by rewrite.** `frontend/src/lib/api.ts` calls a relative
`/api/...` and has no configurable base URL, so the static site rewrites `/api/*` to the API
(and `/*` to `/index.html`, because React Router owns the paths). CORS therefore never enters
the picture. **Check the API's hostname after the first deploy** — Render appends a suffix when
a name is taken, and a wrong host in that rewrite 404s every request in the app. If the rewrite
ever has to go, the fallback is a `VITE_API_BASE` env var in the client plus `ALLOWED_ORIGINS`
set to the site's URL.

**The API is on a public URL, and that is not a misconfiguration.** `dbui-api.onrender.com`
answers anyone — the rewrite above is a convenience, not a shield, and it proxies to that same
public hostname. Nor could it be otherwise: the SPA runs in each visitor's browser, so any
request the app makes, `curl` makes identically. What protects it is the bearer token every
route requires (`401` without one), plus the failure limits on login, which is the only route
that has to answer an unproven caller. `ALLOWED_ORIGINS` is not part of that: CORS governs
what other browser pages may read, and scripts ignore it. The unauthenticated surface is
`/health` and the four `…/auth/tokens` routes (`login`, `refresh`, `revoke`, `jwks.json`) —
worth re-checking with an unauthenticated
`curl` after any change to how routers are mounted, since the token requirement is declared
per router (`dependencies=[Depends(get_current_user)]`) and a new router added without it
would be open.

`/docs`, `/redoc` and `/openapi.json` are **not** on that list here: `main.py` serves them
only while `DEBUG` is true, and `DEBUG` is `False` below, so they answer `404` on Render.
They were never an authentication hole — the token requirement applies to a request from
Swagger UI like any other — but they published the full route inventory and every payload
shape, which is free reconnaissance for anyone holding the URL. Note that this rides on the
same flag as `DISABLE_AUTH`: turning `DEBUG` on here would bring the docs back along with
much worse.

**No container.** The app needs Python 3.14 (every entity's public id comes from `uuid.uuid7`,
`src/core/mixins.py`), and Render's native runtime installs any released version, so
`backend/.python-version` is enough. A Dockerfile would only add build time.

**The nightly maintenance jobs run in GitHub Actions**
([`../../.github/workflows/maintenance.yml`](../../.github/workflows/maintenance.yml)), because
Render's Cron Job is a **paid** service type. Eight of them today — the shape reconcile, the
five purges, the orphan-grant sweep and the calculated-column re-index — and their **order
matters** where they overlap, which the workflow encodes: the workspace and list purges own
everything under a deleted parent, so they run before the jobs that sweep individual children,
and the grant sweep runs after all of them because the purges are what orphan grants in the
first place. (The re-index is independent and sits at the end.) All
are idempotent, chunked and safe to kill, so a missed night costs nothing.

**No cold start.** `starter` is always-on; the ~15-minute spin-down and the up-to-a-minute
first request belonged to the free plan and no longer apply.
