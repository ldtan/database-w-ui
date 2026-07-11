from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from src.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # This replaces before_first_request logic

    # Initialize database
    from src.infrastructure.database import init_db, SessionLocal

    # Explicitly import models to ensure they are registered with Base.metadata
    import src.auth.users.models
    import src.auth.tokens.models
    import src.workspaces.models
    import src.lists.models
    import src.lists.columns.models
    import src.lists.items.models
    import src.lists.views.models
    import src.lists.forms.models

    await init_db()

    # Seed the first user so there is an account to authenticate with
    from src.auth.seed import seed_initial_data
    async with SessionLocal() as db:
        await seed_initial_data(db)

    yield
    # Shutdown logic (if any) would go here

def create_app() -> FastAPI:
    app = FastAPI(
        title="Database with UI",
        description="This app lets you create lists and add records.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Import and include your routers here
    from src.auth.routers import router as auth_router
    from src.workspaces.routers import router as workspaces_router
    from src.lists.routers import router as lists_router
    app.include_router(auth_router, prefix=settings.API_PREFIX)
    app.include_router(workspaces_router, prefix=settings.API_PREFIX)
    app.include_router(lists_router, prefix=settings.API_PREFIX)

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
