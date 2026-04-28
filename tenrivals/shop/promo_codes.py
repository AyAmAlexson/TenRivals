"""Checkout promo code evaluation (GEL, VAT-inclusive cart lines)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.db.models import Count
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from shop.models import PromoCode


@dataclass
class PromoEvaluation:
    ok: bool
    discount_gross: Decimal
    message: str
    promo: PromoCode | None
    applicable_subtotal: Decimal
    eligible_line_indices: list[int]

    @property
    def promo_id(self) -> int | None:
        """``PromoCode`` PK when a row was loaded; ``None`` if no promo applies."""
        if self.promo is None:
            return None
        return int(self.promo.pk)


def normalize_promo_code(raw: str) -> str:
    if not isinstance(raw, str):
        raw = str(raw or '')
    return raw.strip().upper()


def _count_redemptions(promo: PromoCode) -> int:
    from shop.models import PromoRedemption

    return PromoRedemption.objects.filter(promo=promo).count()


def eligible_product_ids_for_promo(promo: PromoCode) -> set[int] | None:
    """
    None = whole catalog.
    Otherwise set of product PKs that count toward this promo's scope.
    """
    from shop.models import PromoCode  # noqa: PLC0415

    scope = promo.application_scope
    if scope == PromoCode.ApplicationScope.ALL:
        return None
    if scope == PromoCode.ApplicationScope.PRODUCTS:
        return set(promo.restricted_products.values_list('pk', flat=True))
    ids: set[int] = set()
    for coll in promo.restricted_collections.all():
        ids.update(coll.products.values_list('pk', flat=True))
        for grp in coll.groups.prefetch_related('products').all():
            ids.update(grp.products.values_list('pk', flat=True))
    return ids


def evaluate_promo_for_cart_rows(
    *,
    code: str,
    rows: list[dict[str, Any]],
    user: AbstractUser | None,
) -> PromoEvaluation:
    """rows: same shape as build_cart_page_rows (product, line_total Decimal, qty int, ...)."""
    from shop.models import PromoCode  # noqa: PLC0415

    zero = Decimal('0.00')
    code_n = normalize_promo_code(code)
    if not code_n:
        return PromoEvaluation(
            ok=True,
            discount_gross=zero,
            message='',
            promo=None,
            applicable_subtotal=zero,
            eligible_line_indices=[],
        )

    promo = (
        PromoCode.objects.filter(code__iexact=code_n)
        .prefetch_related('restricted_products', 'restricted_collections', 'restricted_collections__groups__products')
        .first()
    )
    if promo is None:
        return PromoEvaluation(False, zero, 'Unknown promo code.', None, zero, [])

    if not promo.is_manually_active:
        return PromoEvaluation(False, zero, 'This promo code is inactive.', promo, zero, [])

    now = timezone.now()
    if promo.valid_from and now < promo.valid_from:
        return PromoEvaluation(False, zero, 'This promo code is not active yet.', promo, zero, [])
    if promo.valid_until and now > promo.valid_until:
        return PromoEvaluation(False, zero, 'This promo code has expired.', promo, zero, [])

    uses = _count_redemptions(promo)
    if promo.single_use_globally and uses >= 1:
        return PromoEvaluation(
            False,
            zero,
            'This promo code has already been used.',
            promo,
            zero,
            [],
        )
    if promo.max_redemptions is not None and uses >= promo.max_redemptions:
        return PromoEvaluation(False, zero, 'This promo code is no longer available.', promo, zero, [])

    if promo.restricted_to_user_id:
        if user is None or not getattr(user, 'is_authenticated', False):
            return PromoEvaluation(
                False,
                zero,
                'Sign in with the account this promo code belongs to.',
                promo,
                zero,
                [],
            )
        if int(user.pk) != int(promo.restricted_to_user_id):
            return PromoEvaluation(
                False,
                zero,
                'This promo code is linked to another account.',
                promo,
                zero,
                [],
            )

    allowed_ids = eligible_product_ids_for_promo(promo)
    eligible_idx: list[int] = []
    applicable = zero
    for i, r in enumerate(rows):
        p = r.get('product')
        if p is None:
            continue
        pid = int(p.pk)
        if allowed_ids is not None and pid not in allowed_ids:
            continue
        eligible_idx.append(i)
        lt = r.get('line_total')
        applicable += Decimal(str(lt)).quantize(Decimal('0.01')) if lt is not None else zero

    if applicable <= zero:
        return PromoEvaluation(
            False,
            zero,
            'This promo does not apply to any items in your cart.',
            promo,
            zero,
            [],
        )

    raw_discount = zero
    if promo.discount_type == PromoCode.DiscountType.FIXED_GROSS:
        if promo.fixed_amount_gross is None or promo.fixed_amount_gross <= zero:
            return PromoEvaluation(False, zero, 'Promo is misconfigured (fixed amount).', promo, applicable, [])
        cap_pct = promo.fixed_cap_percent_of_eligible
        if cap_pct is None or cap_pct < zero:
            cap_pct = Decimal('100')
        cap_pct = min(Decimal('100'), max(zero, cap_pct))
        pct_cap_amt = (applicable * cap_pct / Decimal('100')).quantize(Decimal('0.01'))
        raw_discount = min(promo.fixed_amount_gross, applicable, pct_cap_amt)
    elif promo.discount_type == PromoCode.DiscountType.PERCENT:
        if promo.percent_off is None or promo.percent_off <= zero:
            return PromoEvaluation(False, zero, 'Promo is misconfigured (percent).', promo, applicable, [])
        floor = promo.percent_min_eligible_subtotal or zero
        if applicable < floor:
            return PromoEvaluation(
                False,
                zero,
                f'Eligible subtotal must be at least {floor} ₾ for this promo.',
                promo,
                applicable,
                eligible_idx,
            )
        pct = min(Decimal('100'), max(zero, promo.percent_off))
        raw_discount = (applicable * pct / Decimal('100')).quantize(Decimal('0.01'))
    else:
        return PromoEvaluation(False, zero, 'Promo is misconfigured.', promo, applicable, [])

    raw_discount = min(raw_discount, applicable).quantize(Decimal('0.01'))
    if raw_discount <= zero:
        return PromoEvaluation(
            False,
            zero,
            'Promo does not reduce this order (check minimum amounts or percent).',
            promo,
            applicable,
            eligible_idx,
        )

    return PromoEvaluation(
        True,
        raw_discount,
        '',
        promo,
        applicable,
        eligible_idx,
    )


def annotate_promo_usage_counts(qs):
    """Queryset of PromoCode → adds redemption_count annotation."""
    return qs.annotate(redemption_count=Count('redemptions', distinct=False))
