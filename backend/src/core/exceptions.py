class ServiceError(Exception):
    """Base class for errors the router layer maps onto HTTP responses."""


class ConflictError(ServiceError):
    """A unique field (username, email, group name) is already taken."""


class UnknownReferenceError(ServiceError):
    """A referenced resource does not exist."""


class ValidationError(ServiceError):
    """Submitted data failed domain validation (bad column config, record value, etc.)."""


class InvalidCredentialsError(ServiceError):
    """The username does not exist or the password does not match."""


class InactiveUserError(ServiceError):
    """The credentials were valid but the account is deactivated."""


class InvalidRefreshTokenError(ServiceError):
    """The refresh token is unknown, revoked, or expired."""
