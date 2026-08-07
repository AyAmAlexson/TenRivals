"""Offer confirmation statuses and eligibility for ranking."""

from __future__ import annotations

from buying.models import SupplierOffer


class ConfirmationStatus:
    CONFIRMED = 'confirmed'
    NOT_CONFIRMED = 'not_confirmed'
    UNAVAILABLE = 'unavailable'
    UNKNOWN = 'unknown'


class EligibilityStatus:
    VERIFIED = 'verified'
    PARTIAL = 'partial'
    MANUAL_REVIEW = 'manual_review'
    UNAVAILABLE = 'unavailable'
    REJECTED = 'rejected'


def apply_offer_eligibility(offer: SupplierOffer) -> SupplierOffer:
    """Set the three confirmation fields and eligibility_status on an offer (unsaved OK)."""
    offer.purchase_context_confirmed = _purchase_context(offer)
    offer.product_identity_confirmed = _product_identity(offer)
    offer.requested_variant_confirmed = _variant(offer)
    offer.eligibility_status = _eligibility(offer)
    return offer


def _purchase_context(offer: SupplierOffer) -> str:
    if offer.destination_selection_confirmed and offer.purchase_context_status == 'confirmed':
        return ConfirmationStatus.CONFIRMED
    if offer.purchase_context_status == 'purchase_context_unconfirmed':
        return ConfirmationStatus.NOT_CONFIRMED
    if offer.destination_selection_confirmed:
        return ConfirmationStatus.CONFIRMED
    return ConfirmationStatus.UNKNOWN


def _product_identity(offer: SupplierOffer) -> str:
    if offer.match_status in (
        SupplierOffer.MatchStatus.EXACT,
        SupplierOffer.MatchStatus.ALTERNATIVE_COLOR,
    ):
        return ConfirmationStatus.CONFIRMED
    if offer.match_status == SupplierOffer.MatchStatus.NO_MATCH:
        return ConfirmationStatus.UNAVAILABLE
    if offer.match_status == SupplierOffer.MatchStatus.ALTERNATIVE_VERSION:
        return ConfirmationStatus.NOT_CONFIRMED
    return ConfirmationStatus.UNKNOWN


def _variant(offer: SupplierOffer) -> str:
    if offer.requested_variant_available is True:
        return ConfirmationStatus.CONFIRMED
    if offer.requested_variant_available is False:
        return ConfirmationStatus.UNAVAILABLE
    # No requested grip/size → N/A treated as confirmed for eligibility of non-variant products
    req = offer.requested_variant or {}
    if not (req.get('grip_size') or req.get('size')):
        return ConfirmationStatus.CONFIRMED
    return ConfirmationStatus.UNKNOWN


def _eligibility(offer: SupplierOffer) -> str:
    if offer.match_status == SupplierOffer.MatchStatus.NO_MATCH:
        return EligibilityStatus.REJECTED
    if offer.requested_variant_available is False:
        return EligibilityStatus.UNAVAILABLE
    if offer.stock_status == SupplierOffer.StockStatus.OUT_OF_STOCK:
        return EligibilityStatus.UNAVAILABLE

    has_price = offer.effective_price is not None or offer.current_price is not None
    identity_ok = offer.product_identity_confirmed == ConfirmationStatus.CONFIRMED
    context_ok = offer.purchase_context_confirmed == ConfirmationStatus.CONFIRMED
    variant_ok = offer.requested_variant_confirmed == ConfirmationStatus.CONFIRMED

    if identity_ok and context_ok and variant_ok and has_price:
        return EligibilityStatus.VERIFIED
    if offer.match_status == SupplierOffer.MatchStatus.MANUAL_REVIEW:
        return EligibilityStatus.MANUAL_REVIEW
    if has_price and identity_ok:
        return EligibilityStatus.PARTIAL
    return EligibilityStatus.MANUAL_REVIEW
