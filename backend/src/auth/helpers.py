import hashlib

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.config import settings
from .tokens.models import RefreshToken
from .tokens.schemas import TokenData
from .users.models import User

ph = PasswordHasher()

ALLOWED_ACTIONS = {"create", "read", "update", "delete"}

ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS = settings.REFRESH_TOKEN_EXPIRE_DAYS
ALGORITHM = settings.JWT_ALGORITHM
PRIVATE_KEY = Path(settings.RSA_PRIVATE_KEY_PATH).read_text()
PUBLIC_KEY = Path(settings.RSA_PUBLIC_KEY_PATH).read_text()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return ph.verify(hashed_password, plain_password + settings.AUTH_SALT)
    except VerifyMismatchError:
        return False


def get_password_hash(password: str) -> str:
    return ph.hash(password + settings.AUTH_SALT)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})

    return jwt.encode(to_encode, PRIVATE_KEY, algorithm=ALGORITHM)


def create_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """Hashes a refresh token using SHA256 for consistent lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def store_refresh_token(db: AsyncSession, user_id: int, token: str) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    hashed_token = hash_refresh_token(token) # Use SHA256 hash
    refresh_token = RefreshToken(
        token=hashed_token,
        user_id=user_id,
        expires_at=expires_at,
    )
    db.add(refresh_token)
    await db.commit()


async def validate_refresh_token(db: AsyncSession, token: str) -> Optional[User]:
    hashed_token_for_lookup = hash_refresh_token(token) # Use SHA256 hash for lookup
    result = await db.execute(
        select(RefreshToken)
        .where(
            and_(
                RefreshToken.token == hashed_token_for_lookup,
                RefreshToken.revoked == False,
                RefreshToken.expires_at > datetime.now(timezone.utc),
            )
        )
        .options(selectinload(RefreshToken.user))
    )
    refresh_token = result.scalars().first()
    if refresh_token:
        # The user is eager-loaded above; no need for a second lookup.
        return refresh_token.user
    return None


async def revoke_refresh_token(db: AsyncSession, token: str) -> None:
    hashed_token_for_lookup = hash_refresh_token(token) # Use SHA256 hash for lookup
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token == hashed_token_for_lookup)
    )
    
    refresh_token = result.scalars().first()
    if refresh_token:
        refresh_token.revoked = True
        await db.commit()


def decode_access_token(token: str) -> TokenData:
    try:
        payload = jwt.decode(token, PUBLIC_KEY, algorithms=[ALGORITHM])
        user_uuid: str | None = payload.get("sub")
        username: str | None = payload.get("username")

        if username is None or user_uuid is None:
            raise ValueError("Token payload missing required claims")

        return TokenData(username=username, user_uuid=user_uuid)

    except (JWTError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
