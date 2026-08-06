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


@shared_task(bind=True, max_retries=1, default_retry_delay=60)
def run_supplier_search(self, run_id: int, force: bool = False):
    from buying.services.search import execute_supplier_search

    result = execute_supplier_search(run_id, force=force)
    return result


@shared_task
def finalize_buying_search(buying_request_id: int):
    from buying.services.search import finalize_search

    return finalize_search(buying_request_id)


@shared_task
def health_check_supplier(supplier_id: int):
    from buying.models import Supplier
    from buying.services.search import run_health_check

    supplier = Supplier.objects.get(pk=supplier_id)
    row = run_health_check(supplier)
    return {'supplier': supplier.code, 'status': row.status}


@shared_task
def retry_failed_connectors(buying_request_id: int):
    from buying.models import BuyingRequest
    from buying.services.search import retry_failed

    request = BuyingRequest.objects.get(pk=buying_request_id)
    return retry_failed(request)
