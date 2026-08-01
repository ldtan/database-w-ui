from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ..helpers import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    PUBLIC_KEY,
    create_access_token,
    create_refresh_token,
    get_password_hash_async,
    needs_rehash,
    revoke_refresh_token,
    store_refresh_token,
    validate_refresh_token,
    verify_password_async,
)
from src.core.exceptions import (
    InactiveUserError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)

from ..users.models import User
from ..users.services import UserService
from .schemas import TokenResponse


class TokenService:
    @staticmethod
    def public_key() -> str:
        return PUBLIC_KEY

    @staticmethod
    async def authenticate(db: AsyncSession, username: str, password: str) -> User:
        user = await UserService.get_by_username(db, username)
        if user is None or not await verify_password_async(password, user.password):
            raise InvalidCredentialsError("Incorrect username or password")
        if not user.is_active:
            raise InactiveUserError("Inactive user")

        # A sign-in is the only moment the plaintext exists, so it is the only moment a
        # stored hash can be upgraded to the current argon2 parameters. Without this the
        # cheaper parameters set on `ph` would only ever apply to new accounts, and every
        # existing password would keep costing the old price for the life of the account.
        #
        # After the credentials are accepted, deliberately: re-hashing a password that
        # turned out to be wrong would spend the cost on an attacker's guess. Failing to
        # upgrade is also not worth failing a valid login over, so this is the last thing
        # that happens and it does not gate the return.
        if needs_rehash(user.password):
            user.password = await get_password_hash_async(password)
            await db.commit()
        return user

    @staticmethod
    async def _issue(
        db: AsyncSession, user_id: int, user_uuid, username: str
    ) -> TokenResponse:
        access_token = create_access_token(
            data={"sub": str(user_uuid), "username": username},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        )
        refresh_token = create_refresh_token()
        await store_refresh_token(db, user_id, refresh_token)
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
        )

    @classmethod
    async def login(
        cls, db: AsyncSession, username: str, password: str
    ) -> TokenResponse:
        user = await cls.authenticate(db, username, password)
        # Read the attributes before _issue commits and expires the instance.
        return await cls._issue(db, user.id, user.uuid, user.username)

    @classmethod
    async def refresh(cls, db: AsyncSession, refresh_token: str) -> TokenResponse:
        user = await validate_refresh_token(db, refresh_token)
        if user is None:
            raise InvalidRefreshTokenError("Invalid or expired refresh token")

        if not user.is_active:
            # Rotate the token out so a deactivated account can't keep refreshing.
            await revoke_refresh_token(db, refresh_token)
            raise InactiveUserError("Inactive user")

        # Read what we need before the revoke commits and expires the instance.
        user_id, user_uuid, username = user.id, user.uuid, user.username
        await revoke_refresh_token(db, refresh_token)
        return await cls._issue(db, user_id, user_uuid, username)

    @staticmethod
    async def revoke(db: AsyncSession, refresh_token: str) -> None:
        await revoke_refresh_token(db, refresh_token)
