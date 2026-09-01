import hashlib

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, status
from jose import JWTError, jwt
from jose.backends import RSAKey
from sqlalchemy import select, and_
from starlette.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.config import settings
from .tokens.models import RefreshToken
from .tokens.schemas import TokenData
from .users.models import User

# RFC 9106's low-memory profile, which is also the OWASP minimum: t=2, m=19MiB, p=1.
# The library's own defaults (t=3, m=64MiB, p=4) cost 316ms of CPU and 64MiB of peak
# memory *per verification*, measured — enough that six concurrent sign-ins exhaust a
# 512MB instance, and enough that one of them dominated the whole login request. These
# parameters cost 44ms and 19MiB for the same guarantee.
#
# Safe to change in place: argon2 encodes its parameters *into the hash string*, so every
# password already stored still verifies on its own old terms and nobody is locked out.
# The flip side is that an old hash keeps costing the old price forever, which is why
# TokenService.authenticate re-hashes on a successful sign-in (`check_needs_rehash`) —
# the saving arrives as people log in, not the moment this line changes.
ph = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)

ALLOWED_ACTIONS = {"create", "read", "update", "delete"}

ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS = settings.REFRESH_TOKEN_EXPIRE_DAYS
ALGORITHM = settings.JWT_ALGORITHM
PRIVATE_KEY = Path(settings.RSA_PRIVATE_KEY_PATH).read_text()
PUBLIC_KEY = Path(settings.RSA_PUBLIC_KEY_PATH).read_text()

# Parse each PEM once, here, instead of on every call.
#
# `jwt.encode(payload, PRIVATE_KEY, ...)` looks free and is not: python-jose builds a key
# object from whatever it is handed, so passing the PEM *string* re-parsed a 4096-bit RSA
# key on every token issued — 272ms of CPU per login and per 15-minute refresh, against
# 6ms for the same signature from a prepared key. It was the single most expensive thing
# the app did, and none of it was cryptography.
#
# The keys stay interoperable in both directions, so this is not a token-invalidating
# change: a token signed before this existed still verifies after it, and vice versa.
#
# PRIVATE_KEY/PUBLIC_KEY are kept as strings alongside these because the JWKS endpoint
# publishes the PEM itself (see TokenService.public_key).
_SIGNING_KEY = RSAKey(PRIVATE_KEY, ALGORITHM) if ALGORITHM.startswith("RS") else PRIVATE_KEY
_VERIFY_KEY = RSAKey(PUBLIC_KEY, ALGORITHM) if ALGORITHM.startswith("RS") else PUBLIC_KEY


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return ph.verify(hashed_password, plain_password + settings.AUTH_SALT)
    except VerifyMismatchError:
        return False


def get_password_hash(password: str) -> str:
    return ph.hash(password + settings.AUTH_SALT)


def needs_rehash(hashed_password: str) -> bool:
    """Whether this stored hash was made with weaker parameters than `ph` uses now.

    True for every password stored before the parameters above changed. See the comment
    on `ph`: the point of re-hashing is that argon2's cost is baked into the stored hash,
    so a password that is never re-hashed never gets cheaper.
    """
    return ph.check_needs_rehash(hashed_password)


# The async pair below is what request paths must use. Argon2 is deliberately expensive
# and — unlike almost everything else here — it is *synchronous CPU work*, so calling the
# functions above directly from an `async def` route runs it on the event loop. Uvicorn
# serves this app with a single worker, so there is no other thread to take up the slack:
# measured, one concurrent sign-in took an unrelated `GET /auth/users/me` from 34ms to
# 459ms, and four took it to 1250ms. Hashing in a worker thread keeps one person signing
# in from being everybody's outage.
#
# The synchronous versions stay for callers that are not serving a request — seeding,
# the demo builder, tests — where a threadpool hop would buy nothing.


async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    return await run_in_threadpool(verify_password, plain_password, hashed_password)


async def get_password_hash_async(password: str) -> str:
    return await run_in_threadpool(get_password_hash, password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})

    return jwt.encode(to_encode, _SIGNING_KEY, algorithm=ALGORITHM)


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
                RefreshToken.revoked.is_(False),
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
        payload = jwt.decode(token, _VERIFY_KEY, algorithms=[ALGORITHM])
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
