"""TikTok API error hierarchy."""


class TikTokError(Exception):
    """Base class for all tiktokrapi errors."""


class AuthError(TikTokError):
    """Session invalid, expired, or missing required cookies."""


class SignatureError(TikTokError):
    """Server rejected signed URL — likely stale signer or bad payload."""


class RateLimited(TikTokError):
    """TikTok rate-limited this account / IP."""


class NotFound(TikTokError):
    """Requested resource (user, video, comment) does not exist."""


class PrivateAccount(TikTokError):
    """Account is private and the session has no access."""


class CaptchaRequired(TikTokError):
    """TikTok demanded a captcha — must be solved manually or via solver."""


class LoginError(TikTokError):
    """Login flow failed. `code` and `surface_message` capture what TikTok said."""

    def __init__(self, message: str, *, code: str = "", surface_message: str = ""):
        super().__init__(message)
        self.code = code
        self.surface_message = surface_message


class LoginRateLimited(LoginError):
    """TikTok said 'Maximum number of attempts'. Wait + try later (typically 1-24h)."""


class WrongCredentials(LoginError):
    """Username/password mismatch."""


class AccountLocked(LoginError):
    """Account locked, suspended, or under review."""


class CaptchaSolverError(LoginError):
    """Captcha encountered but solver failed/missing."""


class OTPError(LoginError):
    """OTP screen shown but no poller / poller failed."""


class SignerError(TikTokError):
    """Node signer subprocess failed (crash, missing bundle, etc)."""


class UploadError(TikTokError):
    """Upload pipeline failed at some step."""


class InvalidResponse(TikTokError):
    """Server returned non-JSON or malformed response."""
