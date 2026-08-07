"""Deterministic product matching (Phase 3 — no LLM).

Uses enriched racquet specs (head size, weight, pattern, generation, variant)
as hard constraints. Mini / Junior / kids and Team/Lite/etc. never soft-match
an adult standard request.

Specs on the product page (page_text) count the same as title tokens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from buying.connectors.base import NormalizedProductQuery, SearchCandidate
from buying.services.enrichment import (
    EXCLUSION_VARIANTS,
    VARIANT_TOKENS,
    _detect_variant,
    _norm,
    _parse_head_size,
    _parse_pattern,
    _parse_weight,
    normalize_grip_size,
)

MATCHING_RULES_VERSION = 'match-rules-v4'

GENERATION_ALIASES: dict[str, set[str]] = {
    '2025': {'2025', 'gen11', 'gen 11', 'g11'},
    '2024': {'2024', 'gen10', 'gen 10', 'g10'},
    'v10': {'v10', '10'},
    'v9': {'v9', '9'},
}

# Product-type tokens incompatible with a racquet request (title-level only)
ACCESSORY_TITLE_TOKENS = frozenset({
    'backpack', 'back pack', 'bag', 'duffel', 'duffle', 'tote',
    'dampener', 'dampner', 'vibration damp', 'overgrip', 'replacement grip',
    'string reel', 'strings', 'wristband', 'visor', 'cap', 'hat',
    'shoe', 'shoes', 'sock', 'socks', 'shirt', 'shorts', 'skirt',
})


@dataclass
class MatchVerdict:
    match_status: str
    match_score: Decimal
    details: dict


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if len(t) > 1}


def _candidate_blob(candidate: SearchCandidate) -> str:
    parts = [candidate.title or '']
    raw = candidate.raw_data if isinstance(candidate.raw_data, dict) else {}
    for key in ('page_text', 'description', 'body', 'specs_text'):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val)
    return ' '.join(parts)


def _title_blob(candidate: SearchCandidate) -> str:
    """Title (+ short product name) for family/variant exclusion — not full page HTML.

    Full product pages often list related Junior / Mini SKUs; those must not
    hard-reject an adult standard racquet candidate.
    """
    parts = [candidate.title or '']
    raw = candidate.raw_data if isinstance(candidate.raw_data, dict) else {}
    for key in ('product_name', 'name', 'handle'):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val)
    return ' '.join(parts)


def _parse_weight_for_match(text: str) -> int | None:
    """Prefer unstrung weight via enrichment parser."""
    from buying.services.enrichment import _parse_weight
    return _parse_weight(text)


def _parse_head_for_match(text: str) -> int | None:
    from buying.services.enrichment import _parse_head_size
    m = re.search(
        r'head\s*size\s*[:\-]?\s*(90|93|95|97|98|100|104|107|110|115)\b',
        text,
        re.I,
    )
    if m:
        return int(m.group(1))
    return _parse_head_size(text)


def _generation_matches(q_gen: str, text_n: str) -> bool:
    if not q_gen:
        return True
    if q_gen in text_n:
        return True
    aliases = GENERATION_ALIASES.get(q_gen, set()) | {q_gen}
    return any(_norm(a) in text_n for a in aliases if a)


def score_candidate(query: NormalizedProductQuery, candidate: SearchCandidate) -> MatchVerdict:
    details: dict = {
        'signals': [],
        'hard_mismatches': [],
        'matching_rules_version': MATCHING_RULES_VERSION,
    }
    score = Decimal('0')
    blob = _candidate_blob(candidate)
    blob_n = _norm(blob)
    title = candidate.title or ''
    title_n = _norm(title)
    title_for_family = _title_blob(candidate)
    title_family_n = _norm(title_for_family)
    attrs = query.raw_attributes if isinstance(query.raw_attributes, dict) else {}

    q_brand = _norm(query.brand)
    q_model = _norm(query.model_name)
    q_code = _norm(query.manufacturer_code)
    q_gen = _norm(query.generation)
    q_variant = _norm(str(attrs.get('variant') or ''))
    q_head = str(attrs.get('head_size') or '').strip()
    q_weight = attrs.get('weight_g')
    q_pattern = _norm(str(attrs.get('string_pattern') or '')).replace(' ', '')

    if query.category == 'racquet' or attrs.get('category') == 'racquet' or q_model:
        # Accessories in the title are never racquet matches
        for tok in ACCESSORY_TITLE_TOKENS:
            if tok in title_n or (tok in title_family_n and ' ' in tok):
                details['hard_mismatches'].append(f'product_type:{tok.replace(" ", "_")}')
                break
            if ' ' not in tok and tok in title_family_n.split():
                details['hard_mismatches'].append(f'product_type:{tok}')
                break

        # Variant / Mini / Junior / Team / Lite — TITLE only (not related-product page noise)
        cand_variant = _norm(_detect_variant(title_for_family))
        exclusion_norms = {_norm(t) for t in EXCLUSION_VARIANTS} | {_norm(t) for t in ('mini', 'junior')}

        if not q_variant and cand_variant in exclusion_norms:
            details['hard_mismatches'].append(f'variant:{cand_variant or "excluded"}')
        if q_variant and cand_variant and q_variant != cand_variant:
            details['hard_mismatches'].append(f'variant:{cand_variant}')
        if not q_variant and cand_variant and cand_variant in {_norm(t) for t in VARIANT_TOKENS}:
            details['hard_mismatches'].append(f'variant:{cand_variant}')
        if not q_variant:
            for tok in ('mini', 'junior', 'jr', 'kids', 'youth', 'children'):
                if tok in title_family_n.split():
                    details['hard_mismatches'].append(f'variant:{tok}')
                    break

        # Specs: prefer labeled page fields; fall back to title
        spec_source = blob if len(blob) > len(title_for_family) + 20 else title_for_family

        if q_head:
            cand_head = _parse_head_for_match(spec_source)
            # If page noise yields a head that conflicts, trust title when title has an explicit size
            title_head = _parse_head_for_match(title_for_family)
            if title_head:
                cand_head = title_head
            try:
                want_head = int(re.sub(r'\D', '', q_head) or '0')
            except ValueError:
                want_head = 0
            if want_head and cand_head and cand_head != want_head:
                details['hard_mismatches'].append(f'head_size:{cand_head}')

        if q_weight:
            cand_weight = _parse_weight_for_match(spec_source)
            title_weight = _parse_weight_for_match(title_for_family)
            if title_weight:
                cand_weight = title_weight
            try:
                want_w = int(q_weight)
            except (TypeError, ValueError):
                want_w = 0
            if want_w and cand_weight and cand_weight != want_w:
                details['hard_mismatches'].append(f'weight_g:{cand_weight}')

        if q_pattern:
            cand_pattern = _norm(_parse_pattern(spec_source) or '').replace(' ', '')
            title_pattern = _norm(_parse_pattern(title_for_family) or '').replace(' ', '')
            if title_pattern:
                cand_pattern = title_pattern
            if cand_pattern and cand_pattern != q_pattern:
                details['hard_mismatches'].append(f'string_pattern:{cand_pattern}')

        if q_gen:
            # Generation: title first, then page — avoid rejecting because a related SKU is 2026
            gen_text = title_family_n if re.search(r'\b(19|20)\d{2}\b|gen\s*\d+', title_family_n) else blob_n
            years_in = {t for t in re.findall(r'\b(19|20)\d{2}\b', gen_text)}
            if years_in and not _generation_matches(q_gen, gen_text):
                if q_gen.isdigit() and len(q_gen) == 4 and q_gen not in years_in:
                    details['hard_mismatches'].append('generation')
            elif re.search(r'\bgen\s*\d+\b', gen_text) and not _generation_matches(q_gen, gen_text):
                details['hard_mismatches'].append('generation')

        if query.grip_size:
            q_grip = normalize_grip_size(query.grip_size)
            # Grip hard-mismatch only from title/variant labels, not full page noise
            title_grip = normalize_grip_size(title_for_family)
            if q_grip and title_grip and q_grip != title_grip and re.search(
                r'(?:grip|l)\s*[0-5]\b|\bl[0-5]\b|4\s*[-\s]?\s*1\s*/\s*2', title_for_family, re.I
            ):
                details['hard_mismatches'].append(f'grip_size:{title_grip}')

    # Deduplicate hard mismatches
    seen_mm = []
    for mm in details['hard_mismatches']:
        if mm not in seen_mm:
            seen_mm.append(mm)
    details['hard_mismatches'] = seen_mm

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
    if q_code and (q_code in blob_n or q_code == _norm(candidate.manufacturer_code or '')):
        score += Decimal('0.30')
        details['signals'].append('manufacturer_code')
    if candidate.supplier_sku and query.manufacturer_code:
        if _norm(candidate.supplier_sku) == q_code:
            score += Decimal('0.15')
            details['signals'].append('sku')

    brand_ok = (not q_brand) or (q_brand in blob_n)
    model_ok = (not q_model) or all(t in _tokens(blob) for t in _tokens(q_model) if len(t) > 2)
    if brand_ok and q_brand:
        score += Decimal('0.15')
        details['signals'].append('brand')
    if model_ok and q_model:
        score += Decimal('0.25')
        details['signals'].append('model')

    if q_gen and _generation_matches(q_gen, blob_n):
        score += Decimal('0.10')
        details['signals'].append('generation')
    if q_head and _parse_head_size(blob) and str(_parse_head_size(blob)) in str(q_head):
        score += Decimal('0.08')
        details['signals'].append('head_size')
    if q_weight and _parse_weight(blob) == int(q_weight or 0):
        score += Decimal('0.08')
        details['signals'].append('weight')

    overlap = _tokens(blob) & (_tokens(q_brand) | _tokens(q_model))
    if overlap:
        score += min(Decimal('0.10'), Decimal('0.02') * len(overlap))

    if query.grip_size:
        grip_n = _norm(query.grip_size).replace(' ', '')
        if grip_n and grip_n not in blob_n.replace(' ', ''):
            details['signals'].append('grip_unchecked')

    color_mismatch = bool(query.color and query.color.lower() not in blob.lower())
    gen_ok = (not q_gen) or _generation_matches(q_gen, blob_n) or (
        q_gen not in title_n and 'gen' not in blob_n
    )

    if score >= Decimal('0.70') and brand_ok and model_ok:
        if color_mismatch:
            status = 'alternative_color'
        elif q_gen and not gen_ok:
            status = 'alternative_version'
        else:
            status = 'exact'
    elif score >= Decimal('0.45'):
        status = 'manual_review'
        if color_mismatch:
            status = 'alternative_color'
        elif q_gen and not gen_ok:
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
