from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from . import throttle
from .schemas import RefreshRequest, TokenResponse
from .services import TokenService
from src.core.exceptions import (
    InactiveUserError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from src.infrastructure.database import get_db


router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.post("/login", response_model=TokenResponse)
async def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    ip = throttle.client_ip(request)

    # Ahead of TokenService.login, not inside its failure path: verifying a
    # password costs an argon2 hash by design, so refusing to answer at all is
    # the point. Otherwise a throttled attacker still spends the instance's CPU
    # on every attempt they make.
    wait = throttle.retry_after_seconds(form_data.username, ip)
    if wait is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed sign-in attempts. Try again shortly.",
            headers={"Retry-After": str(wait)},
        )

    try:
        tokens = await TokenService.login(db, form_data.username, form_data.password)
    except InvalidCredentialsError as exc:
        throttle.record_failure(form_data.username, ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except InactiveUserError as exc:
        # Not counted. The password was right — this is a disabled account, not
        # a guess, and charging it would let a deactivated user's own attempts
        # lock out the address they share with everyone else in their office.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    throttle.clear_failures(form_data.username)
    return tokens


@router.post("/refresh", response_model=TokenResponse)
async def refresh_access_token(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await TokenService.refresh(db, payload.refresh_token)
    except InvalidRefreshTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except InactiveUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc


@router.get("/jwks.json")
async def get_public_key():
    """Expose the public key for other microservices to verify tokens."""
    return {"public_key": TokenService.public_key()}
