"""StrengthLog exceptions."""


class StrengthLogError(Exception):
    """Base exception for StrengthLog errors."""
    pass


class AuthenticationError(StrengthLogError):
    """Raised when authentication fails."""
    pass


class APIError(StrengthLogError):
    """Raised when an API call fails."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class TokenExpiredError(AuthenticationError):
    """Raised when the authentication token has expired."""
    pass
