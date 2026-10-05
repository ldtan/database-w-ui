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

    # Address search for `location` columns, proxied through the API so the
    # deployment has one identity, one rate limit and one cache — none of which a
    # browser can provide. See src/geocode/services.py.
    #
    # Empty disables it, and that is a supported way to run: a location column
    # works by typing an address and dropping a pin, so the search box simply says
    # it is unavailable rather than the field breaking.
    GEOCODE_URL: str = "https://nominatim.openstreetmap.org/search"
    # Nominatim's policy asks to be able to contact whoever is calling. A
    # deployment that leaves this at the default is anonymous to them, which is
    # exactly the thing that gets a User-Agent blocked — set it.
    GEOCODE_USER_AGENT: str = "database-w-ui (set GEOCODE_USER_AGENT)"
    # One call a second is the published limit. Enforced deployment-wide by a
    # semaphore, on the same single-instance assumption as the login throttle.
    GEOCODE_MIN_INTERVAL_SECONDS: float = 1.0
    GEOCODE_TIMEOUT_SECONDS: float = 5.0

    # How many rows one import may carry — the list importer and the user batch
    # importer both read this. A policy decision rather than a tuning knob: every row
    # is validated and inserted through the ordinary service, several round trips
    # apiece, so what this really sets is how long one import may occupy the single
    # worker. Someone whose spreadsheets are routinely larger has a fair reason to
    # raise it and no need to read the code to decide.
    #
    # Both importers check it before writing anything, so whatever it is set to, a
    # file over the limit imports nothing rather than part of itself.
    IMPORT_MAX_ROWS: int = 1_000

    # What an uploaded file may weigh. Belongs to the instance rather than to the
    # feature: the file is read whole, and a JSON one is then parsed whole again at
    # several times its size on the wire, so the ceiling that matters is available
    # memory. 8 MB suits the 512 MB instance this runs on, and a larger plan should
    # not need a code change to use what it paid for.
    MAX_UPLOAD_BYTES: int = 8 * 1024 * 1024

    # The ceiling on a whole request body, which is a different question from the one
    # above and needs a different number.
    #
    # `MAX_UPLOAD_BYTES` bounds the *file*, is checked by the route, and produces a
    # message naming the limit — that is the good error and it should keep happening.
    # This bounds what the process will buffer *at all*, and exists because nothing
    # did: `ListItemCreate.data` is a `dict` to Pydantic and a `text` column's
    # `max_length` is unset by default, so a single JSON body could be tens of
    # megabytes.
    #
    # Deliberately **above** the upload limit rather than equal to it. A multipart
    # upload carries boundaries and headers around the file, so a request holding a
    # file exactly at `MAX_UPLOAD_BYTES` is a body slightly over it — set the two
    # equal and the backstop would start refusing files the route was about to accept,
    # with the worse message. The gap is the envelope; it is not a second budget.
    MAX_REQUEST_BYTES: int = 12 * 1024 * 1024

    # Records one bulk record body may name — the delete, archive and unarchive
    # routes all read it, since each does one UPDATE over the uuids given and there
    # is no reason for them to disagree about how many is too many.
    #
    # Unlike the two above, this one is also a published API limit — it reaches the
    # OpenAPI schema through `BulkDeleteRequest` and its siblings, and the frontend
    # keeps its own copy to chunk a large selection against (a static build cannot
    # read this). So it may be raised freely, but lowering it below the client's 500
    # would produce a validation error nobody could act on; change `BULK_MAX` in
    # frontend/src/lib/limits.ts first if it ever needs to go down.
    BULK_DELETE_MAX: int = 500

    # "Live records": an open list subscribes to GET /lists/{uuid}/events and
    # refetches when it is told something changed, instead of polling on a timer.
    # See src/lists/events/ and docs/planned-features.md #8.
    #
    # False makes the route answer 503, which the client reads as "fall back to
    # polling" — so this is the kill switch, and it is deliberately here rather
    # than in the SPA's build-time config: a Vite variable needs a rebuild and a
    # redeploy of the static site, where this is an environment change on the API.
    SSE_ENABLED: bool = True

    # How long one stream may live before the server closes it and the client
    # reconnects. A stream is deliberately finite, and the number is doing four
    # jobs at once:
    #
    #   - An access token lasts ACCESS_TOKEN_EXPIRE_MINUTES. Authorization is
    #     checked at connect and never again, so a stream that outlived its token
    #     would be a session with no expiry.
    #   - **It is the revocation window.** Losing `view` on the list, or a grant
    #     expiring, stops the signals reaching a subscriber at their next connect
    #     and not before. Nothing leaks meanwhile — the events carry no data and
    #     every refetch they trigger is separately permissioned — but the tap
    #     closes late, and this is how late.
    #   - Graceful shutdown: uvicorn waits for in-flight requests, and a stream is
    #     one. The shutdown event in src/lists/events/broker.py ends a stream
    #     immediately; this bounds the case where that is not reached.
    #   - Proxies cap connection age on their own schedules. Reconnecting on ours
    #     makes that ordinary rather than a symptom.
    #
    # Lower is safer and costs one request per stream per interval; higher is
    # cheaper and widens the revocation window by exactly the same amount.
    SSE_MAX_STREAM_SECONDS: int = 300

    # How often an idle stream emits a comment frame. Two jobs: proxies reap a
    # connection that has said nothing (Render's static-site rewrite sits in front
    # of the API, and its timeout is not ours to set), and a write to a peer that
    # has gone away is how the server learns to drop the subscriber — without
    # traffic, an abandoned connection is indistinguishable from a quiet one.
    SSE_HEARTBEAT_SECONDS: int = 15

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
