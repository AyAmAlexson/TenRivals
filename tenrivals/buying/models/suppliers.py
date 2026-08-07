"""Suppliers (shops), connector health, per-request search tracking, candidates,
verified offers and the ProductMapping knowledge base.

A SupplierOffer intentionally ends at 'shop data' (price, discount, local shipping,
local tax). International shipping and the customer price belong to CostScenario.
"""

from django.conf import settings
from django.db import models

from .requests import ProductCategory


class TaxDisplayMode(models.TextChoices):
    # Phase 3 canonical modes
    VAT_INCLUDED = 'vat_included', 'VAT included'
    VAT_EXCLUDED = 'vat_excluded', 'VAT excluded'
    SALES_TAX_AT_CHECKOUT = 'sales_tax_at_checkout', 'Sales tax at checkout'
    DESTINATION_BASED_TAX = 'destination_based_tax', 'Destination-based tax'
    NO_LOCAL_TAX = 'no_local_tax', 'No local tax'
    UNKNOWN = 'unknown', 'Unknown'
    # Legacy aliases (kept for existing rows / forms)
    PRICES_INCLUDE_VAT = 'prices_include_vat', 'Prices include VAT (legacy)'
    TAX_AT_CHECKOUT = 'tax_at_checkout', 'Tax added at checkout (legacy)'
    SALES_TAX_BY_STATE = 'sales_tax_by_state', 'Sales tax by state/address (legacy)'


class ValueSource(models.TextChoices):
    PARSED = 'parsed', 'Parsed'
    CHECKOUT_SIMULATION = 'checkout_simulation', 'Checkout simulation'
    CONFIGURED_RULE = 'configured_rule', 'Configured rule'
    HISTORICAL_ESTIMATE = 'historical_estimate', 'Historical estimate'
    MANUAL_OVERRIDE = 'manual_override', 'Manual override'
    MANUAL_ENTRY = 'manual_entry', 'Manual entry'
    UNKNOWN = 'unknown', 'Unknown'


class OnexApplicability(models.TextChoices):
    SUPPORTED = 'supported', 'Supported'
    UNSUPPORTED = 'unsupported', 'Unsupported'
    MANUAL_REVIEW = 'manual_review', 'Manual review'


class FreeShippingStatus(models.TextChoices):
    FREE_SHIPPING_REACHED = 'free_shipping_reached', 'Free shipping reached'
    PAID_SHIPPING = 'paid_shipping', 'Paid shipping'
    THRESHOLD_UNKNOWN = 'threshold_unknown', 'Threshold unknown'
    SHIPPING_CALCULATED_AT_CHECKOUT = 'shipping_calculated_at_checkout', 'Calculated at checkout'


class PurchaseContextStatus(models.TextChoices):
    CONFIRMED = 'confirmed', 'Confirmed'
    UNCONFIRMED = 'purchase_context_unconfirmed', 'Purchase context unconfirmed'


class ConfirmationState(models.TextChoices):
    CONFIRMED = 'confirmed', 'Confirmed'
    NOT_CONFIRMED = 'not_confirmed', 'Not confirmed'
    UNAVAILABLE = 'unavailable', 'Unavailable'
    UNKNOWN = 'unknown', 'Unknown'


class OfferEligibility(models.TextChoices):
    VERIFIED = 'verified', 'Verified'
    PARTIAL = 'partial', 'Partial'
    MANUAL_REVIEW = 'manual_review', 'Manual review'
    UNAVAILABLE = 'unavailable', 'Unavailable'
    REJECTED = 'rejected', 'Rejected'


class Supplier(models.Model):
    class ReturnComplexity(models.TextChoices):
        EASY = 'easy', 'Easy'
        MODERATE = 'moderate', 'Moderate'
        HARD = 'hard', 'Hard'
        UNKNOWN = 'unknown', 'Unknown'

    name = models.CharField(max_length=120)
    code = models.SlugField(max_length=60, unique=True, help_text='Connector registry key')
    base_url = models.URLField()
    country = models.CharField(
        max_length=2,
        help_text='ISO 3166-1 alpha-2 — Onex receiving warehouse country for local delivery',
    )
    currency = models.CharField(max_length=3, help_text='ISO 4217')
    connector_class = models.CharField(
        max_length=200, blank=True, help_text='Registry connector code; empty = manual-only'
    )
    enabled = models.BooleanField(default=True)
    onex_applicability = models.CharField(
        max_length=20,
        choices=OnexApplicability.choices,
        default=OnexApplicability.SUPPORTED,
        help_text='Whether an Onex CostScenario may be built for this supplier',
    )
    default_destination_country = models.CharField(
        max_length=2, blank=True,
        help_text='Default PurchaseContext destination (usually Onex warehouse country)',
    )
    default_destination_postal_code = models.CharField(max_length=20, blank=True)
    reliability_score = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True, help_text='0.00–1.00'
    )
    risk_score = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True, help_text='0.00–1.00'
    )
    average_delivery_days = models.PositiveIntegerField(
        null=True, blank=True, help_text='Local delivery to the forwarder warehouse'
    )
    return_complexity = models.CharField(
        max_length=10, choices=ReturnComplexity.choices, default=ReturnComplexity.UNKNOWN
    )
    tax_display_mode = models.CharField(
        max_length=25, choices=TaxDisplayMode.choices, default=TaxDisplayMode.UNKNOWN
    )
    free_shipping_threshold = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='Default threshold in supplier currency; connector data wins when available',
    )
    notes = models.TextField(blank=True)
    last_successful_check = models.DateTimeField(null=True, blank=True)
    last_failed_check = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        default_permissions = ()

    def __str__(self):
        return self.name


class SupplierConnectorStatus(models.Model):
    """History of connector health checks and failures."""

    class Status(models.TextChoices):
        AVAILABLE = 'available', 'Available'
        DEGRADED = 'degraded', 'Degraded'
        FAILED = 'failed', 'Failed'
        DISABLED = 'disabled', 'Disabled'
        AUTHENTICATION_REQUIRED = 'authentication_required', 'Authentication required'
        CAPTCHA_DETECTED = 'captcha_detected', 'Captcha detected'
        RATE_LIMITED = 'rate_limited', 'Rate limited'
        PARSING_ERROR = 'parsing_error', 'Parsing error'

    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='connector_statuses')
    status = models.CharField(max_length=30, choices=Status.choices)
    checked_at = models.DateTimeField(auto_now_add=True)
    response_time_ms = models.PositiveIntegerField(null=True, blank=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    checked_url = models.URLField(blank=True)
    error_type = models.CharField(max_length=60, blank=True)
    error_message = models.TextField(blank=True)
    stack_trace = models.TextField(blank=True)
    screenshot = models.ImageField(upload_to='buying/connector_screenshots/', null=True, blank=True)
    parser_version = models.CharField(max_length=40, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-checked_at']
        default_permissions = ()
        verbose_name_plural = 'Supplier connector statuses'

    def __str__(self):
        return f'{self.supplier} — {self.status} @ {self.checked_at:%Y-%m-%d %H:%M}'


class SupplierSearchRun(models.Model):
    """Per-request, per-supplier progress tracking ('Completed: 10 / 15')."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        RUNNING = 'running', 'Running'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        SKIPPED = 'skipped', 'Skipped'

    buying_request = models.ForeignKey(
        'buying.BuyingRequest', on_delete=models.CASCADE, related_name='search_runs'
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='search_runs')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    attempt = models.PositiveIntegerField(default=1)
    celery_task_id = models.CharField(max_length=60, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    candidates_found = models.PositiveIntegerField(default=0)
    offers_created = models.PositiveIntegerField(default=0)
    error_type = models.CharField(max_length=60, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=['buying_request', 'supplier'], name='uniq_search_run_per_supplier'
            )
        ]

    def __str__(self):
        return f'Run {self.buying_request_id}/{self.supplier.code}: {self.status}'


class SupplierSearchResult(models.Model):
    """Raw search candidate found in a shop, before verification."""

    buying_request = models.ForeignKey(
        'buying.BuyingRequest', on_delete=models.CASCADE, related_name='search_results'
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='search_results')
    title = models.CharField(max_length=300)
    url = models.URLField(max_length=600)
    price_preview = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    supplier_sku = models.CharField(max_length=80, blank=True)
    manufacturer_code = models.CharField(max_length=80, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f'{self.supplier.code}: {self.title[:60]}'


class ConnectorResponse(models.Model):
    """Sanitized technical payload from a connector fetch (not a business object)."""

    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='connector_responses')
    url = models.URLField(max_length=600)
    fetched_at = models.DateTimeField()
    content_type = models.CharField(max_length=80, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    parser_version = models.CharField(max_length=40, blank=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fetched_at']
        default_permissions = ()

    def __str__(self):
        return f'{self.supplier.code} @ {self.fetched_at:%Y-%m-%d %H:%M}'


class SupplierOffer(models.Model):
    """Verified shop offer: an IMMUTABLE snapshot of what the shop showed at
    checked_at. Ends at shop-level data; no international shipping, no customer
    price — those live in CostScenario.

    Never edited in place once scenarios exist: a price/availability change or a
    staff correction produces a NEW version (see services.offers.revise_offer),
    and the old row keeps answering "what did we see back then".
    """

    class StockStatus(models.TextChoices):
        IN_STOCK = 'in_stock', 'In stock'
        OUT_OF_STOCK = 'out_of_stock', 'Out of stock'
        PREORDER = 'preorder', 'Preorder'
        UNKNOWN = 'unknown', 'Unknown'

    class MatchStatus(models.TextChoices):
        EXACT = 'exact', 'Exact match'
        ALTERNATIVE_COLOR = 'alternative_color', 'Alternative color'
        ALTERNATIVE_VERSION = 'alternative_version', 'Alternative version'
        MANUAL_REVIEW = 'manual_review', 'Manual review'
        NO_MATCH = 'no_match', 'No match'

    buying_request = models.ForeignKey(
        'buying.BuyingRequest', on_delete=models.CASCADE, related_name='offers'
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='offers')
    search_result = models.ForeignKey(
        SupplierSearchResult, null=True, blank=True, on_delete=models.SET_NULL, related_name='offers'
    )
    connector_response = models.ForeignKey(
        ConnectorResponse, null=True, blank=True, on_delete=models.SET_NULL, related_name='offers'
    )
    is_manual = models.BooleanField(default=False, help_text='Entered by staff, not by a connector')
    version = models.PositiveIntegerField(default=1)
    superseded_by = models.OneToOneField(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='supersedes'
    )

    title = models.CharField(max_length=300)
    product_url = models.URLField(max_length=600, blank=True)
    supplier_sku = models.CharField(max_length=80, blank=True)
    manufacturer_code = models.CharField(max_length=80, blank=True)
    ean = models.CharField(max_length=20, blank=True)
    upc = models.CharField(max_length=20, blank=True)

    original_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    current_price = models.DecimalField(max_digits=12, decimal_places=2)
    displayed_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    effective_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    public_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    authenticated_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    authentication_status = models.CharField(max_length=40, blank=True, default='not_applicable')
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3)

    promotions = models.JSONField(default=list, blank=True)
    applied_promotion_ids = models.JSONField(default=list, blank=True)
    unapplied_eligible_promotions = models.JSONField(default=list, blank=True)

    requested_variant = models.JSONField(default=dict, blank=True)
    available_variants = models.JSONField(default=list, blank=True)
    requested_variant_available = models.BooleanField(null=True, blank=True)
    stock_status = models.CharField(
        max_length=15, choices=StockStatus.choices, default=StockStatus.UNKNOWN
    )
    stock_quantity = models.PositiveIntegerField(null=True, blank=True)
    color = models.CharField(max_length=60, blank=True)
    size = models.CharField(max_length=30, blank=True)
    grip_size = models.CharField(max_length=10, blank=True)
    court = models.CharField(max_length=30, blank=True)
    gender = models.CharField(max_length=20, blank=True)
    weight_g_actual = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Parcel / shipping weight in grams (not product unstrung weight)',
    )
    # Package dimensions for the volumetric weight formula (L×W×H / divisor).
    length_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    width_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    height_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)

    local_shipping_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    local_shipping_source = models.CharField(
        max_length=25, choices=ValueSource.choices, default=ValueSource.CONFIGURED_RULE
    )
    free_shipping_threshold = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    free_shipping_status = models.CharField(
        max_length=40,
        choices=FreeShippingStatus.choices,
        default=FreeShippingStatus.THRESHOLD_UNKNOWN,
    )
    free_shipping_currency = models.CharField(max_length=3, blank=True)
    threshold_basis = models.CharField(max_length=40, blank=True, default='unknown')
    amount_missing_for_free_shipping = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    tax_display_mode = models.CharField(
        max_length=25, choices=TaxDisplayMode.choices, default=TaxDisplayMode.UNKNOWN
    )
    supplier_tax_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    supplier_tax_source = models.CharField(
        max_length=25, choices=ValueSource.choices, default=ValueSource.CONFIGURED_RULE
    )

    selected_destination_country = models.CharField(max_length=2, blank=True)
    selected_destination_postal_code = models.CharField(max_length=20, blank=True)
    destination_selection_source = models.CharField(max_length=40, blank=True)
    destination_selection_confirmed = models.BooleanField(default=False)
    purchase_context_status = models.CharField(
        max_length=40,
        choices=PurchaseContextStatus.choices,
        default=PurchaseContextStatus.CONFIRMED,
    )
    # Three independent confirmation axes (do not collapse into one "Confirmed" label)
    purchase_context_confirmed = models.CharField(
        max_length=20,
        choices=ConfirmationState.choices,
        default=ConfirmationState.UNKNOWN,
    )
    product_identity_confirmed = models.CharField(
        max_length=20,
        choices=ConfirmationState.choices,
        default=ConfirmationState.UNKNOWN,
    )
    requested_variant_confirmed = models.CharField(
        max_length=20,
        choices=ConfirmationState.choices,
        default=ConfirmationState.UNKNOWN,
    )
    eligibility_status = models.CharField(
        max_length=20,
        choices=OfferEligibility.choices,
        default=OfferEligibility.MANUAL_REVIEW,
    )

    match_score = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    match_status = models.CharField(
        max_length=25, choices=MatchStatus.choices, default=MatchStatus.MANUAL_REVIEW
    )
    match_details = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    checked_at = models.DateTimeField()
    # Deprecated for new connector rows — use connector_response. Kept for manual offers.
    raw_data = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='buying_offers_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        default_permissions = ()

    def __str__(self):
        return f'{self.supplier.code}: {self.title[:60]} (v{self.version})'

    @property
    def is_current(self) -> bool:
        return self.superseded_by_id is None


class ProductMapping(models.Model):
    """Knowledge base link: CanonicalProduct <-> a specific supplier product card."""

    class MappingStatus(models.TextChoices):
        SUGGESTED = 'suggested', 'Suggested'
        CONFIRMED = 'confirmed', 'Confirmed'
        REJECTED = 'rejected', 'Rejected'
        OUTDATED = 'outdated', 'Outdated'
        ALTERNATIVE = 'alternative', 'Alternative'
        DIFFERENT_GENERATION = 'different_generation', 'Different generation'

    class Source(models.TextChoices):
        # Provenance of the record; confirmation is tracked separately in
        # mapping_status/confirmed_by (a status, not an origin).
        AUTOMATIC = 'automatic', 'Automatic (matching pipeline)'
        MANUAL = 'manual', 'Manual (created by staff)'

    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    canonical_product = models.ForeignKey(
        'buying.CanonicalProduct', on_delete=models.CASCADE, related_name='mappings'
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='mappings')
    supplier_product_url = models.URLField(max_length=600)
    supplier_sku = models.CharField(max_length=80, blank=True)
    manufacturer_code = models.CharField(max_length=80, blank=True)
    ean = models.CharField(max_length=20, blank=True)
    upc = models.CharField(max_length=20, blank=True)
    supplier_title = models.CharField(max_length=300, blank=True)
    mapping_status = models.CharField(
        max_length=25, choices=MappingStatus.choices, default=MappingStatus.SUGGESTED
    )
    confidence = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='buying_mappings_confirmed',
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    confirmed_weight_g = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Historically confirmed parcel/shipping weight for the Shipping Weight Engine',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f'{self.canonical_product} @ {self.supplier.code} [{self.mapping_status}]'
