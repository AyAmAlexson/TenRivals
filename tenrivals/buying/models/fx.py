"""Official FX rates (National Bank of Georgia and future sources).

Separate from CalculationRule: rules remain for explicit manual overrides;
FxRate stores fetched official snapshots used by the Pricing Engine.
"""

from decimal import Decimal, ROUND_HALF_UP

from django.db import models


class FxRate(models.Model):
    """One official rate observation for a currency on a calendar day."""

    class Source(models.TextChoices):
        NBG = 'nbg', 'National Bank of Georgia'

    currency = models.CharField(max_length=3, db_index=True)
    # Official NBG figure for `quantity` units of the currency (not necessarily 1).
    rate_gel = models.DecimalField(max_digits=18, decimal_places=8)
    quantity = models.PositiveIntegerField(default=1)
    rate_date = models.DateField(db_index=True, help_text='Calendar day the rate is valid for')
    published_at = models.DateTimeField(null=True, blank=True)
    fetched_at = models.DateTimeField()
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.NBG)
    raw_data = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ['-rate_date', 'currency']
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=['currency', 'rate_date', 'source'],
                name='buying_fxrate_currency_date_source_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['currency', '-rate_date']),
        ]

    def __str__(self):
        return f'{self.currency} {self.rate_date}: {self.rate_gel}/{self.quantity} ({self.source})'

    @property
    def unit_rate_gel(self) -> Decimal:
        """GEL per 1 unit of currency — what the Pricing Engine uses."""
        if self.quantity <= 0:
            return Decimal('0')
        return (self.rate_gel / Decimal(self.quantity)).quantize(
            Decimal('0.00000001'), rounding=ROUND_HALF_UP
        )
