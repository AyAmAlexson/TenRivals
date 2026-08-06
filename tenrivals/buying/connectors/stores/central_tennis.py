"""Central Tennis (UK) — public + authenticated individual pricing.

Credentials (never logged or stored in DB):
  BUYING_CENTRAL_TENNIS_USERNAME
  BUYING_CENTRAL_TENNIS_PASSWORD
"""

from __future__ import annotations

import logging

from django.conf import settings

from buying.connectors.auth_session import (
    clear_session_cookies,
    load_session_cookies,
    save_session_cookies,
)
from buying.connectors.base import (
    AuthenticationResult,
    AuthenticationStatus,
    ConnectorCapabilities,
    NormalizedProductQuery,
    OfferData,
    SearchCandidate,
)
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.http import ConnectorHttpClient
from buying.connectors.registry import register_connector
import httpx

logger = logging.getLogger('buying')


@register_connector
class CentralTennisConnector(HtmlJsonLdConnector):
    code = 'central-tennis'
    parser_version = 'phase3-1'
    base_url = 'https://centraltennis.co.uk/'
    search_path_template = '/search?q={query}'
    default_currency = 'GBP'
    default_tax_mode = 'vat_included'
    default_destination_country = 'GB'
    capabilities = ConnectorCapabilities(
        search=True,
        product_details=True,
        variant_availability=True,
        destination_selection=False,
        tax_detection=True,
        promotion_detection=True,
        cart_simulation=False,
        shipping_calculation=False,
        authenticated_pricing=True,
    )

    def _credentials(self) -> tuple[str, str]:
        user = getattr(settings, 'BUYING_CENTRAL_TENNIS_USERNAME', '') or ''
        password = getattr(settings, 'BUYING_CENTRAL_TENNIS_PASSWORD', '') or ''
        return user.strip(), password

    def _client(self) -> ConnectorHttpClient:
        if self.http is not None:
            return self.http
        cookies = load_session_cookies(self.code)
        jar = httpx.Cookies()
        for name, value in cookies.items():
            jar.set(name, value)
        return ConnectorHttpClient(cookies=jar)

    def authentication_status(self) -> AuthenticationStatus:
        user, password = self._credentials()
        if not user or not password:
            return AuthenticationStatus(
                status='credentials_missing',
                message='Set BUYING_CENTRAL_TENNIS_* env vars',
            )
        if load_session_cookies(self.code):
            return AuthenticationStatus(status='authenticated', message='Session cookies present')
        return AuthenticationStatus(status='session_expired', message='No stored session')

    def login(self) -> AuthenticationResult:
        user, password = self._credentials()
        if not user or not password:
            return AuthenticationResult(status='credentials_missing', message='Credentials not configured')
        client = ConnectorHttpClient()
        from urllib.parse import urljoin
        login_url = urljoin(self.base_url, 'account/login')
        try:
            client.get(login_url)
            response = client.post(
                login_url,
                data={
                    'email': user,
                    'username': user,
                    'password': password,
                    'customer[email]': user,
                    'customer[password]': password,
                },
            )
            cookie_dict = {k: v for k, v in client.cookies.items()}
            if not cookie_dict:
                clear_session_cookies(self.code)
                return AuthenticationResult(
                    status='login_flow_changed',
                    message=f'No session cookies (HTTP {response.status_code})',
                )
            save_session_cookies(self.code, cookie_dict)
            return AuthenticationResult(
                status='authenticated',
                message='Session established',
                metadata={'cookie_count': len(cookie_dict), 'http_status': response.status_code},
            )
        except Exception as exc:
            logger.warning('Central Tennis login failed: %s', type(exc).__name__)
            return AuthenticationResult(status='credentials_invalid', message=type(exc).__name__)

    def refresh_session(self) -> AuthenticationResult:
        clear_session_cookies(self.code)
        return self.login()

    def get_product_details(self, candidate: SearchCandidate, query: NormalizedProductQuery) -> OfferData:
        offer = super().get_product_details(candidate, query)
        offer.public_price = offer.displayed_price
        auth = self.authentication_status()
        offer.authentication_status = auth.status
        if auth.status == 'credentials_missing':
            offer.warnings.append('Authenticated session unavailable — credentials missing')
            return self.apply_configured_destination(offer)
        if auth.status != 'authenticated':
            login_result = self.login()
            offer.authentication_status = login_result.status
            if login_result.status != 'authenticated':
                offer.warnings.append('Authenticated session expired')
                return self.apply_configured_destination(offer)
        try:
            client = self._client()
            response = client.get(candidate.url)
            response.raise_for_status()
            authed = self.parse_product_html(
                response.text,
                page_url=str(response.url),
                candidate=candidate,
                query=query,
            )
            offer.authenticated_price = authed.effective_price
            if offer.authenticated_price is not None:
                offer.effective_price = offer.authenticated_price
                offer.displayed_price = offer.authenticated_price
            offer.authentication_status = 'authenticated'
        except Exception as exc:
            offer.warnings.append(f'Authenticated price fetch failed: {type(exc).__name__}')
            offer.authentication_status = 'session_expired'
        return self.apply_configured_destination(offer)
