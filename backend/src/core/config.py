from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    SECRET_KEY: str
    AUTH_SALT: str
    RSA_PRIVATE_KEY_PATH: str
    RSA_PUBLIC_KEY_PATH: str
    JWT_ALGORITHM: str = "RS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    API_PREFIX: str = "/api"
    DEBUG: bool = False
    ALLOWED_ORIGINS: str = ""
    DATABASE_URI: str
    FIRST_SUPERUSER_EMAIL: str = "admin@example.com"
    FIRST_SUPERUSER_PASSWORD: str = "changeme"
    FIRST_SUPERUSER_USERNAME: str = "admin"
    DISABLE_AUTH: bool = False
    # How long a soft-deleted record is kept before the purge job may destroy it.
    # This is the window in which a delete can still be undone, so it is a policy
    # decision, not a tuning knob. 0 means "purge as soon as the job runs".
    PURGE_DELETED_AFTER_DAYS: int = 30

    @property
    def allowed_origins(self) -> list[str]:
        """Parse the comma-separated ALLOWED_ORIGINS env value into a list."""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True
    )


settings = Settings()
