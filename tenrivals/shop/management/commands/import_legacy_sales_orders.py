"""Import legacy retail invoices from a line-items spreadsheet.

Creates SalesOrder / SalesOrderLine rows as free-text (product=None) with a
guessed ProductType category — same shape as unticking "In stock" on the staff
order form. Does not touch warehouse stock. Payment method, sale_channel and
landed costs stay blank/default for manual fill later.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from shop.models import (
    Customer,
    ProductType,
    SalesInvoiceYearSequence,
    SalesOrder,
    SalesOrderLine,
)
from shop.sales_order_currency import apply_payment_currency_fields
from shop.sales_order_utils import compute_order_totals, line_amounts


WALK_IN_FIRST = 'Walk-in'
WALK_IN_LAST = 'Customer'
LEGACY_NOTE = 'Legacy import (sales_line_items.xlsx)'

# Explicit overrides when keyword heuristics are ambiguous.
ITEM_TYPE_OVERRIDES: dict[str, str] = {
    'wilson bldv9 clshv2 psv14 bt172562 tennis racket buttcap red 3': ProductType.ACCESSORIES,
    'wilson infrared non-ps bt155712 tennis racket buttcap': ProductType.ACCESSORIES,
    'wilson blade v8/clash v2 butt cap (2)': ProductType.ACCESSORIES,
    'wilson retro racket cover / sycamore': ProductType.ACCESSORIES,
    'wilson absorbx padel black / 3-roll': ProductType.GRIPS,
}


def _norm_key(s: str) -> str:
    s = unicodedata.normalize('NFKC', (s or '').strip().lower())
    s = re.sub(r'\s+', ' ', s)
    return s


def guess_product_type(title: str) -> str:
    """Map free-text item title → ProductType for analytics."""
    raw = (title or '').strip()
    key = _norm_key(raw)
    if key in ITEM_TYPE_OVERRIDES:
        return ITEM_TYPE_OVERRIDES[key]

    t = key

    if any(x in t for x in ('dampen', 'damper', 'vibration damper', 'vibration damp')):
        return ProductType.DAMPENERS

    if any(
        x in t
        for x in (
            'ball can',
            'balls can',
            '3-ball',
            '4 ball',
            '4 balls',
            'championship',
            'roland garros',
            'rolland garros',
            'tecnifibre champion',
        )
    ) and 'racket' not in t and 'racquet' not in t:
        return ProductType.BALLS

    if any(x in t for x in ('shoe', 'shoes', 'adizero', 'gel dedicate', 'gel game', 'gel challenger', 'court lite', 'gp challenge')):
        if any(x in t for x in ("women's", 'womens', 'wom ', ' women', 'w sho')):
            return ProductType.WOMENS_SHOES
        return ProductType.MENS_SHOES

    if any(x in t for x in ('short', 'shorts', 'polo')):
        if any(x in t for x in ("women's", 'womens')):
            return ProductType.WOMENS_APPAREL
        return ProductType.MENS_APPAREL

    if any(
        x in t
        for x in (
            'racket',
            'racquet',
            'ezone',
            'vcore',
            'pro staff',
            'intrigue',
            'blade ',
            'ultra team',
            'speed mp',
            'clash',
        )
    ) and 'butt' not in t and 'cover' not in t and 'string' not in t:
        return ProductType.RACKET

    if any(
        x in t
        for x in (
            'string',
            'strings',
            'o-toro',
            'o-toro',
            'wasabi',
            'luxilon',
            'polytour',
            'sensation',
        )
    ) and 'overgrip' not in t:
        # Strung racket bundles still count as racket if clearly a frame title.
        if re.search(r'\b(g\d|l\d|size g)\b', t) and any(
            x in t for x in ('ezone', 'vcore', 'blade', 'pro staff', 'yonex ezone')
        ):
            return ProductType.RACKET
        return ProductType.STRINGS

    if any(
        x in t
        for x in (
            'overgrip',
            'super grap',
            'tourna grip',
            'mega tac',
            'replacement grip',
            'absorbx',
            'tour overgrip',
        )
    ):
        return ProductType.GRIPS

    if any(x in t for x in ('buttcap', 'butt cap', 'cover')):
        return ProductType.ACCESSORIES

    return ProductType.OTHER


def parse_discount_percent(raw) -> Decimal:
    """Excel stores discount as a fraction (0.1=10%); model uses 0–100 percent."""
    if raw is None or raw == '':
        return Decimal('0.00')
    try:
        d = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return Decimal('0.00')
    if d < 0:
        return Decimal('0.00')
    # Fraction form (0–1] → percent. Exact 1 means 100% gift.
    if d <= 1:
        return (d * Decimal('100')).quantize(Decimal('0.01'))
    return min(d, Decimal('100')).quantize(Decimal('0.01'))


def parse_order_date(raw) -> datetime.date:
    if isinstance(raw, datetime):
        return raw.date()
    if hasattr(raw, 'year') and hasattr(raw, 'month') and hasattr(raw, 'day'):
        return raw  # already date
    s = str(raw or '').strip()
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f'Unrecognized date: {raw!r}')


def split_customer_name(raw: str | None) -> tuple[str, str, str, str]:
    """Return (first_name, last_name, name_local, surname_local)."""
    name = (raw or '').strip()
    if not name:
        return WALK_IN_FIRST, WALK_IN_LAST, '', ''

    def has_georgian(s: str) -> bool:
        return any('\u10a0' <= ch <= '\u10ff' for ch in s)

    parts = name.split()
    if has_georgian(name):
        if len(parts) == 1:
            return parts[0], '', parts[0], ''
        return parts[0], ' '.join(parts[1:]), parts[0], ' '.join(parts[1:])

    if len(parts) == 1:
        return parts[0], '', '', ''
    # "Pachaeva Margarita" / "Belova Alena" style (Last First) is uncommon in
    # this file for Latin names — treat first token as first name.
    return parts[0], ' '.join(parts[1:]), '', ''


def _customer_match_key(first: str, last: str) -> str:
    return _norm_key(f'{first} {last}'.strip())


class Command(BaseCommand):
    help = (
        'Import legacy sales invoices from sales_line_items.xlsx as free-text '
        'lines (no stock). Preserves invoice numbers and order dates.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'xlsx',
            nargs='?',
            default='',
            help='Path to sales_line_items.xlsx (default: shop/data/sales_line_items.xlsx)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse and report without writing to the database.',
        )
        parser.add_argument(
            '--status',
            default=SalesOrder.Status.COMPLETED,
            choices=[c.value for c in SalesOrder.Status],
            help='Status for imported orders (default: COMPLETED).',
        )

    def handle(self, *args, **options):
        try:
            import openpyxl
        except ImportError as exc:
            raise CommandError('openpyxl is required: pip install openpyxl') from exc

        path = Path(options['xlsx'] or '').expanduser()
        if not options['xlsx']:
            path = Path(__file__).resolve().parents[2] / 'data' / 'sales_line_items.xlsx'
        if not path.is_file():
            raise CommandError(f'File not found: {path}')

        dry = bool(options['dry_run'])
        status = options['status']

        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            raise CommandError('Empty workbook')
        header = [str(c or '').strip() for c in rows[0]]
        expected = [
            'Invoice No',
            'Date',
            'Payment Method',
            'Customer',
            'Item',
            'Qty',
            'Unit Price',
            'Discount %',
            'Line Total',
            'VAT',
        ]
        if header[:10] != expected:
            raise CommandError(f'Unexpected header: {header[:10]!r}')

        by_invoice: dict[str, list] = defaultdict(list)
        for raw in rows[1:]:
            if not raw or not raw[0]:
                continue
            inv = str(raw[0]).strip()
            by_invoice[inv].append(raw)

        # Prefetch customers for matching
        customers = list(Customer.objects.all().only('id', 'first_name', 'last_name', 'name_local', 'surname_local'))
        by_key: dict[str, list[Customer]] = defaultdict(list)
        for c in customers:
            by_key[_customer_match_key(c.first_name, c.last_name)].append(c)
            local = _customer_match_key(c.name_local, c.surname_local)
            if local.strip():
                by_key[local].append(c)

        existing_invoices = set(
            SalesOrder.objects.filter(invoice_number__in=by_invoice.keys()).values_list(
                'invoice_number', flat=True
            )
        )

        created_orders = 0
        created_lines = 0
        skipped = 0
        created_customers = 0
        type_counts: dict[str, int] = defaultdict(int)
        problems: list[str] = []

        def resolve_customer(raw_name: str | None) -> Customer:
            nonlocal created_customers
            first, last, nloc, sloc = split_customer_name(raw_name)
            key = _customer_match_key(first, last)
            hits = by_key.get(key) or []
            if not hits and nloc:
                hits = by_key.get(_customer_match_key(nloc, sloc)) or []
            # Single-token first name match when unique (Vlad, Sophia, Ekaterina).
            if not hits and first and not last:
                cand = [
                    c
                    for c in customers
                    if _norm_key(c.first_name) == _norm_key(first)
                ]
                if len(cand) == 1:
                    hits = cand
            # "Alexander K" → Alexander Ka / Alexander Kukh… when unique prefix.
            if not hits and first and last and len(last) == 1:
                cand = [
                    c
                    for c in customers
                    if _norm_key(c.first_name) == _norm_key(first)
                    and _norm_key(c.last_name).startswith(_norm_key(last))
                ]
                if len(cand) == 1:
                    hits = cand
            if hits:
                return hits[0]
            if dry:
                # Placeholder object (not saved).
                return Customer(first_name=first, last_name=last, name_local=nloc, surname_local=sloc)
            cust = Customer.objects.create(
                first_name=first,
                last_name=last,
                name_local=nloc,
                surname_local=sloc,
                source='legacy import',
                comment=LEGACY_NOTE,
            )
            created_customers += 1
            customers.append(cust)
            by_key[_customer_match_key(first, last)].append(cust)
            if nloc:
                by_key[_customer_match_key(nloc, sloc)].append(cust)
            return cust

        for inv in sorted(by_invoice.keys()):
            lines_raw = by_invoice[inv]
            if inv in existing_invoices:
                skipped += 1
                problems.append(f'SKIP existing {inv}')
                continue

            try:
                order_date = parse_order_date(lines_raw[0][1])
            except ValueError as exc:
                problems.append(f'{inv}: {exc}')
                continue

            # Prefer first non-empty customer on the invoice.
            cust_raw = None
            for r in lines_raw:
                val = r[3]
                if val is None:
                    continue
                if str(val).strip():
                    cust_raw = val
                    break

            line_specs = []
            for r in lines_raw:
                item = (str(r[4] or '')).strip()
                if not item:
                    problems.append(f'{inv}: empty item')
                    continue
                try:
                    qty = int(r[5])
                    unit = Decimal(str(r[6])).quantize(Decimal('0.01'))
                except Exception as exc:
                    problems.append(f'{inv}: bad qty/price for {item!r}: {exc}')
                    continue
                disc = parse_discount_percent(r[7])
                ptype = guess_product_type(item)
                type_counts[ptype] += 1
                lg, lv, ln = line_amounts(qty, unit, disc)
                # Prefer spreadsheet line total when it differs by rounding only
                # stay with engine amounts for consistency with staff UI.
                sheet_total = None
                try:
                    if r[8] is not None and r[8] != '':
                        sheet_total = Decimal(str(r[8])).quantize(Decimal('0.01'))
                except Exception:
                    sheet_total = None
                if sheet_total is not None and sheet_total != lg:
                    # Trust sheet total (source of truth for historical invoices).
                    from shop.sales_order_utils import gross_split_vat_net

                    lg = sheet_total
                    ln, lv = gross_split_vat_net(lg)
                line_specs.append(
                    {
                        'custom_label': item[:200],
                        'product_type': ptype,
                        'quantity': qty,
                        'unit_price_gross': unit,
                        'discount_percent': disc,
                        'lg': lg,
                        'lv': lv,
                        'ln': ln,
                    }
                )

            if not line_specs:
                problems.append(f'{inv}: no usable lines')
                continue

            customer = resolve_customer(cust_raw)
            gross, vat, net = compute_order_totals(
                [s['lg'] for s in line_specs],
                [],
                Decimal('0.00'),
            )

            self.stdout.write(
                f'{inv} {order_date} {customer} lines={len(line_specs)} gross={gross}'
            )
            for s in line_specs:
                self.stdout.write(
                    f"  [{s['product_type']}] {s['custom_label'][:70]} ×{s['quantity']} = {s['lg']}"
                )

            if dry:
                created_orders += 1
                created_lines += len(line_specs)
                continue

            with transaction.atomic():
                order = SalesOrder(
                    invoice_number=inv,
                    customer=customer,
                    order_date=order_date,
                    status=status,
                    payment_method='',
                    notes=LEGACY_NOTE,
                    gross_total=gross,
                    vat_total=vat,
                    net_total=net,
                    delivery_gross=Decimal('0.00'),
                    delivery_cost_gel=Decimal('0.00'),
                    services=[],
                )
                apply_payment_currency_fields(order, gross)
                order.save()
                for s in line_specs:
                    SalesOrderLine.objects.create(
                        order=order,
                        product=None,
                        custom_label=s['custom_label'],
                        product_type=s['product_type'],
                        variant_label='',
                        quantity=s['quantity'],
                        unit_price_gross=s['unit_price_gross'],
                        discount_percent=s['discount_percent'],
                        landed_cost_gel=None,
                        # Default STOCK; staff will adjust preorder/stock later.
                        sale_channel=SalesOrderLine.SaleChannel.STOCK,
                        line_gross=s['lg'],
                        line_vat=s['lv'],
                        line_net=s['ln'],
                    )
                created_orders += 1
                created_lines += len(line_specs)

        if not dry:
            # Keep year counters from going backwards for years we just filled.
            self._bump_year_sequences(by_invoice.keys())

        self.stdout.write('')
        self.stdout.write(
            self.style.SUCCESS(
                f'{"DRY RUN — " if dry else ""}'
                f'orders={created_orders} lines={created_lines} '
                f'customers_created={created_customers} skipped={skipped}'
            )
        )
        self.stdout.write('Categories:')
        for code, n in sorted(type_counts.items(), key=lambda x: (-x[1], x[0])):
            label = dict(ProductType.choices).get(code, code)
            self.stdout.write(f'  {n:3d}  {code} ({label})')
        if problems:
            self.stdout.write(self.style.WARNING(f'Notes ({len(problems)}):'))
            for p in problems[:40]:
                self.stdout.write(f'  - {p}')

    def _bump_year_sequences(self, invoice_numbers) -> None:
        """Ensure SalesInvoiceYearSequence.last_seq is at least the highest
        *standard* (<100000) imported suffix per year — without lowering the
        current counter (production may already be ahead)."""
        from shop.sales_order_utils import parse_invoice_number

        per_year: dict[int, int] = {}
        for inv in invoice_numbers:
            parsed = parse_invoice_number(str(inv).strip())
            if not parsed:
                continue
            year, seq = parsed
            if seq >= 100000:
                continue
            per_year[year] = max(per_year.get(year, 0), seq)

        for year, seq in per_year.items():
            with transaction.atomic():
                row, _ = SalesInvoiceYearSequence.objects.select_for_update().get_or_create(
                    year=year,
                    defaults={'last_seq': seq},
                )
                if row.last_seq < seq:
                    row.last_seq = seq
                    row.save(update_fields=['last_seq'])
                    self.stdout.write(f'Sequence {year}: last_seq → {seq}')
