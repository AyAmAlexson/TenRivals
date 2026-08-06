"""Deterministic product matching (Phase 3 — no LLM).

Uses enriched racquet specs (head size, weight, pattern, generation, variant)
as hard constraints so Team/Lite/wrong weight offers are rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from buying.connectors.base import NormalizedProductQuery, SearchCandidate
from buying.services.enrichment import (
    VARIANT_TOKENS,
    _norm,
    _parse_head_size,
    _parse_pattern,
    _parse_weight,
    normalize_grip_size,
)


@dataclass
class MatchVerdict:
    match_status: str
    match_score: Decimal
    details: dict


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if len(t) > 1}


def _title_variant(title: str) -> str:
    n = _norm(title)
    for token in ('super lite', 'superlite', 'junior', 'team', 'lite', 'tour', 'plus'):
        if ' ' in token or token == 'superlite':
            if token in n or (token == 'superlite' and 'super lite' in n):
                return 'Super Lite'
            continue
        if token in n.split():
            return 'Junior' if token == 'junior' else token.title()
    if 'jr' in n.split():
        return 'Junior'
    return ''


def score_candidate(query: NormalizedProductQuery, candidate: SearchCandidate) -> MatchVerdict:
    details: dict = {'signals': [], 'hard_mismatches': []}
    score = Decimal('0')
    title = candidate.title or ''
    title_n = _norm(title)
    attrs = query.raw_attributes if isinstance(query.raw_attributes, dict) else {}

    q_brand = _norm(query.brand)
    q_model = _norm(query.model_name)
    q_code = _norm(query.manufacturer_code)
    q_gen = _norm(query.generation)
    q_variant = _norm(str(attrs.get('variant') or ''))
    q_head = str(attrs.get('head_size') or '').strip()
    q_weight = attrs.get('weight_g')
    q_pattern = _norm(str(attrs.get('string_pattern') or '')).replace(' ', '')

    # --- Hard racquet mismatches ---
    if query.category == 'racquet' or attrs.get('category') == 'racquet' or q_model:
        cand_variant = _norm(_title_variant(title))
        if q_variant and cand_variant and q_variant != cand_variant:
            details['hard_mismatches'].append(f'variant:{cand_variant}')
        if not q_variant and cand_variant in {_norm(t) for t in VARIANT_TOKENS}:
            # Requested standard; candidate is Team/Lite/etc.
            details['hard_mismatches'].append(f'variant:{cand_variant}')

        if q_head:
            cand_head = _parse_head_size(title)
            try:
                want_head = int(re.sub(r'\D', '', q_head) or '0')
            except ValueError:
                want_head = 0
            if want_head and cand_head and cand_head != want_head:
                details['hard_mismatches'].append(f'head_size:{cand_head}')

        if q_weight:
            cand_weight = _parse_weight(title)
            try:
                want_w = int(q_weight)
            except (TypeError, ValueError):
                want_w = 0
            if want_w and cand_weight and cand_weight != want_w:
                details['hard_mismatches'].append(f'weight_g:{cand_weight}')

        if q_pattern:
            cand_pattern = _norm(_parse_pattern(title) or '').replace(' ', '')
            if cand_pattern and cand_pattern != q_pattern:
                details['hard_mismatches'].append(f'string_pattern:{cand_pattern}')

        if q_gen:
            # Older/newer generation markers in title
            gen_tokens = set(re.findall(r'\bv?\d{1,4}\b', title_n))
            if gen_tokens and q_gen not in title_n and not any(q_gen in t or t in q_gen for t in gen_tokens):
                # Only flag when title clearly states a different generation-like token
                years = {t for t in gen_tokens if len(t) == 4}
                if years and q_gen not in years and q_gen.isdigit() and len(q_gen) == 4:
                    details['hard_mismatches'].append('generation')

        if query.grip_size:
            q_grip = normalize_grip_size(query.grip_size)
            title_grip = normalize_grip_size(title)
            # Only hard-reject when the listing explicitly states a different grip
            if q_grip and title_grip and q_grip != title_grip and re.search(
                r'(?:grip|l)\s*[0-5]\b|\bl[0-5]\b', title, re.I
            ):
                details['hard_mismatches'].append(f'grip_size:{title_grip}')

    if details['hard_mismatches']:
        details['score'] = '0.00'
        return MatchVerdict(
            match_status='no_match',
            match_score=Decimal('0.00'),
            details=details,
        )

    if query.ean and candidate.raw_data.get('ean') == query.ean:
        score += Decimal('0.45')
        details['signals'].append('ean')
    if query.upc and candidate.raw_data.get('upc') == query.upc:
        score += Decimal('0.45')
        details['signals'].append('upc')
    if q_code and (q_code in title_n or q_code == _norm(candidate.manufacturer_code or '')):
        score += Decimal('0.30')
        details['signals'].append('manufacturer_code')
    if candidate.supplier_sku and query.manufacturer_code:
        if _norm(candidate.supplier_sku) == q_code:
            score += Decimal('0.15')
            details['signals'].append('sku')

    brand_ok = (not q_brand) or (q_brand in title_n)
    model_ok = (not q_model) or all(t in _tokens(title) for t in _tokens(q_model) if len(t) > 2)
    if brand_ok and q_brand:
        score += Decimal('0.15')
        details['signals'].append('brand')
    if model_ok and q_model:
        score += Decimal('0.25')
        details['signals'].append('model')

    overlap = _tokens(title) & (_tokens(q_brand) | _tokens(q_model))
    if overlap:
        score += min(Decimal('0.10'), Decimal('0.02') * len(overlap))

    if query.grip_size:
        grip_n = _norm(query.grip_size).replace(' ', '')
        if grip_n and grip_n not in title_n.replace(' ', '') and not re.search(
            rf'\b{re.escape(grip_n[-1])}\b', title_n
        ):
            # Soft: grip often not in search title
            details['signals'].append('grip_unchecked')

    if score >= Decimal('0.70') and brand_ok and model_ok:
        status = 'exact'
    elif score >= Decimal('0.45'):
        status = 'manual_review'
        if query.color and query.color.lower() not in title.lower():
            status = 'alternative_color'
        elif query.generation and _norm(query.generation) not in title_n:
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
    seen = set()
    uniq = []
    for a in aliases:
        a = a.strip()
        if a and a.lower() not in seen:
            seen.add(a.lower())
            uniq.append(a)

    attrs = {
        'variant': getattr(np, 'variant', '') or '',
        'head_size': getattr(np, 'head_size', '') or '',
        'weight_g': getattr(np, 'weight_g', None),
        'string_pattern': getattr(np, 'string_pattern', '') or '',
        'category': getattr(np, 'category', '') or '',
        'field_sources': getattr(np, 'field_sources', None) or {},
    }
    if isinstance(raw, dict):
        attrs = {**raw, **attrs}

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
        raw_attributes=attrs,
    )
