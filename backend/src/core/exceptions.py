class ServiceError(Exception):
    """Base class for errors the router layer maps onto HTTP responses."""


class ConflictError(ServiceError):
    """A unique field (username, email, group name) is already taken."""


class UnknownReferenceError(ServiceError):
    """A referenced resource does not exist."""


class ValidationError(ServiceError):
    """Submitted data failed domain validation (bad column config, record value, etc.)."""


class FeatureDisabledError(ServiceError):
    """The operation is built and correct-looking but switched off (maps to 503).

    Distinct from `ValidationError` on purpose: nothing about the request is wrong,
    and nothing the caller changes about it will help, so a 400 would send them
    hunting for a mistake they did not make.
    """


class PermissionDeniedError(ServiceError):
    """The caller lacks the required permission on a resource (maps to 403)."""


class InvalidCredentialsError(ServiceError):
    """The username does not exist or the password does not match."""


class InactiveUserError(ServiceError):
    """The credentials were valid but the account is deactivated."""


class InvalidRefreshTokenError(ServiceError):
    """The refresh token is unknown, revoked, or expired."""
