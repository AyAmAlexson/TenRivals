"""Persist OfferData → SupplierOffer (+ ConnectorResponse)."""

from __future__ import annotations

from django.utils import timezone

from buying.connectors.base import OfferData
from buying.connectors.http import sanitize_for_storage
from buying.models import (
    ConnectorResponse,
    FreeShippingStatus,
    PurchaseContextStatus,
    Supplier,
    SupplierOffer,
    SupplierSearchResult,
    TaxDisplayMode,
    ValueSource,
)
from buying.services.eligibility import apply_offer_eligibility


_TAX_MAP = {
    'vat_included': TaxDisplayMode.VAT_INCLUDED,
    'vat_excluded': TaxDisplayMode.VAT_EXCLUDED,
    'sales_tax_at_checkout': TaxDisplayMode.SALES_TAX_AT_CHECKOUT,
    'destination_based_tax': TaxDisplayMode.DESTINATION_BASED_TAX,
    'no_local_tax': TaxDisplayMode.NO_LOCAL_TAX,
    'unknown': TaxDisplayMode.UNKNOWN,
    'prices_include_vat': TaxDisplayMode.PRICES_INCLUDE_VAT,
    'tax_at_checkout': TaxDisplayMode.TAX_AT_CHECKOUT,
    'sales_tax_by_state': TaxDisplayMode.SALES_TAX_BY_STATE,
}


def _tax_mode(value: str) -> str:
    return _TAX_MAP.get(value, TaxDisplayMode.UNKNOWN)


def _source(value: str) -> str:
    allowed = {c.value for c in ValueSource}
    return value if value in allowed else ValueSource.UNKNOWN


def offer_data_to_supplier_offer(
    *,
    buying_request,
    supplier: Supplier,
    data: OfferData,
    search_result: SupplierSearchResult | None = None,
    match_status: str = 'manual_review',
    match_score=None,
    match_details: dict | None = None,
    requested_variant: dict | None = None,
) -> SupplierOffer:
    payload = sanitize_for_storage(data.raw_payload if data.raw_payload is not None else {})
    if not isinstance(payload, dict):
        payload = {'body': payload}

    response = ConnectorResponse.objects.create(
        supplier=supplier,
        url=data.product_url or '',
        fetched_at=timezone.now(),
        content_type=data.content_type or '',
        payload=payload,
        parser_version=data.parser_version or '',
    )

    effective = data.effective_price
    ctx_status = (
        PurchaseContextStatus.CONFIRMED
        if data.purchase_context_status == 'confirmed' and data.destination_selection_confirmed
        else PurchaseContextStatus.UNCONFIRMED
    )
    fs_status = data.free_shipping_status or FreeShippingStatus.THRESHOLD_UNKNOWN
    if fs_status not in {c.value for c in FreeShippingStatus}:
        fs_status = FreeShippingStatus.THRESHOLD_UNKNOWN

    offer = SupplierOffer(
        buying_request=buying_request,
        supplier=supplier,
        search_result=search_result,
        connector_response=response,
        is_manual=False,
        title=data.title[:300],
        product_url=data.product_url or '',
        supplier_sku=data.supplier_sku or '',
        manufacturer_code=data.manufacturer_code or '',
        ean=data.ean or '',
        upc=data.upc or '',
        original_price=data.original_price,
        current_price=effective,
        displayed_price=data.displayed_price,
        effective_price=effective,
        public_price=data.public_price,
        authenticated_price=data.authenticated_price,
        authentication_status=data.authentication_status or 'not_applicable',
        discount_amount=data.product_discount_amount,
        discount_percent=data.product_discount_percent,
        currency=data.currency,
        promotions=[p.to_dict() if hasattr(p, 'to_dict') else p for p in data.promotions],
        applied_promotion_ids=list(data.applied_promotion_ids),
        unapplied_eligible_promotions=[
            p.to_dict() if hasattr(p, 'to_dict') else p for p in data.unapplied_eligible_promotions
        ],
        requested_variant=requested_variant or {},
        available_variants=list(data.available_variants),
        requested_variant_available=data.requested_variant_available,
        stock_status=data.stock_status or SupplierOffer.StockStatus.UNKNOWN,
        color=data.color or '',
        size=data.size or '',
        grip_size=data.grip_size or '',
        court=data.court or '',
        gender=data.gender or '',
        weight_g_actual=data.weight_g,
        length_cm=data.length_cm,
        width_cm=data.width_cm,
        height_cm=data.height_cm,
        local_shipping_cost=data.local_shipping_cost,
        local_shipping_source=_source(data.local_shipping_source),
        free_shipping_threshold=data.free_shipping_threshold,
        free_shipping_status=fs_status,
        free_shipping_currency=data.free_shipping_currency or '',
        threshold_basis=data.threshold_basis or 'unknown',
        amount_missing_for_free_shipping=data.amount_missing_for_free_shipping,
        tax_display_mode=_tax_mode(data.displayed_price_tax_mode),
        supplier_tax_amount=data.local_tax_amount,
        supplier_tax_source=_source(data.local_tax_source),
        selected_destination_country=data.selected_destination_country or '',
        selected_destination_postal_code=data.selected_destination_postal_code or '',
        destination_selection_source=data.destination_selection_source or '',
        destination_selection_confirmed=bool(data.destination_selection_confirmed),
        purchase_context_status=ctx_status,
        match_score=match_score,
        match_status=match_status,
        match_details=match_details or {},
        warnings=list(data.warnings),
        checked_at=timezone.now(),
        raw_data={},
    )
    return apply_offer_eligibility(offer)
