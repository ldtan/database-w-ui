# Deploying to Render (free tier)

[`render.yaml`](render.yaml) is the whole deployment. In Render: **New → Blueprint** → connect
the repo → branch `main` → **Blueprint path: `deploy/render/render.yaml`** (the file does not
have to sit at the repo root; `rootDir` inside it stays relative to the repo root regardless).

It creates four things: a free Postgres (`dbui-db`), the API as a Python web service
(`dbui-api`), the SPA as a free static site (`dbui-web`), and the environment group they
share (`dbui`). Every paid upgrade is marked `PAID:` in the file and is a one-line edit.

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

**The free database is deleted after ~30 days, and the app goes down with it.** Not emptied —
deleted. `DATABASE_URI` then names a host that no longer exists, `alembic upgrade head` fails
in the start command, and the service never reaches uvicorn. **Down, not blank.** Recovery is
manual and takes minutes: create a new database, paste its internal URL, redeploy — the start
command migrates it from empty and the superuser is seeded again. The clock starts the day the
database is *created*. `plan: basic-256mb` (~$7/mo) ends the expiry and adds backups, and is
the **first** thing to buy when the data starts mattering — before the web service.

**The migration runs in the start command**, which is a compromise. It belongs in a pre-deploy
hook, because at boot every instance races every other instance to run it — but the free plan
has no such hook and runs exactly one instance, so there is no race to lose. It is the first
thing to move when this is paid for or scaled; `render.yaml` says so at the point of change.

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
`/health`, `/docs`, `/redoc`, `/openapi.json` and the three `…/auth/tokens` routes — worth
re-checking with an unauthenticated `curl` after any change to how routers are mounted, since
the token requirement is declared per router (`dependencies=[Depends(get_current_user)]`) and
a new router added without it would be open.

**No container.** The app needs Python 3.14 (every entity's public id comes from `uuid.uuid7`,
`src/core/mixins.py`), and Render's native runtime installs any released version, so
`backend/.python-version` is enough. A Dockerfile would only add build time.

**The four maintenance jobs run in GitHub Actions**
([`../../.github/workflows/maintenance.yml`](../../.github/workflows/maintenance.yml)), because
Render's Cron Job is a **paid** service type. They are idempotent housekeeping, so a missed
night costs nothing.

Expect a cold start of up to a minute on the first request after a quiet spell: Render spins a
free web service down after ~15 minutes idle.
