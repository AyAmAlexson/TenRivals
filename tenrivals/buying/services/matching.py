"""Deterministic product matching (Phase 3 — no LLM)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from buying.connectors.base import NormalizedProductQuery, SearchCandidate


def _norm(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if len(t) > 1}


@dataclass
class MatchVerdict:
    match_status: str
    match_score: Decimal
    details: dict


def score_candidate(query: NormalizedProductQuery, candidate: SearchCandidate) -> MatchVerdict:
    details: dict = {'signals': []}
    score = Decimal('0')

    title = _norm(candidate.title)
    q_brand = _norm(query.brand)
    q_model = _norm(query.model_name)
    q_code = _norm(query.manufacturer_code)

    if query.ean and candidate.raw_data.get('ean') == query.ean:
        score += Decimal('0.45')
        details['signals'].append('ean')
    if query.upc and candidate.raw_data.get('upc') == query.upc:
        score += Decimal('0.45')
        details['signals'].append('upc')
    if q_code and (q_code in title or q_code == _norm(candidate.manufacturer_code or '')):
        score += Decimal('0.30')
        details['signals'].append('manufacturer_code')
    if candidate.supplier_sku and query.manufacturer_code:
        if _norm(candidate.supplier_sku) == q_code:
            score += Decimal('0.15')
            details['signals'].append('sku')

    brand_ok = (not q_brand) or (q_brand in title)
    model_ok = (not q_model) or all(t in _tokens(title) for t in _tokens(q_model) if len(t) > 2)
    if brand_ok and q_brand:
        score += Decimal('0.15')
        details['signals'].append('brand')
    if model_ok and q_model:
        score += Decimal('0.25')
        details['signals'].append('model')

    # Soft title overlap
    overlap = _tokens(title) & (_tokens(q_brand) | _tokens(q_model))
    if overlap:
        score += min(Decimal('0.10'), Decimal('0.02') * len(overlap))

    if score >= Decimal('0.70') and brand_ok and model_ok:
        status = 'exact'
    elif score >= Decimal('0.45'):
        status = 'manual_review'
        if query.color and query.color.lower() not in title:
            status = 'alternative_color'
        elif query.generation and query.generation.lower() not in title:
            status = 'alternative_version'
    elif score >= Decimal('0.25'):
        status = 'manual_review'
    else:
        status = 'no_match'

    score = min(score, Decimal('1.00')).quantize(Decimal('0.01'))
    details['score'] = str(score)
    return MatchVerdict(match_status=status, match_score=score, details=details)


def pick_best_candidates(
    query: NormalizedProductQuery,
    candidates: list[SearchCandidate],
    *,
    limit: int = 5,
) -> list[tuple[SearchCandidate, MatchVerdict]]:
    scored = [(c, score_candidate(query, c)) for c in candidates]
    scored = [(c, v) for c, v in scored if v.match_status != 'no_match']
    scored.sort(key=lambda pair: pair[1].match_score, reverse=True)
    return scored[:limit]


def query_from_normalized(np) -> NormalizedProductQuery:
    aliases = []
    raw_aliases = getattr(np, 'aliases', None) or []
    if isinstance(raw_aliases, list):
        aliases.extend(str(x) for x in raw_aliases if x)
    raw = getattr(np, 'raw_ai_output', None) or {}
    if isinstance(raw, dict):
        for key in ('aliases', 'aka', 'search_aliases'):
            val = raw.get(key)
            if isinstance(val, list):
                aliases.extend(str(x) for x in val)
            elif isinstance(val, str) and val.strip():
                aliases.append(val.strip())
    # unique preserve order
    seen = set()
    uniq = []
    for a in aliases:
        a = a.strip()
        if a and a.lower() not in seen:
            seen.add(a.lower())
            uniq.append(a)
    return NormalizedProductQuery(
        brand=np.brand or '',
        model_name=np.model_name or '',
        category=np.category or '',
        manufacturer_code=getattr(np, 'manufacturer_code', '') or '',
        ean=getattr(np, 'ean', '') or '',
        upc=getattr(np, 'upc', '') or '',
        color=getattr(np, 'color', '') or '',
        size=getattr(np, 'size', '') or '',
        grip_size=getattr(np, 'grip_size', '') or '',
        court=getattr(np, 'court', '') or '',
        gender=getattr(np, 'gender', '') or '',
        generation=getattr(np, 'generation', '') or '',
        aliases=tuple(uniq),
        quantity=getattr(np, 'quantity', 1) or 1,
        raw_attributes=raw if isinstance(raw, dict) else {},
    )
