"""Typed connector failures — map to SupplierSearchRun / ConnectorStatus."""


class ConnectorError(Exception):
    """Base connector failure with a stable error_type for persistence."""

    error_type = 'connector_error'

    def __init__(self, message: str = '', *, metadata: dict | None = None):
        super().__init__(message or self.error_type)
        self.metadata = metadata or {}


class CaptchaDetected(ConnectorError):
    error_type = 'captcha_detected'


class RateLimited(ConnectorError):
    error_type = 'rate_limited'


class DestinationUnconfirmed(ConnectorError):
    error_type = 'destination_unconfirmed'


class AuthenticationFailed(ConnectorError):
    error_type = 'authentication_failed'


class CredentialsMissing(ConnectorError):
    error_type = 'credentials_missing'


class OAuthLoginRequired(ConnectorError):
    error_type = 'oauth_login_required'


class ParsingError(ConnectorError):
    error_type = 'parsing_error'


class ProductNotFound(ConnectorError):
    error_type = 'product_not_found'


class CapabilityNotSupported(ConnectorError):
    error_type = 'not_supported'


class ConnectorNotReady(ConnectorError):
    error_type = 'connector_not_ready'
