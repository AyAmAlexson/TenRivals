"""ITF Tennis Point — authenticated pricing connector.

Credentials (never logged or stored in DB):
  BUYING_ITF_TENNIS_POINT_USERNAME
  BUYING_ITF_TENNIS_POINT_PASSWORD
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
from buying.connectors.exceptions import CredentialsMissing
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.http import ConnectorHttpClient
from buying.connectors.registry import register_connector
import httpx

logger = logging.getLogger('buying')


@register_connector
class ItfTennisPointConnector(HtmlJsonLdConnector):
    code = 'itf-tennis-point'
    parser_version = 'phase3-2'
    base_url = 'https://www.itf-tennis-point.com/itf/'
    search_path_template = '/search?q={query}'
    default_currency = 'EUR'
    default_tax_mode = 'vat_included'
    default_destination_country = 'DE'
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
        user = getattr(settings, 'BUYING_ITF_TENNIS_POINT_USERNAME', '') or ''
        password = getattr(settings, 'BUYING_ITF_TENNIS_POINT_PASSWORD', '') or ''
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
            return AuthenticationStatus(status='credentials_missing', message='Set BUYING_ITF_TENNIS_POINT_* env vars')
        cookies = load_session_cookies(self.code)
        if cookies:
            return AuthenticationStatus(status='authenticated', message='Session cookies present')
        return AuthenticationStatus(status='session_expired', message='No stored session — login required')

    def login(self) -> AuthenticationResult:
        user, password = self._credentials()
        if not user or not password:
            return AuthenticationResult(status='credentials_missing', message='Credentials not configured')
        client = ConnectorHttpClient()
        login_url = urljoin_safe(self.base_url, 'login')
        try:
            # Probe login page; site-specific form posts vary — keep resilient.
            page = client.get(login_url)
            # Attempt common form field names without logging values.
            response = client.post(
                login_url,
                data={
                    'email': user,
                    'username': user,
                    'password': password,
                    'login[email]': user,
                    'login[password]': password,
                },
            )
            cookie_dict = {k: v for k, v in client.cookies.items()}
            if not cookie_dict or response.status_code >= 500:
                clear_session_cookies(self.code)
                return AuthenticationResult(
                    status='login_flow_changed',
                    message=f'Login did not establish a session (HTTP {response.status_code})',
                )
            save_session_cookies(self.code, cookie_dict)
            # Never include credentials or raw cookies in metadata exposed to UI beyond counts.
            return AuthenticationResult(
                status='authenticated',
                message='Session established',
                metadata={'cookie_count': len(cookie_dict), 'http_status': response.status_code},
            )
        except Exception as exc:
            logger.warning('ITF Tennis Point login failed: %s', type(exc).__name__)
            return AuthenticationResult(status='credentials_invalid', message=type(exc).__name__)

    def refresh_session(self) -> AuthenticationResult:
        clear_session_cookies(self.code)
        return self.login()

    def search(self, query: NormalizedProductQuery) -> list[SearchCandidate]:
        user, password = self._credentials()
        if not user or not password:
            raise CredentialsMissing(
                'ITF Tennis Point requires authentication — set BUYING_ITF_TENNIS_POINT_* env vars'
            )
        # Ensure session before search
        auth = self.authentication_status()
        if auth.status != 'authenticated':
            self.login()
        phrase = (query.search_phrases or ['tennis'])[0]
        from buying.connectors.shopify import search_shopify
        found = search_shopify(self._client(), base_url=self.base_url, phrase=phrase)
        return found or super().search(query)

    def get_product_details(self, candidate: SearchCandidate, query: NormalizedProductQuery) -> OfferData:
        # Public parse first
        offer = super().get_product_details(candidate, query)
        offer.public_price = offer.displayed_price
        auth = self.authentication_status()
        offer.authentication_status = auth.status
        if auth.status == 'credentials_missing':
            offer.warnings.append('Authenticated session unavailable — credentials missing')
            return offer
        if auth.status != 'authenticated':
            login_result = self.login()
            offer.authentication_status = login_result.status
            if login_result.status != 'authenticated':
                offer.warnings.append('Authenticated session expired')
                return offer
        # Re-fetch with authenticated client
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
            if offer.public_price and offer.authenticated_price is not None:
                # Prefer authenticated when confirmed
                offer.effective_price = offer.authenticated_price
                offer.displayed_price = offer.authenticated_price
            offer.authentication_status = 'authenticated'
        except Exception as exc:
            offer.warnings.append(f'Authenticated price fetch failed: {type(exc).__name__}')
            offer.authentication_status = 'session_expired'
        return self.apply_configured_destination(offer)


def urljoin_safe(base: str, path: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base.rstrip('/') + '/', path.lstrip('/'))
