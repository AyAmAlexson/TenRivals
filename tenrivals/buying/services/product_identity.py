"""Product-page identity and grip verification after connector details fetch."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from buying.connectors.base import NormalizedProductQuery, OfferData, SearchCandidate
from buying.services.enrichment import (
    _detect_variant,
    _norm,
    _parse_head_size,
    _parse_pattern,
    _parse_weight,
    normalize_grip_size,
)
from buying.services.matching import MATCHING_RULES_VERSION, score_candidate

GRIP_STATUS_AVAILABLE = 'confirmed_available'
GRIP_STATUS_UNAVAILABLE = 'confirmed_unavailable'
GRIP_STATUS_UNKNOWN = 'unknown'


@dataclass
class GripVerification:
    status: str  # confirmed_available | confirmed_unavailable | unknown
    matched_labels: list[str] = field(default_factory=list)
    available_grips: list[str] = field(default_factory=list)
    note: str = ''


@dataclass
class IdentityVerification:
    match_status: str
    match_score: object
    details: dict
    page_text: str = ''
    specs_confirmed: dict = field(default_factory=dict)


def extract_page_text(html_or_offer) -> str:
    """Build a searchable blob from HTML string or OfferData."""
    if isinstance(html_or_offer, OfferData):
        parts = [html_or_offer.title or '']
        payload = html_or_offer.raw_payload if isinstance(html_or_offer.raw_payload, dict) else {}
        for key in ('page_text', 'body', 'description', 'specs_text'):
            val = payload.get(key)
            if isinstance(val, str):
                parts.append(val)
        for variant in html_or_offer.available_variants or []:
            if isinstance(variant, dict):
                parts.append(str(variant.get('label') or ''))
            else:
                parts.append(str(variant))
        # JSON-LD title / description
        jld = payload.get('json_ld')
        if isinstance(jld, dict):
            for key in ('name', 'description', 'title'):
                if jld.get(key):
                    parts.append(str(jld[key]))
        return ' '.join(p for p in parts if p)

    # HTML
    text = html_or_offer or ''
    # Strip scripts/styles coarsely
    text = re.sub(r'(?is)<script[^>]*>.*?</script>', ' ', text)
    text = re.sub(r'(?is)<style[^>]*>.*?</style>', ' ', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text[:50_000]


def collect_variant_labels(offer: OfferData) -> list[str]:
    labels: list[str] = []
    for variant in offer.available_variants or []:
        if isinstance(variant, dict):
            for key in ('label', 'title', 'name', 'option', 'grip', 'size'):
                val = variant.get(key)
                if val:
                    labels.append(str(val))
        else:
            labels.append(str(variant))
    payload = offer.raw_payload if isinstance(offer.raw_payload, dict) else {}
    for key in ('variant_labels', 'grip_options', 'options'):
        val = payload.get(key)
        if isinstance(val, list):
            labels.extend(str(x) for x in val if x)
    return labels


def verify_grip(offer: OfferData, wanted_grip: str) -> GripVerification:
    wanted = normalize_grip_size(wanted_grip or '')
    if not wanted:
        return GripVerification(status=GRIP_STATUS_AVAILABLE, note='No grip requested')

    labels = collect_variant_labels(offer)
    page = extract_page_text(offer)
    # Also scan option-like fragments in page for grip mentions
    page_hits = re.findall(
        r'(?:grip(?:\s*size)?|l)\s*[#:]?\s*[0-5]|l[0-5]|4\s*[-\s]?\s*1\s*/\s*2',
        page,
        flags=re.I,
    )
    candidates = list(labels) + page_hits
    normalized = []
    for label in candidates:
        g = normalize_grip_size(label)
        if re.fullmatch(r'L[0-5]', g or ''):
            normalized.append(g)

    uniq = sorted(set(normalized))
    if wanted in uniq:
        matched = [lab for lab in labels if normalize_grip_size(lab) == wanted]
        return GripVerification(
            status=GRIP_STATUS_AVAILABLE,
            matched_labels=matched or [wanted],
            available_grips=uniq,
        )
    if uniq:
        # Explicit grip options present but wanted missing
        return GripVerification(
            status=GRIP_STATUS_UNAVAILABLE,
            available_grips=uniq,
            note=f'{wanted} not in available grips {uniq}',
        )
    return GripVerification(
        status=GRIP_STATUS_UNKNOWN,
        available_grips=[],
        note='No grip options found on product page',
    )


def apply_grip_to_offer(offer: OfferData, query: NormalizedProductQuery) -> GripVerification:
    result = verify_grip(offer, query.grip_size or '')
    if result.status == GRIP_STATUS_AVAILABLE:
        offer.requested_variant_available = True
        # Drop generic "not verified" warnings
        offer.warnings = [
            w for w in (offer.warnings or [])
            if 'variant availability not verified' not in (w or '').lower()
        ]
    elif result.status == GRIP_STATUS_UNAVAILABLE:
        offer.requested_variant_available = False
        offer.warnings = list(offer.warnings or []) + [result.note or 'Requested grip unavailable']
    else:
        offer.requested_variant_available = None
        if query.grip_size:
            msg = 'Variant availability not verified on product page'
            if msg not in (offer.warnings or []):
                offer.warnings = list(offer.warnings or []) + [msg]
    return result


def rescore_with_product_page(
    query: NormalizedProductQuery,
    candidate: SearchCandidate,
    offer: OfferData,
    *,
    html: str = '',
) -> IdentityVerification:
    page_text = extract_page_text(html) if html else extract_page_text(offer)
    if not page_text:
        page_text = extract_page_text(offer)
    specs = {
        'head_size': _parse_head_size(page_text),
        'weight_g': _parse_weight(page_text),
        'string_pattern': _parse_pattern(page_text),
        'variant': _detect_variant(page_text),
    }
    raw = dict(candidate.raw_data or {})
    raw['page_text'] = page_text
    raw['specs_text'] = page_text[:4000]
    enriched = SearchCandidate(
        title=offer.title or candidate.title,
        url=candidate.url,
        price_preview=candidate.price_preview,
        currency=candidate.currency,
        supplier_sku=candidate.supplier_sku or offer.supplier_sku,
        manufacturer_code=candidate.manufacturer_code or offer.manufacturer_code,
        raw_data=raw,
    )
    verdict = score_candidate(query, enriched)
    details = dict(verdict.details)
    details['page_specs'] = specs
    details['matching_rules_version'] = MATCHING_RULES_VERSION
    return IdentityVerification(
        match_status=verdict.match_status,
        match_score=verdict.match_score,
        details=details,
        page_text=page_text,
        specs_confirmed={k: v for k, v in specs.items() if v not in (None, '')},
    )


def logic_versions(*, parser_version: str = '', prompt_version: str = '') -> dict:
    return {
        'matching_rules': MATCHING_RULES_VERSION,
        'parser_version': parser_version or '',
        'normalization_prompt': prompt_version or '',
        'enrichment': 'enrich-v1',
    }


def cache_logic_compatible(stored: dict | None, *, parser_version: str = '', prompt_version: str = '') -> bool:
    expected = logic_versions(parser_version=parser_version, prompt_version=prompt_version)
    stored = stored or {}
    for key, value in expected.items():
        if not value:
            continue
        if stored.get(key) != value:
            return False
    return True
