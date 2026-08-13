"""AI draft for staff product listings (URL → form fields, no auto-save)."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import threading
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections

from .models import CourtSurface, Gender, ProductType

logger = logging.getLogger('shop')

LISTING_PROMPT_VERSION = 'listing-fill-v1'
_JOB_TTL = 600
_PAGE_TEXT_LIMIT = 14000
_FETCH_TIMEOUT = 22.0
_LLM_TIMEOUT = 90.0
_MAX_REDIRECTS = 6

SHOE_TYPES = {
    ProductType.MENS_SHOES,
    ProductType.WOMENS_SHOES,
    ProductType.JUNIOR_SHOES,
}
APPAREL_TYPES = {
    ProductType.MENS_APPAREL,
    ProductType.WOMENS_APPAREL,
    ProductType.JUNIOR_APPAREL,
}
TYPE_GENDER = {
    ProductType.MENS_SHOES: Gender.MEN,
    ProductType.WOMENS_SHOES: Gender.WOMEN,
    ProductType.JUNIOR_SHOES: Gender.JUNIOR,
    ProductType.MENS_APPAREL: Gender.MEN,
    ProductType.WOMENS_APPAREL: Gender.WOMEN,
    ProductType.JUNIOR_APPAREL: Gender.JUNIOR,
}

GENDER_VALUES = {c.value for c in Gender}
SURFACE_VALUES = {c.value for c in CourtSurface}
ALLOWED_TYPES = {c.value for c in ProductType}

_STR_FIELDS = (
    'brand',
    'name',
    'color',
    'sku',
    'short_description',
    'description',
    'price_currency',
    'width',
    'string_pattern',
    'material',
    'notes_for_staff',
)
_INT_FIELDS = (
    'weight_grams',
    'head_size_sq_in',
    'balance_mm',
    'swingweight',
    'capacity_rackets',
    'balls_per_can',
)
_DEC_FIELDS = ('length_in', 'length_m')

LISTING_JSON_SCHEMA = {
    'name': 'shop_listing_fill',
    'strict': True,
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'brand': {'type': ['string', 'null']},
            'name': {'type': ['string', 'null']},
            'color': {'type': ['string', 'null']},
            'sku': {'type': ['string', 'null']},
            'short_description': {'type': ['string', 'null']},
            'description': {'type': ['string', 'null']},
            'initial_price': {'type': ['number', 'null']},
            'price_currency': {'type': ['string', 'null']},
            'gender': {'type': ['string', 'null']},
            'surface': {'type': ['string', 'null']},
            'width': {'type': ['string', 'null']},
            'weight_grams': {'type': ['integer', 'null']},
            'head_size_sq_in': {'type': ['integer', 'null']},
            'length_in': {'type': ['number', 'null']},
            'balance_mm': {'type': ['integer', 'null']},
            'swingweight': {'type': ['integer', 'null']},
            'string_pattern': {'type': ['string', 'null']},
            'is_strung': {'type': ['boolean', 'null']},
            'material': {'type': ['string', 'null']},
            'length_m': {'type': ['number', 'null']},
            'capacity_rackets': {'type': ['integer', 'null']},
            'balls_per_can': {'type': ['integer', 'null']},
            'attributes': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'additionalProperties': False,
                    'properties': {
                        'parameter': {'type': 'string'},
                        'value': {'type': 'string'},
                    },
                    'required': ['parameter', 'value'],
                },
            },
            'notes_for_staff': {'type': ['string', 'null']},
        },
        'required': [
            'brand',
            'name',
            'color',
            'sku',
            'short_description',
            'description',
            'initial_price',
            'price_currency',
            'gender',
            'surface',
            'width',
            'weight_grams',
            'head_size_sq_in',
            'length_in',
            'balance_mm',
            'swingweight',
            'string_pattern',
            'is_strung',
            'material',
            'length_m',
            'capacity_rackets',
            'balls_per_can',
            'attributes',
            'notes_for_staff',
        ],
    },
}

_RETAILER_RES = (
    re.compile(r'\btennis\s*warehouse(?:\s*europe)?\b', re.I),
    re.compile(r'\btennis[\s-]*point\b', re.I),
    re.compile(r'\bmidwest\s*racquet(?:\s*sports)?\b', re.I),
    re.compile(r'\btennis\s*express\b', re.I),
    re.compile(r'\btennis\s*pro(?:\s*direct)?\b', re.I),
    re.compile(r'\bstring\s*kingdom\b', re.I),
    re.compile(r'\bracquetspecialist\b', re.I),
    re.compile(r'\bholabird\b', re.I),
    re.compile(r'\btennis\s*warehouse\s*europe\b', re.I),
)

_BLOCK_MARKERS = (
    'captcha',
    'access denied',
    'attention required',
    'cf-browser-verification',
    'just a moment',
    'enable javascript and cookies',
    'request unsuccessful',
    'sorry, you have been blocked',
)

_FETCH_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}


class ListingFillError(Exception):
    """Staff-visible failure while drafting a listing."""


def _job_key(job_id: str) -> str:
    return f'shop_ai_listing:{job_id}'


def validate_source_url(url: str, *, resolve: bool = True) -> str:
    raw = (url or '').strip()
    if not raw:
        raise ListingFillError('Paste a product URL first.')
    if len(raw) > 2048:
        raise ListingFillError('URL is too long.')
    parsed = urlparse(raw)
    if parsed.scheme not in ('http', 'https'):
        raise ListingFillError('URL must start with http:// or https://')
    if parsed.username or parsed.password:
        raise ListingFillError('URL must not include credentials.')
    host = (parsed.hostname or '').strip().lower()
    if not host:
        raise ListingFillError('URL is missing a host.')
    if host in ('localhost', 'localhost.localdomain') or host.endswith('.local') or host.endswith('.internal'):
        raise ListingFillError('That URL is not allowed.')
    if resolve:
        _assert_public_host(host)
    return raw


def _assert_public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ListingFillError('Could not resolve the product URL host.') from exc
    if not infos:
        raise ListingFillError('Could not resolve the product URL host.')
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ListingFillError('That URL is not allowed.')


def system_prompt_for_type(product_type: str) -> str:
    label = dict(ProductType.choices).get(product_type, product_type)
    dedicated, extra_attrs = _category_field_instructions(product_type)
    return f"""You are a professional product content specialist creating SEO-friendly, human-written product pages for a curated tennis retailer.

The user will provide extracted content from a product page. Analyze it and generate a complete product listing draft in fluent English for our own store.

GENERAL PRINCIPLES

• Never mention, reference, compare with, or link to the source retailer or any other retailer.
• Never write phrases like "according to Tennis Warehouse", "on the product page", "this retailer says", etc.
• Write as if the product belongs to our own tennis store.
• Do not copy marketing text. Rewrite everything naturally.
• Do not invent specifications. If something is not confirmed, omit it (use null / skip the attribute).
• Keep the tone honest, informative and premium.
• Avoid exaggerated marketing language such as: best, ultimate, revolutionary, game-changing, must-have, perfect for everyone.
• Never overpromise performance.
• Explain what the product is actually good at.

SELECTED CATALOG TYPE: {label} ({product_type})

Fill ONLY fields that apply to this type. Use null for fields that do not apply or are unconfirmed.
Do not change or invent a different product type.

-----------------------------------

IDENTITY FIELDS (always attempt)

• brand — manufacturer name only (e.g. Wilson, Yonex, Babolat).
• name — model name for our catalog. Include generation when it is part of the model (e.g. Blade 100 V9). Do not repeat the brand. Do not append color unless the color is part of the official model name.
• color — colorway / finish as sold (e.g. Black/Green, White). Empty if unknown.
• sku — internal SKU, see SKU rules below.
• initial_price — always null. Staff sets our GEL price separately. Never copy the source price into this field.
• price_currency — always null.

-----------------------------------

SHORT DESCRIPTION

One sentence. 15–25 words.

Describe: product category, main benefit, playing character.

Examples:

Premium overgrip with a soft, tacky feel, excellent comfort, and reliable moisture absorption.

Control-oriented polyester string with a crisp response, excellent spin potential, and dependable durability.

Lightweight all-court shoe with responsive cushioning, stable support, and everyday comfort.

-----------------------------------

MAIN DESCRIPTION (field: description)

Write 2 paragraphs. 120–170 words.

Paragraph 1: what the product is, how it feels, what makes it different, playing characteristics.

Paragraph 2: who should use it, what type of player it suits, practical advantages.

Never mention another retailer.
Never compare with unnamed competitors using "better than".

Instead use:
Compared with heavier frames...
Compared with firmer strings...
Compared with thicker grips...
Compared with speed-oriented shoes...

Use realistic tennis terminology.
When only the color changes versus a sibling SKU, rewrite so the color paragraph is unique rather than swapping the color name.

-----------------------------------

DEDICATED FORM FIELDS FOR THIS TYPE

{dedicated}

Use null when a dedicated field is not confirmed. Do not guess.

-----------------------------------

EXTRA ATTRIBUTES (attributes[])

Each item: {{ "parameter": "...", "value": "..." }}.

These appear on the product page as a spec table.

Do NOT include: Product name, Color, Brand, SKU, price.
Do NOT repeat dedicated form fields listed above.

Only verified specifications. Order: physical specs → technologies → benefits.

Typical extra fields for this type:
{extra_attrs}

-----------------------------------

SKU

Generate a short internal SKU.

Format: [Brand][Model][Generation][Color][Gender][Court]

Brand codes: Y Yonex, W Wilson, H Head, P Prince, B Babolat, NB New Balance, A Asics, NK Nike, AD Adidas, S Solinco, LX Luxilon, TL Tecnifibre, T Toroline, D Dunlop, L Lacoste, F Fila, U Unisex/unknown brand initials.

Generation: V9, V10, 25, 26, etc.

Color: WH BK BL NV GY SV GN MG PG MT YL OR RD PK MP PL PU DP LI CR BG BB TP PH CW TW etc.

Gender (M or W) ONLY for shoes and apparel. Never for racquets, strings, bags, balls, grips, dampeners, accessories.

Court (CL AC HC) ONLY for shoes. Map clay→CL, all-court→AC, hard→HC. Omit for grass/padel unless clearly a tennis court type.

Examples: HBMPL26TP, YEZ10025BB, PS9826GW, AGFF8WMGWCL, NB996V6LIWCL

SKU must be 4–32 characters, letters and digits only, no spaces.

-----------------------------------

STYLE

Write naturally. Do not sound AI-generated. Do not repeat the same sentence patterns. Vary wording between products. Always prioritize accuracy over marketing.

notes_for_staff: short note for our staff only (missing specs, ambiguous colorway). You may mention the source list price here as a hint for manual costing (e.g. "Source list ~249 EUR"). Never for the storefront. Never put that amount into initial_price. Null if nothing to flag.
"""


def _category_field_instructions(product_type: str) -> tuple[str, str]:
    if product_type == ProductType.RACKET:
        dedicated = """Racket fields (numeric unless noted):
• weight_grams — unstrung weight in grams (integer).
• head_size_sq_in — head size in square inches (integer).
• length_in — length in inches (e.g. 27 or 27.5).
• balance_mm — balance point in mm.
• swingweight — swingweight number if stated.
• string_pattern — e.g. 16x19 or 18x20.
• is_strung — true if sold strung, false if sold unstrung, null if unknown.
Leave gender, surface, width, material, length_m, capacity_rackets, balls_per_can null."""
        extra = """Composition / material
Beam (mm, e.g. 21.5 / 21.5 / 21.5)
Stiffness / RA
Grip / grip type
Recommended tension range
Playing style
Recommended level
Main benefits
String pattern note (if not already in string_pattern)
Technology names (only if confirmed)"""
        return dedicated, extra

    if product_type in SHOE_TYPES:
        dedicated = """Shoe fields:
• gender — M, W, J, or U. Must match the selected catalog type when obvious.
• surface — AC all-court, HC hard court, CL clay, GR grass, PD padel. Prefer AC when the shoe is all-court.
• width — last / width if stated (D, 2E, Wide, Standard, …).
Leave racket/string/bag/balls dedicated fields null.
Do not output per-size stock quantities."""
        extra = """Type (tennis shoe)
Court type (if more detail than surface code)
Upper
Midsole
Outsole
Support technology
Fit
Weight (per shoe, with unit)
Drop / stack if confirmed
Main benefits
Best for"""
        return dedicated, extra

    if product_type in APPAREL_TYPES:
        dedicated = """Apparel fields:
• gender — M, W, J, or U. Must match the selected catalog type when obvious.
• material — fabric (e.g. polyester, nylon blend).
Leave racket/shoe/string/bag/balls dedicated fields null except gender/material.
Do not output per-size stock quantities."""
        extra = """Type (polo, shorts, skirt, jacket, …)
Fit
Fabric technologies
Care
Main benefits
Best for"""
        return dedicated, extra

    if product_type == ProductType.STRINGS:
        dedicated = """String fields:
• material — polyester, multifilament, natural gut, hybrid, nylon, …
• length_m — metres per set or reel (e.g. 12 or 200).
Leave gender/surface/width/racket/bag/balls fields null.
Do not output per-gauge stock quantities. Available gauges go in extra attributes."""
        extra = """Type (polyester, multifilament, …)
Construction
Shape (round, shaped, …)
Gauge / available gauges (mm and/or gauge number)
Feel
Tension recommendation
Main benefits
Best for"""
        return dedicated, extra

    if product_type == ProductType.BAGS:
        dedicated = """Bag fields:
• capacity_rackets — integer racket count if stated.
Leave other dedicated type fields null."""
        extra = """Type (backpack, duffel, 6-pack, 9-pack, 12-pack, …)
Capacity (if not only racket count)
Compartments
Material
Thermal / racquet pocket
Dimensions
Main benefits
Best for"""
        return dedicated, extra

    if product_type == ProductType.BALLS:
        dedicated = """Ball fields:
• balls_per_can — integer count per can/tube.
• surface — AC / HC / CL / GR / PD when the ball is court-specific; otherwise AC or null.
Leave other dedicated type fields null."""
        extra = """Type (pressurized, pressureless, coaching, …)
Felt
ITF / ITF approved
Stage / junior stage if relevant
Main benefits
Best for"""
        return dedicated, extra

    if product_type == ProductType.GRIPS:
        dedicated = """Grip / overgrip: no extra model columns beyond identity + description.
Leave gender, surface, width, racket specs, length_m, capacity_rackets, balls_per_can null unless a material string belongs in material.
You MAY set material when the grip material is confirmed."""
        extra = """Type (Overgrip or Replacement grip)
Feel (tacky, dry, textured, …)
Material
Thickness
Width
Length
Pack size
Main benefits
Fit (standard / longbody racquets)
Best for"""
        return dedicated, extra

    dedicated = """Accessory / other: no extra model columns beyond identity + description.
Set material when confirmed. Leave racket/shoe/string/bag/balls dedicated numeric fields null."""
    extra = """Type
Material
Dimensions / size
Pack size
Main benefits
Best for
Any other verified spec"""
    return dedicated, extra


def extract_page_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html or '', 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'svg', 'iframe']):
        if tag.name == 'script' and (tag.get('type') or '').lower() == 'application/ld+json':
            continue
        tag.decompose()

    json_ld_chunks: list[str] = []
    for script in soup.find_all('script', attrs={'type': re.compile(r'application/ld\+json', re.I)}):
        raw = (script.string or script.get_text() or '').strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        product = _find_json_ld_product(data)
        if product:
            json_ld_chunks.append(json.dumps(product, ensure_ascii=False)[:8000])

    title = ''
    h1 = soup.find('h1')
    if h1:
        title = h1.get_text(' ', strip=True)
    if not title and soup.title:
        title = soup.title.get_text(' ', strip=True)

    meta_desc = ''
    md = soup.find('meta', attrs={'name': re.compile(r'^description$', re.I)})
    if md and md.get('content'):
        meta_desc = str(md['content']).strip()

    body_text = soup.get_text('\n', strip=True)
    body_text = re.sub(r'\n{3,}', '\n\n', body_text)
    parts = []
    if title:
        parts.append(f'Title: {title}')
    if meta_desc:
        parts.append(f'Meta description: {meta_desc}')
    parts.append(body_text)
    text = '\n\n'.join(parts)
    if len(text) > _PAGE_TEXT_LIMIT:
        text = text[:_PAGE_TEXT_LIMIT] + '\n…'
    json_ld = '\n\n'.join(json_ld_chunks)
    return text, json_ld


def _find_json_ld_product(data):
    if isinstance(data, list):
        for item in data:
            found = _find_json_ld_product(item)
            if found:
                return found
        return None
    if not isinstance(data, dict):
        return None
    types = data.get('@type')
    type_list = types if isinstance(types, list) else [types]
    type_norm = {str(t).lower() for t in type_list if t}
    if 'product' in type_norm:
        return data
    graph = data.get('@graph')
    if isinstance(graph, list):
        return _find_json_ld_product(graph)
    return None


def _looks_blocked(status_code: int, html: str) -> bool:
    if status_code in (202, 401, 403, 429):
        return True
    sample = (html or '')[:4000].lower()
    return any(marker in sample for marker in _BLOCK_MARKERS)


def fetch_product_page(url: str) -> str:
    import httpx

    current = validate_source_url(url, resolve=True)
    last_error = 'Could not load the product page.'
    with httpx.Client(timeout=_FETCH_TIMEOUT, headers=_FETCH_HEADERS, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS):
            try:
                response = client.get(current)
            except httpx.TimeoutException as exc:
                raise ListingFillError('Timed out loading the product page.') from exc
            except httpx.HTTPError as exc:
                raise ListingFillError(f'Could not load the product page: {exc}') from exc
            if response.is_redirect:
                loc = response.headers.get('location')
                if not loc:
                    raise ListingFillError('Product page redirect was empty.')
                current = validate_source_url(urljoin(current, loc), resolve=True)
                continue
            html = response.text or ''
            if _looks_blocked(response.status_code, html):
                raise ListingFillError(
                    'The product page is blocked (anti-bot / login wall). Try another URL.'
                )
            if response.status_code >= 400:
                last_error = f'Product page returned HTTP {response.status_code}.'
                raise ListingFillError(last_error)
            return html
    raise ListingFillError('Too many redirects on the product page.')


def _scrub_retailer_names(text: str) -> str:
    out = text or ''
    for pattern in _RETAILER_RES:
        out = pattern.sub('', out)
    out = re.sub(r'\s{2,}', ' ', out)
    out = re.sub(r'\s+([,.;:])', r'\1', out)
    return out.strip()


def validate_listing_payload(data: object, *, product_type: str) -> dict:
    if not isinstance(data, dict):
        raise ListingFillError('AI response was not a JSON object.')
    out: dict = {}
    for key in _STR_FIELDS:
        value = data.get(key)
        if value is None or value == '':
            out[key] = ''
        elif isinstance(value, str):
            out[key] = _scrub_retailer_names(value.strip())
        else:
            out[key] = _scrub_retailer_names(str(value).strip())

    for key in _INT_FIELDS:
        value = data.get(key)
        if value in (None, ''):
            out[key] = None
            continue
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ListingFillError(f'Invalid integer for {key}.') from exc
        if number < 0:
            out[key] = None
        else:
            out[key] = number

    for key in _DEC_FIELDS:
        value = data.get(key)
        if value in (None, ''):
            out[key] = None
            continue
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ListingFillError(f'Invalid number for {key}.') from exc
        if number < 0:
            out[key] = None
        else:
            out[key] = str(number)

    gender = (data.get('gender') or '').strip().upper() if isinstance(data.get('gender'), str) else ''
    if gender in GENDER_VALUES:
        out['gender'] = gender
    else:
        out['gender'] = TYPE_GENDER.get(product_type, '')

    surface = (data.get('surface') or '').strip().upper() if isinstance(data.get('surface'), str) else ''
    out['surface'] = surface if surface in SURFACE_VALUES else ''

    is_strung = data.get('is_strung')
    if isinstance(is_strung, bool):
        out['is_strung'] = is_strung
    else:
        out['is_strung'] = None

    attrs_in = data.get('attributes') or []
    attributes: dict[str, str] = {}
    if isinstance(attrs_in, dict):
        items = [{'parameter': k, 'value': v} for k, v in attrs_in.items()]
    elif isinstance(attrs_in, list):
        items = attrs_in
    else:
        items = []
    skip_keys = {
        'product name',
        'name',
        'color',
        'colour',
        'brand',
        'sku',
        'price',
        'msrp',
        'rrp',
        'list price',
        'retail price',
        'sale price',
    }
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get('parameter') or item.get('key') or '').strip()
        val = item.get('value')
        if val is None:
            continue
        val_s = _scrub_retailer_names(str(val).strip())
        if not key or not val_s or key.lower() in skip_keys:
            continue
        attributes[key] = val_s
    out['attributes'] = attributes

    sku = re.sub(r'[^A-Za-z0-9]', '', out.get('sku') or '')
    out['sku'] = sku[:64]

    if out.get('short_description') and len(out['short_description']) > 255:
        out['short_description'] = out['short_description'][:252].rstrip() + '…'

    if product_type not in SHOE_TYPES and product_type not in APPAREL_TYPES:
        out['gender'] = ''
    if product_type not in SHOE_TYPES and product_type != ProductType.BALLS:
        out['surface'] = ''
    if product_type not in SHOE_TYPES:
        out['width'] = ''
    if product_type != ProductType.RACKET:
        for key in (
            'weight_grams',
            'head_size_sq_in',
            'length_in',
            'balance_mm',
            'swingweight',
            'string_pattern',
        ):
            out[key] = None
        out['is_strung'] = None
    if product_type not in APPAREL_TYPES | {ProductType.STRINGS, ProductType.GRIPS, ProductType.BAGS}:
        out['material'] = ''
    if product_type != ProductType.STRINGS:
        out['length_m'] = None
    if product_type != ProductType.BAGS:
        out['capacity_rackets'] = None
    if product_type != ProductType.BALLS:
        out['balls_per_can'] = None

    out['initial_price'] = None
    out['price_currency'] = ''
    out.pop('price_note', None)

    if not out.get('name') and not out.get('brand'):
        raise ListingFillError('AI could not identify the product. Try another URL.')
    return out


def fill_listing_from_url(*, url: str, product_type: str) -> dict:
    if product_type not in ALLOWED_TYPES:
        raise ListingFillError('Unknown product type.')
    html = fetch_product_page(url)
    page_text, json_ld = extract_page_text(html)
    if len(re.sub(r'\s+', '', page_text)) < 80:
        raise ListingFillError('Could not read enough product content from that page.')

    user_parts = [
        f'Catalog type: {dict(ProductType.choices).get(product_type, product_type)} ({product_type}).',
        'Write storefront copy for OUR shop. Never mention the source site.',
    ]
    if json_ld:
        user_parts.append('STRUCTURED DATA (JSON-LD):\n' + json_ld)
    user_parts.append('PAGE TEXT:\n' + page_text)
    user_content = '\n\n'.join(user_parts)

    from buying.ai.base import AIProviderError, AIProviderNotConfigured
    from buying.ai.providers.openai_provider import OpenAIProvider

    provider = OpenAIProvider(
        api_key=getattr(settings, 'BUYING_OPENAI_API_KEY', '') or '',
        timeout=_LLM_TIMEOUT,
    )
    try:
        provider._require_key()
        raw = provider._chat(
            model=provider.normalization_model,
            system_prompt=system_prompt_for_type(product_type),
            user_content=user_content,
            json_schema=LISTING_JSON_SCHEMA,
        )
        parsed = provider._extract_json(raw)
    except AIProviderNotConfigured as exc:
        raise ListingFillError(str(exc)) from exc
    except AIProviderError as exc:
        raise ListingFillError(f'AI draft failed: {exc}') from exc

    return validate_listing_payload(parsed, product_type=product_type)


def start_listing_fill_job(*, url: str, product_type: str) -> str:
    validate_source_url(url, resolve=False)
    pt = (product_type or '').strip()
    if pt not in ALLOWED_TYPES:
        raise ListingFillError('Unknown product type.')
    if not (getattr(settings, 'BUYING_OPENAI_API_KEY', '') or '').strip():
        raise ListingFillError('OpenAI API key is not configured (OPENAI_API_KEY).')
    job_id = uuid.uuid4().hex
    cache.set(_job_key(job_id), {'ok': True, 'status': 'pending'}, _JOB_TTL)
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, url, pt),
        daemon=True,
        name=f'shop-ai-fill-{job_id[:8]}',
    )
    thread.start()
    return job_id


def get_listing_fill_job(job_id: str) -> dict | None:
    cleaned = re.sub(r'[^a-fA-F0-9]', '', job_id or '')
    if len(cleaned) != 32:
        return None
    payload = cache.get(_job_key(cleaned.lower()))
    return payload if isinstance(payload, dict) else None


def _run_job(job_id: str, url: str, product_type: str) -> None:
    close_old_connections()
    try:
        fields = fill_listing_from_url(url=url, product_type=product_type)
        cache.set(
            _job_key(job_id),
            {'ok': True, 'status': 'ok', 'fields': fields, 'prompt_version': LISTING_PROMPT_VERSION},
            _JOB_TTL,
        )
    except ListingFillError as exc:
        logger.info('AI listing fill failed: %s', exc)
        cache.set(
            _job_key(job_id),
            {'ok': False, 'status': 'error', 'error': str(exc)},
            _JOB_TTL,
        )
    except Exception:
        logger.exception('AI listing fill crashed')
        cache.set(
            _job_key(job_id),
            {'ok': False, 'status': 'error', 'error': 'AI draft failed unexpectedly.'},
            _JOB_TTL,
        )
    finally:
        close_old_connections()
