"""Acquisition attribution for retail customers (staff-only field ``source``)."""

from __future__ import annotations

from typing import Any

DEFAULT_ORGANIC_SOURCE = 'Organic Website'
SESSION_KEY = 'acquisition_attribution'

# Query params we persist when present on any page view.
_ATTR_KEYS = (
    'utm_source',
    'utm_medium',
    'utm_campaign',
    'utm_term',
    'utm_content',
    'gclid',
    'fbclid',
    'ref',
)


def capture_attribution_from_request(request) -> None:
    """If the request carries UTM/click ids, store them in the session (first-touch wins)."""
    if not hasattr(request, 'session'):
        return
    if request.session.get(SESSION_KEY):
        return
    found: dict[str, str] = {}
    get = getattr(request, 'GET', None)
    if get is None:
        return
    for key in _ATTR_KEYS:
        raw = (get.get(key) or '').strip()
        if raw:
            found[key] = raw[:200]
    if found:
        request.session[SESSION_KEY] = found


def format_attribution(attrs: dict[str, Any] | None) -> str:
    if not attrs:
        return ''
    utm_parts = []
    for key in ('utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'):
        val = (attrs.get(key) or '').strip()
        if val:
            utm_parts.append(val)
    if utm_parts:
        return ' / '.join(utm_parts)[:255]
    if attrs.get('gclid'):
        return 'Google Ads (gclid)'
    if attrs.get('fbclid'):
        return 'Facebook / Meta (fbclid)'
    ref = (attrs.get('ref') or '').strip()
    if ref:
        return f'ref: {ref}'[:255]
    return ''


def resolve_acquisition_source(request=None) -> str:
    """Source label for a self-serve signup / storefront customer create."""
    attrs = None
    if request is not None and hasattr(request, 'session'):
        attrs = request.session.get(SESSION_KEY)
    formatted = format_attribution(attrs if isinstance(attrs, dict) else None)
    return formatted or DEFAULT_ORGANIC_SOURCE


def apply_acquisition_source_to_customer(customer, request=None, *, only_if_empty: bool = True) -> bool:
    """Set ``customer.source`` from session attribution. Returns True if saved."""
    if customer is None:
        return False
    if only_if_empty and (customer.source or '').strip():
        return False
    source = resolve_acquisition_source(request)
    if (customer.source or '').strip() == source:
        return False
    customer.source = source
    customer.save(update_fields=['source', 'updated_at'])
    return True
