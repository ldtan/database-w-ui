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

    # The largest list whose calculated columns are recomputed *inside* the request
    # that changed a formula. Above this the work is handed to a background task and
    # the columns report `recompute_pending` until it lands (see
    # src/lists/columns/backfill.py).
    #
    # A tuning knob, not a policy one, and the number to move is the one on the left
    # of "is anyone waiting for this?". At ~0.11 ms of app CPU per record, 5,000 is
    # about 5 s of PATCH on a 0.1-CPU instance and ~1 s on a 0.5. Raise it and more
    # edits finish before the response, at the cost of a longer save; lower it and
    # saves are quicker but the "values catching up" warning shows up on smaller
    # lists. It deliberately does not cap the *work* — a deferred backfill still does
    # the whole list, because a half-updated column holds two formulas' answers at
    # once and is worse than a uniformly stale one.
    FORMULA_INLINE_MAX_RECORDS: int = 5_000

    # Whether a column's values may be converted — both endings, retyping the
    # column in place and duplicating it into a new one. Off, because the cutover
    # loses writes: the backfill copies each cell into a hidden column, and the
    # commit swaps the two keys back unconditionally, so a record edited between
    # the two reverts to its pre-edit value and a record *created* between them
    # has its cell nulled, since the swap reads a key the backfill never wrote.
    # The window is not a race but the whole interval a person spends reading the
    # report, which is the feature's entire selling point. See known-gaps.md §11.
    #
    # A duplicate does not swap, so it cannot lose a source value — but it is the
    # same two-step flow through the same stale scratch column, so a copy made
    # while the report was open silently misses those same edits. One switch
    # rather than two: they are one feature to the person using them, and half of
    # it working is harder to explain than none of it.
    #
    # A policy switch, not a tuning knob: turn it back on when the commit catches
    # up records touched since the backfill (`list_items.updated_at` is already
    # there for it), not because a particular list looks quiet.
    COLUMN_CONVERSION_ENABLED: bool = False
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
