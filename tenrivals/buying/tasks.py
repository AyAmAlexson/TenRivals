"""Celery tasks for the Buying module."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger('buying')


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def fetch_nbg_rates(self, force: bool = False, rate_date: str | None = None):
    """Fetch official NBG FX rates and store FxRate rows.

    Scheduled morning + midday (UTC). Midday run is a no-op when today's rates
    are already present unless force=True.
    """
    from datetime import date

    from buying.integrations.nbg import NbgApiError
    from buying.services.fx_rates import sync_nbg_rates

    parsed_date = date.fromisoformat(rate_date) if rate_date else None
    try:
        return sync_nbg_rates(rate_date=parsed_date, force=force)
    except NbgApiError as exc:
        logger.error('NBG FX fetch task failed: %s', exc)
        raise self.retry(exc=exc)
