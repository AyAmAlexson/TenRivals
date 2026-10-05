"""Manufacturer barcodes keyed by product + size, separate from the qty JSON."""

from __future__ import annotations

from .models import Product, ProductBarcode


def normalize_barcode(raw: str) -> str:
    return ''.join((raw or '').split())


def lookup_barcode(raw: str) -> ProductBarcode | None:
    code = normalize_barcode(raw)
    if not code:
        return None
    return (
        ProductBarcode.objects.select_related('product')
        .filter(barcode=code)
        .first()
    )


def validate_barcode_pairs(pairs: list[tuple[str, str]], product_id: int | None) -> str | None:
    """``pairs`` is (variant_label, barcode). Empty barcode clears that variant."""
    seen: set[str] = set()
    for _variant, code in pairs:
        if not code:
            continue
        if code in seen:
            return f'Barcode {code} is entered more than once.'
        seen.add(code)
        other = ProductBarcode.objects.select_related('product').filter(barcode=code)
        if product_id:
            other = other.exclude(product_id=product_id)
        taken = other.first()
        if taken is not None:
            return f'Barcode {code} is already used by {taken.product}.'
    return None


def write_barcode_pairs(product: Product, pairs: list[tuple[str, str]]) -> None:
    """Replace the barcode of each listed variant. Empty code removes it."""
    for variant, code in pairs:
        variant = (variant or '')[:48]
        existing = list(
            ProductBarcode.objects.filter(product=product, variant_label=variant)
        )
        if not code:
            if existing:
                ProductBarcode.objects.filter(pk__in=[row.pk for row in existing]).delete()
            continue
        row = existing[0] if existing else None
        if len(existing) > 1:
            ProductBarcode.objects.filter(pk__in=[r.pk for r in existing[1:]]).delete()
        if row is None:
            ProductBarcode.objects.create(product=product, variant_label=variant, barcode=code)
        elif row.barcode != code:
            row.barcode = code
            row.save(update_fields=['barcode'])


def pairs_from_post(post, size_rows: list) -> list[tuple[str, str]]:
    if size_rows:
        pairs = []
        for i, row in enumerate(size_rows):
            label = row[0]
            pairs.append(((label or '')[:48], normalize_barcode(post.get(f'bc-{i}') or '')))
        return pairs
    return [('', normalize_barcode(post.get('barcode') or ''))]


def attach_barcode_inputs(form, product: Product | None, post=None) -> str:
    """Rewrite size rows to (label, qty, code) and return the single-product barcode."""
    existing = {}
    if product is not None and getattr(product, 'pk', None):
        existing = {
            row.variant_label: row.barcode
            for row in ProductBarcode.objects.filter(product=product)
        }
    size_rows = list(getattr(form, 'size_inventory_rows', []) or [])
    if size_rows:
        enriched = []
        for i, row in enumerate(size_rows):
            label, qty = row[0], row[1]
            code = existing.get(label, '')
            if post is not None:
                code = post.get(f'bc-{i}', code)
            enriched.append((label, qty, code))
        form.size_inventory_rows = enriched
        return ''
    code = existing.get('', '')
    if post is not None:
        code = post.get('barcode', code)
    return code
