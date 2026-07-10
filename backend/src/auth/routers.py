from fastapi import APIRouter

from .tokens.routers import router as tokens_router
from .users.routers import group_router, router as users_router


# Mounted under settings.API_PREFIX by the app factory, which makes the login
# endpoint /api/auth/tokens/login -- the path oauth2_scheme's tokenUrl assumes.
router = APIRouter(prefix="/auth")
router.include_router(tokens_router)
router.include_router(users_router)
router.include_router(group_router)
