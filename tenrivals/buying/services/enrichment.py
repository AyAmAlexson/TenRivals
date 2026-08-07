"""Tennis racquet enrichment: resolve Canonical/RacquetSpecification after AI.

Deterministic knowledge-base layer — does not invent specs from LLM memory.
Ambiguous requests stay uncertain; unique matches copy specs onto the
NormalizedProduct snapshot (historical requests are not rewritten later).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from buying.models import CanonicalProduct, ProductCategory, RacquetSpecification

SOURCE_CLIENT = 'client'
SOURCE_AI = 'ai'
SOURCE_CANONICAL = 'canonical'
SOURCE_STAFF = 'staff'

VARIANT_TOKENS = (
    'mini', 'junior', 'jr', 'kids', 'kid', 'children', 'child', 'youth',
    'team', 'lite', 'tour', 'plus', 'super lite', 'superlite',
)

# Always exclude from adult standard requests (never a soft alternative)
EXCLUSION_VARIANTS = frozenset({
    'mini', 'junior', 'jr', 'kids', 'kid', 'children', 'child', 'youth',
})


COLOR_MAP = {
    'синий': 'blue', 'синяя': 'blue', 'синее': 'blue',
    'чёрный': 'black', 'черный': 'black', 'чёрная': 'black',
    'белый': 'white', 'белая': 'white',
    'красный': 'red', 'красная': 'red',
    'зелёный': 'green', 'зеленый': 'green',
    'жёлтый': 'yellow', 'желтый': 'yellow',
    'оранжевый': 'orange', 'розовый': 'pink', 'серый': 'grey',
    'голубой': 'light blue', 'фиолетовый': 'purple',
}


@dataclass
class EnrichmentInput:
    brand: str = ''
    model_name: str = ''
    generation: str = ''
    category: str = ''
    variant: str = ''
    head_size: str = ''
    weight_g: int | None = None
    string_pattern: str = ''
    grip_size: str = ''
    color: str = ''
    length_cm: str = ''
    manufacturer_code: str = ''
    required_attributes: list = field(default_factory=list)
    optional_attributes: list = field(default_factory=list)
    uncertainties: list = field(default_factory=list)
    aliases: list = field(default_factory=list)
    original_query: str = ''


@dataclass
class EnrichmentResult:
    brand: str = ''
    model_name: str = ''
    generation: str = ''
    category: str = ''
    variant: str = ''
    head_size: str = ''
    weight_g: int | None = None
    string_pattern: str = ''
    grip_size: str = ''
    color: str = ''
    length_cm: str | float | None = None
    manufacturer_code: str = ''
    required_attributes: list = field(default_factory=list)
    optional_attributes: list = field(default_factory=list)
    uncertainties: list = field(default_factory=list)
    aliases: list = field(default_factory=list)
    field_sources: dict = field(default_factory=dict)
    enrichment_snapshot: dict = field(default_factory=dict)
    canonical_product_id: int | None = None
    ambiguous: bool = False
    resolved: bool = False


def _norm(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).strip()


def normalize_grip_size(raw: str) -> str:
    """Normalize supplier grip labels to L0–L5.

    Accepts: L4, Grip 4, grip size 4, 4 1/2, 4-1/2, ручка 4, 3я ручка.
    US circumference fractions map: 4\"→L0 … 4 1/2\"→L4 … 4 5/8\"→L5.
    """
    text = (raw or '').strip()
    if not text:
        return ''
    if re.fullmatch(r'L[0-5]', text, re.I):
        return text.upper()

    fraction_map = (
        (r'4\s*[-\s]?\s*5\s*/\s*8', 'L5'),
        (r'4\s*[-\s]?\s*1\s*/\s*2', 'L4'),
        (r'4\s*[-\s]?\s*3\s*/\s*8', 'L3'),
        (r'4\s*[-\s]?\s*1\s*/\s*4', 'L2'),
        (r'4\s*[-\s]?\s*1\s*/\s*8', 'L1'),
    )
    low = text.lower().replace('″', '"').replace('′', "'")
    for pattern, grip in fraction_map:
        if re.search(pattern, low):
            return grip

    # Russian ordinal forms: "3я ручка", "4-я ручка", "2ой ручки"
    m = re.search(
        r'([0-5])\s*[-.]?\s*(?:я|й|ой|ая|ое|ье)?\s*ручк',
        text,
        re.I,
    )
    if m:
        return f'L{m.group(1)}'

    m = re.search(r'(?:l|ручка|grip(?:\s*size)?)\s*[#:]?\s*([0-5])\b', text, re.I)
    if m:
        return f'L{m.group(1)}'
    # Whole-field bare digit from AI / forms (e.g. grip_size="4")
    if re.fullmatch(r'[0-5]', text.strip()):
        return f'L{text.strip()}'
    m = re.search(r'\b([0-5])\b', text)
    if m and re.search(r'grip|ручка|size|l\b', text, re.I):
        return f'L{m.group(1)}'
    return text


def normalize_color(raw: str) -> str:
    text = (raw or '').strip()
    if not text:
        return ''
    mapped = COLOR_MAP.get(text.lower())
    return mapped or text


def _parse_pattern(text: str) -> str | None:
    m = re.search(r'\b(\d{2})\s*[x×]\s*(\d{2})\b', text, re.I)
    if m:
        return f'{m.group(1)}x{m.group(2)}'
    return None


def _parse_weight(text: str) -> int | None:
    m = re.search(r'unstrung(?:\s+weight)?\s*[:\-]?\s*(\d{2,3})\s*g', text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r'weight\s*\(unstrung\)\s*[:\-]?\s*(\d{2,3})\s*g', text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r'\b(\d{2,3})\s*g\b', text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r'\b(\d{2,3})\s*г\b', text, re.I)
    if m:
        return int(m.group(1))
    return None


def _parse_head_size(text: str) -> int | None:
    # Prefer explicit "100 sq" / "98\"" — else standalone 98/100/107 near model words
    m = re.search(r'\b(90|93|95|97|98|100|104|107|110|115)\s*(?:sq\.?\s*in|in|"|′)?\b', text, re.I)
    if m:
        return int(m.group(1))
    return None


def _detect_variant(text: str) -> str:
    n = _norm(text)
    words = n.split()
    # Mini / kids first — never confuse with adult Pure Drive etc.
    for token in ('mini', 'junior', 'kids', 'kid', 'children', 'child', 'youth'):
        if token in words:
            return 'Mini' if token == 'mini' else 'Junior'
    if 'jr' in words:
        return 'Junior'
    for token in ('super lite', 'superlite', 'team', 'lite', 'tour', 'plus'):
        if ' ' in token or token == 'superlite':
            if token in n or (token == 'superlite' and 'super lite' in n):
                return 'Super Lite'
            continue
        if token not in words:
            continue
        # Marketing "Tour racket" / "Tour Racket" / tournament naming ≠ model variant
        # (e.g. Tennis-Point "Blade 100 V10 Tour racket"). Real variants look like
        # "Blade Tour", "Pure Drive Tour".
        if token == 'tour':
            if re.search(r'\btour\s+(racket|racquet|schlaeger|schlager)\b', n):
                if not re.search(
                    r'\b(blade|pure\s*drive|pure\s*aero|ezone|vcore|clash|pro\s*staff)\s+tour\b',
                    n,
                ):
                    continue
            # German "Turnierschläger" normalizes without matching bare "tour"
        return token.title()
    return ''


def strip_spec_tokens_from_model(model: str) -> tuple[str, dict]:
    """Move head size / pattern / weight / variant out of the model string."""
    extracted: dict = {}
    text = model or ''
    variant = _detect_variant(text)
    if variant:
        extracted['variant'] = variant
        # remove variant word from model
        text = re.sub(re.escape(variant), '', text, flags=re.I)
        text = re.sub(r'\bjr\b', '', text, flags=re.I)

    pattern = _parse_pattern(text)
    if pattern:
        extracted['string_pattern'] = pattern
        text = re.sub(r'\b\d{2}\s*[x×]\s*\d{2}\b', '', text, flags=re.I)

    weight = _parse_weight(text)
    if weight:
        extracted['weight_g'] = weight
        text = re.sub(r'\b\d{2,3}\s*g\b', '', text, flags=re.I)
        text = re.sub(r'\b\d{2,3}\s*г\b', '', text, flags=re.I)

    head = _parse_head_size(text)
    if head:
        extracted['head_size'] = str(head)
        text = re.sub(rf'\b{head}\b', '', text, count=1)

    # generation tokens like v9 / v10 often stay in model — leave for AI/generation field
    text = re.sub(r'\s+', ' ', text).strip(' ,/-')
    return text, extracted


def enrichment_input_from_ai(data: dict, *, original_query: str = '') -> EnrichmentInput:
    weight_raw = data.get('weight') or ''
    weight_g = None
    if isinstance(weight_raw, int):
        weight_g = weight_raw
    else:
        from buying.services.normalization import _parse_weight_g
        weight_g = _parse_weight_g(str(weight_raw))
    return EnrichmentInput(
        brand=(data.get('brand') or '').strip(),
        model_name=(data.get('model') or '').strip(),
        generation=(data.get('generation') or '').strip(),
        category=(data.get('category') or '').strip(),
        variant=(data.get('variant') or '').strip(),
        head_size=str(data.get('head_size') or '').strip(),
        weight_g=weight_g,
        string_pattern=(data.get('string_pattern') or '').strip(),
        grip_size=(data.get('grip_size') or '').strip(),
        color=(data.get('color') or '').strip(),
        manufacturer_code=(data.get('manufacturer_code') or '').strip(),
        required_attributes=list(data.get('required_attributes') or []),
        optional_attributes=list(data.get('optional_attributes') or []),
        uncertainties=list(data.get('uncertainties') or []),
        aliases=list(data.get('aliases') or []),
        original_query=original_query or '',
    )


def enrich_racquet(inp: EnrichmentInput) -> EnrichmentResult:
    """Resolve and enrich a racquet request. Non-racquets pass through lightly."""
    sources: dict[str, str] = {}
    result = EnrichmentResult(
        brand=inp.brand,
        model_name=inp.model_name,
        generation=inp.generation,
        category=inp.category or ProductCategory.RACQUET,
        variant=inp.variant,
        head_size=inp.head_size,
        weight_g=inp.weight_g,
        string_pattern=inp.string_pattern,
        grip_size=normalize_grip_size(inp.grip_size),
        color=normalize_color(inp.color),
        manufacturer_code=inp.manufacturer_code,
        required_attributes=list(inp.required_attributes),
        optional_attributes=list(inp.optional_attributes),
        uncertainties=list(inp.uncertainties),
        aliases=list(inp.aliases),
    )

    # Mark AI-provided fields
    for key, val in (
        ('brand', result.brand),
        ('model_name', result.model_name),
        ('generation', result.generation),
        ('category', result.category),
        ('variant', result.variant),
        ('head_size', result.head_size),
        ('weight_g', result.weight_g),
        ('string_pattern', result.string_pattern),
        ('grip_size', result.grip_size),
        ('color', result.color),
        ('manufacturer_code', result.manufacturer_code),
    ):
        if val not in (None, ''):
            sources[key] = SOURCE_AI

    # Client-query heuristics (override empty / refine model tokens)
    query = inp.original_query or ''
    if query:
        if not result.grip_size:
            g = normalize_grip_size(query)
            # only if lookslike grip mention
            if re.search(r'(grip|ручка|l\s*[0-5]|\b[0-5]\b)', query, re.I):
                # Prefer explicit grip patterns
                m = re.search(r'(?:grip|ручка|l)\s*([0-5])\b', query, re.I)
                if m:
                    result.grip_size = f'L{m.group(1)}'
                    sources['grip_size'] = SOURCE_CLIENT
        if result.color and sources.get('color') == SOURCE_AI:
            # still normalize Russian → English
            new_c = normalize_color(result.color)
            if new_c != result.color:
                result.color = new_c
                sources['color'] = SOURCE_CLIENT
        elif not result.color:
            for ru, en in COLOR_MAP.items():
                if ru in query.lower():
                    result.color = en
                    sources['color'] = SOURCE_CLIENT
                    break

    # Strip tokens from model
    cleaned_model, extracted = strip_spec_tokens_from_model(result.model_name)
    if cleaned_model and cleaned_model != result.model_name:
        result.model_name = cleaned_model
        sources['model_name'] = sources.get('model_name', SOURCE_AI)
    for key, val in extracted.items():
        current = getattr(result, key, None)
        if not current:
            setattr(result, key, val)
            sources[key] = SOURCE_CLIENT if key in query.lower() or True else SOURCE_AI
            # Prefer marking as client when present in raw query
            if key == 'head_size' and str(val) in _norm(query):
                sources[key] = SOURCE_CLIENT
            elif key == 'weight_g' and re.search(rf'\b{val}\s*g', query, re.I):
                sources[key] = SOURCE_CLIENT
            else:
                sources[key] = SOURCE_AI

    if not result.variant:
        v = _detect_variant(query) or _detect_variant(inp.model_name)
        if v:
            result.variant = v
            sources['variant'] = SOURCE_CLIENT

    if result.category and result.category != ProductCategory.RACQUET:
        result.field_sources = sources
        return result

    # Resolve against maintained specs
    candidates = _find_specs(result)
    if len(candidates) == 0:
        result.uncertainties = _clean_uncertainties(
            result.uncertainties,
            keep_weight=result.weight_g is None,
            keep_head=not result.head_size,
        )
        if result.weight_g is None and result.category == ProductCategory.RACQUET:
            _ensure_uncertainty(result, 'Weight could not be resolved from a known racquet specification.')
        result.field_sources = sources
        return result

    if len(candidates) > 1:
        result.ambiguous = True
        result.uncertainties = _clean_uncertainties(result.uncertainties, keep_weight=True, keep_head=True)
        names = [str(c) for c in candidates[:6]]
        _ensure_uncertainty(
            result,
            'Multiple racquet variants match — staff must confirm: ' + '; '.join(names),
        )
        # Do not auto-fill weight/head from an arbitrary candidate
        result.field_sources = sources
        return result

    spec = candidates[0]
    result.resolved = True
    result.enrichment_snapshot = spec.to_snapshot()
    if spec.canonical_product_id:
        result.canonical_product_id = spec.canonical_product_id

    def apply(field_name: str, value, *, as_str: bool = False):
        if value in (None, ''):
            return
        current = getattr(result, field_name)
        if current in (None, ''):
            setattr(result, field_name, str(value) if as_str and value is not None else value)
            sources[field_name] = SOURCE_CANONICAL
        elif field_name == 'model_name' and _norm(current) != _norm(spec.model_family):
            # Prefer canonical family name (Pure Drive not Pure Drive 100)
            setattr(result, field_name, value)
            sources[field_name] = SOURCE_CANONICAL

    apply('brand', spec.brand)
    # Always use canonical family name once resolved (Pure Drive, not Pure Drive 100)
    result.model_name = spec.model_family
    sources['model_name'] = SOURCE_CANONICAL
    apply('generation', spec.generation)
    apply('variant', spec.variant)
    if spec.head_size_sqin:
        apply('head_size', str(spec.head_size_sqin), as_str=True)
    if spec.weight_g_unstrung:
        if result.weight_g is None:
            result.weight_g = spec.weight_g_unstrung
            sources['weight_g'] = SOURCE_CANONICAL
    apply('string_pattern', spec.string_pattern)
    if spec.length_cm is not None and result.length_cm is None:
        result.length_cm = spec.length_cm
        sources['length_cm'] = SOURCE_CANONICAL
    apply('manufacturer_code', spec.manufacturer_code)

    if spec.aliases:
        merged = list(result.aliases)
        for a in spec.aliases:
            if a and a not in merged:
                merged.append(a)
        result.aliases = merged

    # Required attributes for a resolved racquet
    req = {
        'brand', 'model', 'generation', 'head_size', 'weight_g', 'string_pattern',
    }
    if result.grip_size:
        req.add('grip_size')
    if result.variant:
        req.add('variant')
    # map model_name → model for attribute lists used by AI schema
    existing_req = {a for a in result.required_attributes if a != 'model_name'}
    if 'model' not in existing_req and result.model_name:
        existing_req.add('model')
    result.required_attributes = sorted(existing_req | req)
    if result.color and 'color' not in result.optional_attributes and 'color' not in result.required_attributes:
        result.optional_attributes = list(result.optional_attributes) + ['color']

    result.uncertainties = _clean_uncertainties(
        result.uncertainties,
        keep_weight=False,
        keep_head=False,
        drop_phrases=('weight', 'head size', 'head_size'),
    )
    result.field_sources = sources
    result.category = ProductCategory.RACQUET
    return result


def _find_specs(result: EnrichmentResult) -> list[RacquetSpecification]:
    brand = result.brand
    if not brand:
        return []
    qs = RacquetSpecification.objects.filter(enabled=True, brand__iexact=brand)
    family = _norm(result.model_name)
    # Match model_family contained in model or vice versa
    matched = []
    for spec in qs:
        fam = _norm(spec.model_family)
        if not fam:
            continue
        if fam == family or fam in family or family in fam:
            matched.append(spec)
        else:
            aliases = [_norm(a) for a in (spec.aliases or [])]
            if any(a and (a == family or a in family or family in a) for a in aliases):
                matched.append(spec)

    # Filter by generation when provided
    if result.generation:
        gen = _norm(result.generation)
        gen_filtered = [
            s for s in matched
            if not s.generation or _norm(s.generation) == gen or gen in _norm(s.generation)
        ]
        if gen_filtered:
            matched = gen_filtered

    # Filter by variant
    want_variant = _norm(result.variant)
    if want_variant:
        matched = [s for s in matched if _norm(s.variant) == want_variant]
    else:
        # Prefer standard (empty variant) when request has no variant token
        standards = [s for s in matched if not s.variant]
        if standards:
            matched = standards

    # Filter by head size
    if result.head_size:
        try:
            hs = int(re.sub(r'\D', '', result.head_size) or '0')
        except ValueError:
            hs = 0
        if hs:
            hs_filtered = [s for s in matched if s.head_size_sqin in (None, hs)]
            exact = [s for s in matched if s.head_size_sqin == hs]
            matched = exact or hs_filtered

    # Filter by weight if explicitly provided
    if result.weight_g:
        w_filtered = [
            s for s in matched
            if s.weight_g_unstrung in (None, result.weight_g)
        ]
        exact = [s for s in matched if s.weight_g_unstrung == result.weight_g]
        matched = exact or w_filtered

    # Filter by string pattern
    if result.string_pattern:
        pat = _norm(result.string_pattern).replace(' ', '')
        p_filtered = [
            s for s in matched
            if not s.string_pattern or _norm(s.string_pattern).replace(' ', '') == pat
        ]
        if p_filtered:
            matched = p_filtered

    return matched


def _clean_uncertainties(items: list, *, keep_weight: bool, keep_head: bool, drop_phrases=None) -> list:
    drop_phrases = drop_phrases or ()
    out = []
    for item in items:
        low = (item or '').lower()
        if not keep_weight and 'weight' in low:
            continue
        if not keep_head and ('head size' in low or 'head_size' in low):
            continue
        if any(p in low for p in drop_phrases):
            continue
        if item and item not in out:
            out.append(item)
    return out


def _ensure_uncertainty(result: EnrichmentResult, message: str) -> None:
    if message not in result.uncertainties:
        result.uncertainties.append(message)


def apply_enrichment_to_product(product, result: EnrichmentResult) -> None:
    """Write enrichment onto a NormalizedProduct instance (unsaved OK)."""
    product.brand = result.brand
    product.model_name = result.model_name
    product.generation = result.generation
    product.category = result.category
    product.variant = result.variant
    product.head_size = result.head_size
    product.weight_g = result.weight_g
    product.string_pattern = result.string_pattern
    product.grip_size = result.grip_size
    product.color = result.color
    product.length_cm = result.length_cm
    product.manufacturer_code = result.manufacturer_code or product.manufacturer_code
    product.required_attributes = result.required_attributes
    product.optional_attributes = result.optional_attributes
    product.uncertainties = result.uncertainties
    product.aliases = result.aliases
    product.field_sources = result.field_sources
    product.enrichment_snapshot = result.enrichment_snapshot
    if result.canonical_product_id:
        product.canonical_product_id = result.canonical_product_id
    elif result.resolved and result.brand and result.model_name:
        # Ensure a CanonicalProduct exists for linking
        canon, _ = CanonicalProduct.objects.get_or_create(
            brand=result.brand,
            model_name=result.model_name,
            generation=result.generation or '',
            category=ProductCategory.RACQUET,
            defaults={},
        )
        product.canonical_product = canon
