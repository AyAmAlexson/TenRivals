"""Honest connector readiness matrix (not the same as HTTP health_ok).

A connector is end-to-end operational only when it can:
  search → identify candidate → open product page → parse price → verify variant.

Statuses used in the matrix:
  ok | partial | missing | blocked | unsupported | credentials_required
"""

from __future__ import annotations

# Per-supplier readiness: health_ok / search_ok / product_parse_ok /
# variant_parse_ok / checkout_context_ok
CONNECTOR_READINESS: dict[str, dict] = {
    'tennis-warehouse-eu': {
        'health_ok': 'ok',
        'search_ok': 'partial',  # custom search; Accept headers required (was 406)
        'product_parse_ok': 'ok',
        'variant_parse_ok': 'partial',
        'checkout_context_ok': 'missing',
        'notes': 'Best Phase-3 HTML connector; grip options parsed when present',
        'tier': 'partial_e2e',
    },
    'tennis-warehouse-us': {
        'health_ok': 'partial',
        'search_ok': 'blocked',
        'product_parse_ok': 'blocked',
        'variant_parse_ok': 'blocked',
        'checkout_context_ok': 'blocked',
        'notes': 'Attempts browser-like search; often HTTP 403 from datacenter IPs — manual fallback',
        'tier': 'anti_bot_blocked',
    },
    'tennis-nuts': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'notes': 'Search path fixed to /shop/catalogsearch/result/',
        'tier': 'search_only',
    },
    'ole-tennis': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'notes': 'Shopify products.json search',
        'tier': 'search_only',
    },
    'central-tennis': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'partial',
        'notes': 'Public price OK; auth price needs credentials',
        'tier': 'auth_partial',
    },
    'itf-tennis-point': {
        'health_ok': 'ok',
        'search_ok': 'credentials_required',
        'product_parse_ok': 'credentials_required',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'credentials_required',
        'notes': 'Auth required; empty search is search_empty when BUYING_ITF_* vars are set',
        'tier': 'authenticated_blocked',
    },
    'tennis-point-de': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'notes': 'Shopify suggest/products.json + HTML fallback',
        'tier': 'search_only',
    },
    'tennis-point-com': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'notes': 'Shopify search; marketing Tour racket handled in matching',
        'tier': 'search_only',
    },
    'midwest-racquet-sports': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'notes': 'Shopify products.json',
        'tier': 'search_only',
    },
    'holabird-sports': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'notes': 'Shopify products.json; variant verify missing',
        'tier': 'search_only',
    },
    'saburi-sports': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'notes': 'Shopify products.json; variant verify missing',
        'tier': 'search_only',
    },
    'prodirect-sport': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'notes': 'Generic HTML',
        'tier': 'search_only',
    },
    'm1-tennis': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'tier': 'search_only',
    },
    'extreme-tennis': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'tier': 'search_only',
    },
    'passa-sports': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'tier': 'search_only',
    },
    'tennispro-eu': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'tier': 'search_only',
    },
    'smashinn': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'tier': 'search_only',
    },
    'mister-tennis': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'tier': 'search_only',
    },
    'tennis-express': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'tier': 'search_only',
    },
    'direct-tennis': {
        'health_ok': 'ok',
        'search_ok': 'partial',
        'product_parse_ok': 'partial',
        'variant_parse_ok': 'missing',
        'checkout_context_ok': 'missing',
        'tier': 'search_only',
    },
}


def readiness_for(code: str) -> dict:
    return CONNECTOR_READINESS.get(code, {
        'health_ok': 'unknown',
        'search_ok': 'unknown',
        'product_parse_ok': 'unknown',
        'variant_parse_ok': 'unknown',
        'checkout_context_ok': 'unknown',
        'notes': 'Not catalogued',
        'tier': 'unknown',
    })


def e2e_operational_codes() -> list[str]:
    return [c for c, m in CONNECTOR_READINESS.items() if m.get('tier') == 'e2e']


def tier_summary() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for code, meta in CONNECTOR_READINESS.items():
        out.setdefault(meta.get('tier', 'unknown'), []).append(code)
    return out
