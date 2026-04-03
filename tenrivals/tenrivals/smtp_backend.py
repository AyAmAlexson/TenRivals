"""
SMTP backend for implicit TLS (port 465) that uses certifi's CA bundle.

Django's default ssl.create_default_context() can fail on some hosts (e.g. macOS
Python) with CERTIFICATE_VERIFY_FAILED. Optional insecure mode for broken
corporate SMTP chains — set EMAIL_SMTP_ALLOW_UNVERIFIED_SSL=true (last resort).
"""
import ssl

import certifi
from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend
from django.utils.functional import cached_property


class FlexibleSSLEmailBackend(EmailBackend):
    @cached_property
    def ssl_context(self):
        if getattr(settings, "EMAIL_SMTP_ALLOW_UNVERIFIED_SSL", False):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        if self.ssl_certfile or self.ssl_keyfile:
            return super().ssl_context
        return ssl.create_default_context(cafile=certifi.where())
