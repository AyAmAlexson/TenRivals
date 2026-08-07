"""Connector DTOs and abstract SupplierConnector interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class PurchaseContext:
    destination_country: str
    destination_postal_code: str | None
    fulfillment_warehouse_id: int | None
    customer_mode: str  # public | authenticated | member
    authenticated: bool
    currency: str | None
    quantity: int
    coupon_codes: tuple[str, ...] = ()


@dataclass
class ConnectorCapabilities:
    search: bool = True
    product_details: bool = True
    variant_availability: bool = False
    destination_selection: bool = False
    tax_detection: bool = False
    promotion_detection: bool = False
    cart_simulation: bool = False
    shipping_calculation: bool = False
    authenticated_pricing: bool = False


@dataclass
class HealthCheckResult:
    status: str  # SupplierConnectorStatus.Status values
    response_time_ms: int | None = None
    http_status: int | None = None
    checked_url: str = ''
    error_type: str | None = None
    error_message: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class SearchCandidate:
    title: str
    url: str
    price_preview: Decimal | None = None
    currency: str | None = None
    supplier_sku: str | None = None
    manufacturer_code: str | None = None
    raw_data: dict = field(default_factory=dict)


@dataclass
class PromotionData:
    promotion_name: str
    promotion_type: str = ''
    promotion_value: str | None = None
    coupon_code: str = ''
    minimum_order_value: Decimal | None = None
    maximum_discount: Decimal | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    authentication_required: bool = False
    applicability_status: str = 'unknown'
    exclusion_reason: str = ''
    source: str = ''
    promotion_id: str = ''

    def to_dict(self) -> dict:
        return {
            'promotion_id': self.promotion_id or self.promotion_name,
            'promotion_name': self.promotion_name,
            'promotion_type': self.promotion_type,
            'promotion_value': str(self.promotion_value) if self.promotion_value is not None else None,
            'coupon_code': self.coupon_code,
            'minimum_order_value': (
                str(self.minimum_order_value) if self.minimum_order_value is not None else None
            ),
            'maximum_discount': (
                str(self.maximum_discount) if self.maximum_discount is not None else None
            ),
            'valid_from': self.valid_from,
            'valid_to': self.valid_to,
            'authentication_required': self.authentication_required,
            'applicability_status': self.applicability_status,
            'exclusion_reason': self.exclusion_reason,
            'source': self.source,
        }


@dataclass
class OfferData:
    title: str
    product_url: str

    original_price: Decimal | None
    displayed_price: Decimal
    effective_price: Decimal
    currency: str

    displayed_price_tax_mode: str = 'unknown'
    local_tax_amount: Decimal | None = None
    local_tax_source: str = 'unknown'

    product_discount_amount: Decimal | None = None
    product_discount_percent: Decimal | None = None

    promotions: list[PromotionData] = field(default_factory=list)
    applied_promotion_ids: list[str] = field(default_factory=list)
    unapplied_eligible_promotions: list[PromotionData] = field(default_factory=list)

    local_shipping_cost: Decimal | None = None
    local_shipping_source: str = 'unknown'
    free_shipping_threshold: Decimal | None = None
    free_shipping_status: str = 'threshold_unknown'
    free_shipping_currency: str | None = None
    threshold_basis: str = 'unknown'
    amount_missing_for_free_shipping: Decimal | None = None

    selected_destination_country: str | None = None
    selected_destination_postal_code: str | None = None
    destination_selection_source: str = ''
    destination_selection_confirmed: bool = False

    public_price: Decimal | None = None
    authenticated_price: Decimal | None = None
    authentication_status: str = 'not_applicable'

    available_variants: list[dict] = field(default_factory=list)
    requested_variant_available: bool | None = None
    stock_status: str = 'unknown'

    purchase_context_status: str = 'confirmed'  # confirmed | purchase_context_unconfirmed
    warnings: list[str] = field(default_factory=list)

    supplier_sku: str = ''
    manufacturer_code: str = ''
    ean: str = ''
    upc: str = ''
    color: str = ''
    size: str = ''
    grip_size: str = ''
    court: str = ''
    gender: str = ''
    weight_g: int | None = None
    length_cm: Decimal | None = None
    width_cm: Decimal | None = None
    height_cm: Decimal | None = None

    parser_version: str = ''
    raw_payload: dict | str | None = None
    content_type: str = 'text/html'


@dataclass
class AuthenticationStatus:
    status: str  # authenticated | session_expired | credentials_missing | …
    message: str = ''
    metadata: dict = field(default_factory=dict)


@dataclass
class AuthenticationResult:
    status: str
    message: str = ''
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedProductQuery:
    """Read-only query DTO derived from NormalizedProduct — no ORM in connectors."""

    brand: str = ''
    model_name: str = ''
    category: str = ''
    manufacturer_code: str = ''
    ean: str = ''
    upc: str = ''
    color: str = ''
    size: str = ''
    grip_size: str = ''
    court: str = ''
    gender: str = ''
    generation: str = ''
    aliases: tuple[str, ...] = ()
    quantity: int = 1
    raw_attributes: dict = field(default_factory=dict)

    @property
    def search_phrases(self) -> list[str]:
        """Build storefront search strings — include head size + generation when known.

        Without generation/head, stores return broad "Wilson Blade" noise and miss
        the specific Blade 100 V10 SKU.
        """
        parts = []
        for piece in (self.brand, self.model_name):
            piece = (piece or '').strip()
            if piece and piece not in parts:
                parts.append(piece)
        attrs = self.raw_attributes if isinstance(self.raw_attributes, dict) else {}
        head = str(attrs.get('head_size') or self.size or '').strip()
        if head and head not in parts:
            # Keep digits only for head size in the phrase (e.g. "100")
            head_digits = ''.join(ch for ch in head if ch.isdigit())
            if head_digits:
                parts.append(head_digits)
        gen = (self.generation or '').strip()
        if gen and gen.lower() not in {p.lower() for p in parts}:
            parts.append(gen)
        code = (self.manufacturer_code or '').strip()
        if code and code not in parts:
            parts.append(code)

        primary = ' '.join(parts).strip()
        phrases = [primary] if primary else []
        # Also try brand + model only (broader) as secondary
        short = ' '.join(
            p for p in ((self.brand or '').strip(), (self.model_name or '').strip()) if p
        ).strip()
        if short and short not in phrases:
            phrases.append(short)
        for alias in self.aliases:
            alias = (alias or '').strip()
            if alias and alias not in phrases:
                phrases.append(alias)
        if self.ean:
            phrases.append(self.ean)
        if self.upc:
            phrases.append(self.upc)
        return phrases or ['tennis']


@dataclass(frozen=True)
class ProductMappingRef:
    supplier_product_url: str
    supplier_sku: str = ''
    manufacturer_code: str = ''
    ean: str = ''
    upc: str = ''


class SupplierConnector(ABC):
    code: str = ''
    parser_version: str = '1'
    capabilities: ConnectorCapabilities = ConnectorCapabilities()

    def __init__(self, *, http_client=None, purchase_context: PurchaseContext | None = None):
        self.http = http_client
        self.purchase_context = purchase_context

    @abstractmethod
    def health_check(self) -> HealthCheckResult:
        ...

    @abstractmethod
    def search(self, query: NormalizedProductQuery) -> list[SearchCandidate]:
        ...

    @abstractmethod
    def get_product_details(
        self,
        candidate: SearchCandidate,
        query: NormalizedProductQuery,
    ) -> OfferData:
        ...

    def check_mapping(
        self,
        mapping: ProductMappingRef,
        query: NormalizedProductQuery,
    ) -> OfferData | None:
        candidate = SearchCandidate(
            title='',
            url=mapping.supplier_product_url,
            supplier_sku=mapping.supplier_sku or None,
            manufacturer_code=mapping.manufacturer_code or None,
        )
        return self.get_product_details(candidate, query)

    def authentication_status(self) -> AuthenticationStatus:
        return AuthenticationStatus(status='not_applicable')

    def login(self) -> AuthenticationResult:
        return AuthenticationResult(status='not_applicable', message='Public connector')

    def refresh_session(self) -> AuthenticationResult:
        return self.login()

    def build_default_context_warnings(self, offer: OfferData) -> OfferData:
        if not offer.destination_selection_confirmed:
            offer.purchase_context_status = 'purchase_context_unconfirmed'
            if 'Destination not confirmed' not in offer.warnings:
                offer.warnings.append('Destination not confirmed')
        if offer.displayed_price_tax_mode == 'unknown':
            if 'Tax mode unknown' not in offer.warnings:
                offer.warnings.append('Tax mode unknown')
        return offer
