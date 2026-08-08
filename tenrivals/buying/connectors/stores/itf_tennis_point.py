"""ITF Tennis Point — authenticated B2B portal (Azure AD B2C OAuth).

Credentials (never logged or stored in DB):
  BUYING_ITF_TENNIS_POINT_USERNAME
  BUYING_ITF_TENNIS_POINT_PASSWORD

The storefront redirects anonymous traffic to login.itftennis.com (Azure B2C).
Simple form POST login does not work — OAuth/Playwright is required (Phase 6).
Until then we surface a clear oauth_login_required error instead of a silent
empty search mislabeled as credentials_missing.
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
    HealthCheckResult,
    NormalizedProductQuery,
    OfferData,
    SearchCandidate,
)
from buying.connectors.exceptions import ConnectorError, CredentialsMissing, OAuthLoginRequired
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.http import ConnectorHttpClient
from buying.connectors.registry import register_connector
from buying.connectors.shopify import BROWSER_UA, fetch_shopify_product, offer_from_shopify_product
import httpx

logger = logging.getLogger('buying')


@register_connector
class ItfTennisPointConnector(HtmlJsonLdConnector):
    code = 'itf-tennis-point'
    parser_version = 'phase3-3'
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
        return ConnectorHttpClient(cookies=jar, user_agent=BROWSER_UA)

    def health_check(self) -> HealthCheckResult:
        # Homepage always 302s to Azure B2C when unauthenticated — treat redirect host as signal.
        client = self._client()
        try:
            response = client.get(self.base_url)
            final = str(response.url)
            if 'login.itftennis.com' in final or 'oauth2' in final.lower():
                return HealthCheckResult(
                    status='failed',
                    response_time_ms=getattr(response, '_buying_elapsed_ms', 0),
                    http_status=response.status_code,
                    checked_url=self.base_url,
                    error_type='oauth_login_required',
                    error_message='ITF Tennis Point requires Azure AD B2C login (not form POST)',
                )
            status = 'available' if response.status_code < 400 else 'failed'
            return HealthCheckResult(
                status=status,
                response_time_ms=getattr(response, '_buying_elapsed_ms', 0),
                http_status=response.status_code,
                checked_url=self.base_url,
            )
        except Exception as exc:
            return HealthCheckResult(
                status='failed',
                checked_url=self.base_url,
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
            )

    def authentication_status(self) -> AuthenticationStatus:
        user, password = self._credentials()
        if not user or not password:
            return AuthenticationStatus(status='credentials_missing', message='Set BUYING_ITF_TENNIS_POINT_* env vars')
        cookies = load_session_cookies(self.code)
        if cookies:
            return AuthenticationStatus(status='authenticated', message='Session cookies present')
        return AuthenticationStatus(
            status='session_expired',
            message='Azure AD B2C session required — form login is not supported',
        )

    def login(self) -> AuthenticationResult:
        user, password = self._credentials()
        if not user or not password:
            return AuthenticationResult(status='credentials_missing', message='Credentials not configured')
        # Probe whether a stored session still reaches the storefront.
        client = self._client()
        try:
            response = client.get(self.base_url)
            final = str(response.url)
            if 'login.itftennis.com' in final or 'oauth2' in final.lower():
                clear_session_cookies(self.code)
                return AuthenticationResult(
                    status='oauth_login_required',
                    message=(
                        'ITF Tennis Point uses Azure AD B2C OAuth. '
                        'Automated form login is not supported yet — use manual offer entry '
                        'or Playwright OAuth (Phase 6).'
                    ),
                )
            if response.status_code < 400 and 'itf-tennis-point.com' in final:
                cookie_dict = {k: v for k, v in client.cookies.items()}
                if cookie_dict:
                    save_session_cookies(self.code, cookie_dict)
                return AuthenticationResult(status='authenticated', message='Existing session still valid')
        except Exception as exc:
            logger.warning('ITF Tennis Point session probe failed: %s', type(exc).__name__)
        return AuthenticationResult(
            status='oauth_login_required',
            message='ITF Tennis Point requires Azure AD B2C OAuth (Playwright Phase 6)',
        )

    def refresh_session(self) -> AuthenticationResult:
        clear_session_cookies(self.code)
        return self.login()

    def search(self, query: NormalizedProductQuery) -> list[SearchCandidate]:
        user, password = self._credentials()
        if not user or not password:
            raise CredentialsMissing(
                'ITF Tennis Point requires authentication — set BUYING_ITF_TENNIS_POINT_* env vars'
            )
        auth = self.authentication_status()
        if auth.status != 'authenticated':
            login_result = self.login()
            if login_result.status != 'authenticated':
                raise OAuthLoginRequired(
                    login_result.message
                    or 'ITF Tennis Point requires Azure AD B2C OAuth login'
                )
        phrase = (query.search_phrases or ['tennis'])[0]
        from buying.connectors.shopify import search_shopify_multi
        found = search_shopify_multi(
            self._client(),
            base_url=self.base_url,
            phrases=list(query.search_phrases or [phrase]),
        )
        if found:
            return found
        # If we somehow have a session but search is empty, return empty (not credentials_missing).
        return super().search(query)

    def get_product_details(self, candidate: SearchCandidate, query: NormalizedProductQuery) -> OfferData:
        client = self._client()
        product = fetch_shopify_product(client, product_url=candidate.url)
        if product:
            try:
                offer = offer_from_shopify_product(
                    product,
                    page_url=str(candidate.url).split('?')[0],
                    default_currency=self.default_currency,
                    default_tax_mode=self.default_tax_mode,
                    default_destination_country=self.default_destination_country,
                    parser_version=self.parser_version,
                    wanted_grip=query.grip_size or query.size or '',
                )
                offer.authentication_status = self.authentication_status().status
                return self.apply_configured_destination(offer)
            except ValueError:
                pass
        offer = super().get_product_details(candidate, query)
        offer.public_price = offer.displayed_price
        auth = self.authentication_status()
        offer.authentication_status = auth.status
        return self.apply_configured_destination(offer)


def urljoin_safe(base: str, path: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base.rstrip('/') + '/', path.lstrip('/'))
