"""Strict JSON schemas for AI outputs + validation.

Validation is intentionally hand-rolled against the JSON schema below: it keeps
the project dependency-free (no pydantic) and gives explicit, testable errors.
Any AI provider response MUST pass validate_normalization() before it is used.
"""

from __future__ import annotations

CATEGORIES = [
    'racquet', 'shoes', 'string_reel', 'string_set', 'overgrip',
    'balls', 'apparel', 'bag', 'accessory',
]

NORMALIZATION_FIELDS_STR = [
    'brand', 'model', 'generation', 'category', 'gender', 'court',
    'size', 'size_system', 'grip_size', 'color', 'weight',
    'head_size', 'string_pattern', 'manufacturer_code', 'ean', 'upc',
]
NORMALIZATION_FIELDS_LIST = [
    'required_attributes', 'optional_attributes', 'uncertainties', 'aliases',
]

# Strict JSON schema handed to providers supporting structured output.
NORMALIZATION_JSON_SCHEMA = {
    'name': 'buying_normalization',
    'strict': True,
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            **{
                key: {'type': ['string', 'null']}
                for key in NORMALIZATION_FIELDS_STR
            },
            'category': {'type': ['string', 'null'], 'enum': CATEGORIES + [None]},
            'quantity': {'type': 'integer', 'minimum': 1},
            **{
                key: {'type': 'array', 'items': {'type': 'string'}}
                for key in NORMALIZATION_FIELDS_LIST
            },
        },
        'required': NORMALIZATION_FIELDS_STR + ['quantity'] + NORMALIZATION_FIELDS_LIST,
    },
}


class SchemaValidationError(Exception):
    pass


def validate_normalization(data: object) -> dict:
    """Validate and coerce a provider response into a clean normalization dict.

    Returns a dict with: str fields ('' when null), int quantity (>=1) and
    list-of-str fields. Raises SchemaValidationError on any violation.
    """
    if not isinstance(data, dict):
        raise SchemaValidationError('Normalization result must be a JSON object')

    unknown = set(data) - set(NORMALIZATION_FIELDS_STR) - set(NORMALIZATION_FIELDS_LIST) - {'quantity'}
    if unknown:
        raise SchemaValidationError(f'Unknown fields in normalization result: {sorted(unknown)}')

    clean: dict = {}
    for key in NORMALIZATION_FIELDS_STR:
        value = data.get(key)
        if value is None:
            clean[key] = ''
        elif isinstance(value, str):
            clean[key] = value.strip()
        else:
            raise SchemaValidationError(f'Field "{key}" must be a string or null')

    if clean['category'] and clean['category'] not in CATEGORIES:
        raise SchemaValidationError(f'Unknown category "{clean["category"]}"')

    quantity = data.get('quantity', 1)
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        raise SchemaValidationError('Field "quantity" must be an integer >= 1')
    clean['quantity'] = quantity

    for key in NORMALIZATION_FIELDS_LIST:
        value = data.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise SchemaValidationError(f'Field "{key}" must be a list of strings')
        clean[key] = [item.strip() for item in value if item.strip()]

    return clean


MATCH_STATUSES = [
    'exact',
    'alternative_color',
    'alternative_version',
    'manual_review',
    'no_match',
]

MATCH_JSON_SCHEMA = {
    'name': 'buying_match',
    'strict': True,
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'match_status': {'type': 'string', 'enum': MATCH_STATUSES},
            'match_score': {'type': 'number', 'minimum': 0, 'maximum': 1},
            'reasons': {'type': 'array', 'items': {'type': 'string'}},
        },
        'required': ['match_status', 'match_score', 'reasons'],
    },
}


def validate_match(data: object) -> dict:
    if not isinstance(data, dict):
        raise SchemaValidationError('Match result must be a JSON object')
    unknown = set(data) - {'match_status', 'match_score', 'reasons'}
    if unknown:
        raise SchemaValidationError(f'Unknown fields in match result: {sorted(unknown)}')

    status = data.get('match_status')
    if status not in MATCH_STATUSES:
        raise SchemaValidationError(f'Invalid match_status "{status}"')

    score = data.get('match_score')
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        raise SchemaValidationError('match_score must be a number')
    if score < 0 or score > 1:
        raise SchemaValidationError('match_score must be between 0 and 1')

    reasons = data.get('reasons', [])
    if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
        raise SchemaValidationError('reasons must be a list of strings')

    return {
        'match_status': status,
        'match_score': float(score),
        'reasons': [r.strip() for r in reasons if r.strip()],
    }
