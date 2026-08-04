from pydantic import model_validator
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

    # Login throttling — see src/auth/tokens/throttle.py for what these buy and
    # for the single-instance assumption underneath them. Failures only: a
    # caller typing the right password is never turned away, and a success
    # clears that username's tally.
    LOGIN_THROTTLE_WINDOW_MINUTES: int = 15
    # Per username. This is the one that stops guessing at a named account, and
    # the one that cannot be evaded, since the username is the thing being
    # attacked. The cost of it is the classic lockout: someone can spend wrong
    # passwords to keep a real person out for the length of the window.
    LOGIN_MAX_FAILURES_PER_USERNAME: int = 10
    # Per source address, to catch one host spraying across many usernames.
    # Best-effort, because the address is read from X-Forwarded-For: if the
    # deployment ever puts a proxy in front of the API that does *not* forward
    # the original client address, every user arrives as one address and shares
    # this bucket. Generous for that reason. 0 disables it and leaves the
    # per-username limit doing the work.
    LOGIN_MAX_FAILURES_PER_IP: int = 50

    # Above this many records, re-indexing a list's calculated columns is handed to a
    # background task and the request answers at once. Below it the work happens in the
    # request, so the response is already correct when it goes out — which is every list
    # in a deployment this size, and why the deferred path is rarely reached.
    #
    # The threshold decides who waits, **not** how much work is done: a deferred run
    # still walks the whole list. Stopping partway would leave the index holding two
    # formulas' answers at once, which filters incoherently — strictly worse than one
    # that is uniformly a version behind. Measured at ~0.035 ms of app CPU per record,
    # so this threshold is ~0.2 s of CPU — under two seconds of wall time even on a
    # 0.1-CPU instance.
    FORMULA_INLINE_MAX_RECORDS: int = 5_000

    @model_validator(mode="after")
    def _auth_may_only_be_disabled_in_debug(self) -> "Settings":
        """DISABLE_AUTH is a development shortcut and a production disaster.

        It does not merely skip the token check: get_current_user resolves the
        caller to the *first row* of the users table, which is the seeded
        superuser. Set on a public deployment it hands every route to anyone who
        can reach the URL, with no credential at all and nothing in the logs to
        distinguish them from the real administrator.

        A single mistyped environment variable is all that stands between the
        two, so refuse to construct at all rather than boot into it. Pairing it
        with DEBUG means turning it on is a deliberate two-flag act.
        """
        if self.DISABLE_AUTH and not self.DEBUG:
            raise ValueError(
                "DISABLE_AUTH=True requires DEBUG=True. It resolves every "
                "request to the first user in the database — the superuser — "
                "without a token, so it is only ever safe on a development "
                "machine. Unset it, or set DEBUG=True if this really is one."
            )
        return self

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
