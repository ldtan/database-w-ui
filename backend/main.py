from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from src.core.config import settings
from src.core.exceptions import PermissionDeniedError
from src.infrastructure.body_limit import BodySizeLimitMiddleware
from src.infrastructure.observability import (
    RequestContextMiddleware,
    configure_logging,
    install_exception_handler,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # This replaces before_first_request logic

    # Initialize database
    from src.infrastructure.database import init_db, SessionLocal

    # Registers every model on Base.metadata (one list, shared with the tests and
    # with Alembic's env.py — see src/infrastructure/model_registry.py).
    import src.infrastructure.model_registry  # noqa: F401

    await init_db()

    # Seed the first user so there is an account to authenticate with, and the
    # virtual system groups the permission resolver relies on.
    from src.auth.seed import ensure_system_groups, seed_initial_data
    async with SessionLocal() as db:
        await seed_initial_data(db)
        await ensure_system_groups(db)

    # Streams opened before this point must not survive it. Clear the flag on the
    # way in so an app built a second time in one process (the test suite does this
    # per fixture) does not inherit the previous one's shutdown — or its event loop.
    from src.lists.events.broker import begin_shutdown, reset_shutdown
    reset_shutdown()

    yield

    # End every open event stream. A stream is an in-flight request that has simply
    # not finished answering, so without this uvicorn's graceful shutdown waits out
    # each subscriber's full SSE_MAX_STREAM_SECONDS on every redeploy.
    begin_shutdown()

def create_app() -> FastAPI:
    # Swagger UI, ReDoc, and the schema document they both read, are development
    # tools and are not served anywhere else. They are not a hole on their own —
    # every router declares its bearer token and answers 401 to an unproven
    # caller either way — but they hand a stranger the complete route inventory
    # and every payload shape, including the routes the SPA never calls. Off in
    # production, that costs an attacker the work of reading the bundle.
    #
    # All three have to be None together: dropping only docs_url and redoc_url
    # leaves /openapi.json serving the same document without the viewer.
    #
    # This does not affect `npm run gen:api`. The frontend's types come from
    # create_app().openapi() in-process (src/infrastructure/export_openapi.py);
    # openapi_url decides whether the schema is *routed*, not whether it can be
    # built. Nor does it affect local work, where DEBUG is true.
    docs_enabled = settings.DEBUG

    # Before the app, so anything the lifespan logs on the way up is formatted and
    # carries a request id field like everything else.
    configure_logging(settings.DEBUG)

    app = FastAPI(
        title="Database w/ UI",
        description="This app lets you create lists and add records.",
        version="1.0.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )

    # The backstop, not the upload limit: `MAX_REQUEST_BYTES` sits above
    # `MAX_UPLOAD_BYTES` so the import and batch-user routes keep answering with
    # their own message naming their own limit, and this only catches bodies nothing
    # else bounds. See the setting for why the two must not be equal.
    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=settings.MAX_REQUEST_BYTES
    )

    # Added last, so it is outermost: `add_middleware` builds the stack in reverse.
    # That is deliberate — a request refused by the size limit above is still worth a
    # log line and still gets an `X-Request-ID` to quote when somebody asks why their
    # upload failed.
    app.add_middleware(RequestContextMiddleware)

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        # Custom response headers are invisible to cross-origin JS unless exposed.
        # Dev goes through the Vite proxy (same-origin) and would work regardless,
        # so without this the paginator breaks only in production.
        expose_headers=["X-Total-Count"],
    )

    # Map permission failures raised anywhere in the service layer onto 403.
    @app.exception_handler(PermissionDeniedError)
    async def _permission_denied_handler(request: Request, exc: PermissionDeniedError):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)}
        )

    # Last resort. Everything the service layer raises deliberately has a handler
    # above or is an HTTPException; this is for the ones nobody predicted, and it is
    # the only place their traceback is written down.
    install_exception_handler(app)

    # Liveness probe for a host's health check (Render, a container orchestrator).
    # It answers from the process alone: a check that queried the database would
    # turn a brief database outage into a restart loop, killing the instance that
    # is about to recover. A database the app cannot work against is caught at
    # startup instead, by the schema check in init_db.
    @app.get("/health", tags=["health"], summary="Liveness probe")
    async def _health() -> dict[str, str]:
        return {"status": "ok"}

    # Import and include your routers here
    from src.auth.routers import router as auth_router
    from src.workspaces.routers import router as workspaces_router
    from src.lists.routers import router as lists_router
    from src.permissions.routers import router as permissions_router
    from src.people.routers import router as people_router
    from src.geocode.routers import router as geocode_router
    # Live records. Deliberately *not* included into lists_router: that router
    # applies Depends(get_current_user), which takes Depends(get_db), and a yield
    # dependency is released only once the response completes — which for a stream
    # is when the browser closes it. See src/lists/events/routers.py.
    from src.lists.events.routers import router as list_events_router
    app.include_router(auth_router, prefix=settings.API_PREFIX)
    app.include_router(workspaces_router, prefix=settings.API_PREFIX)
    app.include_router(lists_router, prefix=settings.API_PREFIX)
    app.include_router(list_events_router, prefix=settings.API_PREFIX)
    app.include_router(permissions_router, prefix=settings.API_PREFIX)
    app.include_router(people_router, prefix=settings.API_PREFIX)
    app.include_router(geocode_router, prefix=settings.API_PREFIX)

    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:create_app",
        host="0.0.0.0",
        port=5000,
        reload=True,
        factory=True,
    )
