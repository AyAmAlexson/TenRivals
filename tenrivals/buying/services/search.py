"""Orchestrate multi-supplier search for a BuyingRequest."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from buying.connectors.base import PurchaseContext
from buying.connectors.exceptions import ConnectorError
from buying.connectors.http import ConnectorHttpClient
from buying.connectors.registry import get_connector, get_connector_class
from buying.engine.routes import supplier_supports_onex
from buying.models import (
    BuyingRequest,
    OnexApplicability,
    Supplier,
    SupplierConnectorStatus,
    SupplierOffer,
    SupplierSearchResult,
    SupplierSearchRun,
)
from buying.services.matching import pick_best_candidates, query_from_normalized
from buying.services.offer_persist import offer_data_to_supplier_offer
from buying.services.offers import rebuild_scenarios_for_offer

logger = logging.getLogger('buying')


def build_purchase_context(supplier: Supplier, *, quantity: int = 1) -> PurchaseContext:
    country = supplier.default_destination_country or supplier.country
    return PurchaseContext(
        destination_country=country,
        destination_postal_code=supplier.default_destination_postal_code or None,
        fulfillment_warehouse_id=None,
        customer_mode='authenticated' if _wants_auth(supplier) else 'public',
        authenticated=_wants_auth(supplier),
        currency=supplier.currency,
        quantity=quantity,
        coupon_codes=(),
    )


def _wants_auth(supplier: Supplier) -> bool:
    return supplier.code in ('itf-tennis-point', 'central-tennis')


def cache_age_state(checked_at) -> str:
    """Return fresh | stale | expired based on settings hours."""
    if not checked_at:
        return 'expired'
    now = timezone.now()
    fresh_h = int(getattr(settings, 'BUYING_SEARCH_FRESH_HOURS', 6))
    stale_h = int(getattr(settings, 'BUYING_SEARCH_STALE_HOURS', 48))
    age = now - checked_at
    if age <= timedelta(hours=fresh_h):
        return 'fresh'
    if age <= timedelta(hours=stale_h):
        return 'stale'
    return 'expired'


def start_search(buying_request: BuyingRequest, *, force: bool = False) -> dict:
    """Create search runs for all enabled suppliers and dispatch work."""
    if not buying_request.normalized_product_id:
        raise ValueError('Buying request must be normalized before search')

    suppliers = list(Supplier.objects.filter(enabled=True).order_by('name'))
    runs = []
    with transaction.atomic():
        buying_request.status = BuyingRequest.Status.SEARCHING
        buying_request.save(update_fields=['status', 'updated_at'])
        for supplier in suppliers:
            run, _ = SupplierSearchRun.objects.update_or_create(
                buying_request=buying_request,
                supplier=supplier,
                defaults={
                    'status': SupplierSearchRun.Status.PENDING,
                    'attempt': 1,
                    'celery_task_id': '',
                    'started_at': None,
                    'finished_at': None,
                    'candidates_found': 0,
                    'offers_created': 0,
                    'error_type': '',
                    'error_message': '',
                },
            )
            if force:
                # bump attempt when forcing
                SupplierSearchRun.objects.filter(pk=run.pk).update(
                    attempt=run.attempt + (0 if run.attempt == 1 and not run.finished_at else 1)
                )
                run.refresh_from_db()
            runs.append(run)

    sync = getattr(settings, 'BUYING_SEARCH_SYNC_FALLBACK', True)
    dispatched = 0
    for run in runs:
        if sync:
            execute_supplier_search(run.pk, force=force)
            dispatched += 1
        else:
            from buying.tasks import run_supplier_search
            async_result = run_supplier_search.delay(run.pk, force=force)
            SupplierSearchRun.objects.filter(pk=run.pk).update(celery_task_id=async_result.id or '')
            dispatched += 1

    if sync:
        finalize_search(buying_request.pk)
    else:
        from buying.tasks import finalize_buying_search
        finalize_buying_search.delay(buying_request.pk)

    return {'runs': len(runs), 'dispatched': dispatched, 'sync': sync}


def retry_failed(buying_request: BuyingRequest) -> dict:
    failed = list(
        buying_request.search_runs.filter(status=SupplierSearchRun.Status.FAILED).select_related('supplier')
    )
    buying_request.status = BuyingRequest.Status.SEARCHING
    buying_request.save(update_fields=['status', 'updated_at'])
    sync = getattr(settings, 'BUYING_SEARCH_SYNC_FALLBACK', True)
    for run in failed:
        SupplierSearchRun.objects.filter(pk=run.pk).update(
            status=SupplierSearchRun.Status.PENDING,
            attempt=run.attempt + 1,
            error_type='',
            error_message='',
            finished_at=None,
        )
        if sync:
            execute_supplier_search(run.pk, force=True)
        else:
            from buying.tasks import run_supplier_search
            run_supplier_search.delay(run.pk, force=True)
    if sync:
        finalize_search(buying_request.pk)
    return {'retried': len(failed)}


def execute_supplier_search(run_id: int, *, force: bool = False) -> dict:
    run = SupplierSearchRun.objects.select_related(
        'supplier', 'buying_request', 'buying_request__normalized_product'
    ).get(pk=run_id)
    supplier = run.supplier
    request = run.buying_request
    np = request.normalized_product

    SupplierSearchRun.objects.filter(pk=run.pk).update(
        status=SupplierSearchRun.Status.RUNNING,
        started_at=timezone.now(),
    )

    code = supplier.connector_class or supplier.code
    if not get_connector_class(code):
        _fail_run(run, 'connector_not_ready', f'No connector registered for {code}')
        return {'status': 'failed', 'error_type': 'connector_not_ready'}

    # Search cache: reuse fresh current offer
    if not force:
        existing = (
            SupplierOffer.objects.filter(
                buying_request=request,
                supplier=supplier,
                superseded_by__isnull=True,
                is_manual=False,
            )
            .order_by('-checked_at')
            .first()
        )
        if existing and cache_age_state(existing.checked_at) == 'fresh':
            SupplierSearchRun.objects.filter(pk=run.pk).update(
                status=SupplierSearchRun.Status.COMPLETED,
                finished_at=timezone.now(),
                candidates_found=0,
                offers_created=0,
                error_message='cache_hit_fresh',
            )
            return {'status': 'completed', 'cache': 'fresh'}

    query = query_from_normalized(np)
    ctx = build_purchase_context(supplier, quantity=request.quantity or 1)
    client = ConnectorHttpClient()
    try:
        connector = get_connector(code, http_client=client, purchase_context=ctx)
        # Lightweight health
        health = connector.health_check()
        SupplierConnectorStatus.objects.create(
            supplier=supplier,
            status=health.status,
            response_time_ms=health.response_time_ms,
            http_status=health.http_status,
            checked_url=health.checked_url or '',
            error_type=health.error_type or '',
            error_message=health.error_message or '',
            parser_version=getattr(connector, 'parser_version', ''),
            metadata=health.metadata or {},
        )
        if health.status in (
            SupplierConnectorStatus.Status.FAILED,
            SupplierConnectorStatus.Status.CAPTCHA_DETECTED,
            SupplierConnectorStatus.Status.RATE_LIMITED,
        ):
            supplier.last_failed_check = timezone.now()
            supplier.save(update_fields=['last_failed_check', 'updated_at'])
            _fail_run(run, health.error_type or health.status, health.error_message or health.status)
            return {'status': 'failed', 'error_type': health.status}

        supplier.last_successful_check = timezone.now()
        supplier.save(update_fields=['last_successful_check', 'updated_at'])

        candidates = connector.search(query)
        for cand in candidates:
            SupplierSearchResult.objects.create(
                buying_request=request,
                supplier=supplier,
                title=cand.title[:300],
                url=cand.url,
                price_preview=cand.price_preview,
                currency=cand.currency or '',
                supplier_sku=cand.supplier_sku or '',
                manufacturer_code=cand.manufacturer_code or '',
                raw_data=cand.raw_data or {},
            )

        best = pick_best_candidates(query, candidates, limit=3)
        offers_created = 0
        if not best:
            SupplierSearchRun.objects.filter(pk=run.pk).update(
                status=SupplierSearchRun.Status.COMPLETED,
                finished_at=timezone.now(),
                candidates_found=len(candidates),
                offers_created=0,
                error_type='',
                error_message='no_matching_product',
            )
            return {'status': 'completed', 'candidates': len(candidates), 'offers': 0}

        for cand, verdict in best:
            try:
                data = connector.get_product_details(cand, query)
            except ConnectorError as exc:
                logger.info('Details failed for %s: %s', supplier.code, exc)
                continue
            except Exception as exc:
                logger.exception('Unexpected details error for %s', supplier.code)
                continue

            # Stale cache reuse note
            if not force:
                prior = (
                    SupplierOffer.objects.filter(
                        buying_request=request,
                        supplier=supplier,
                        superseded_by__isnull=True,
                    )
                    .order_by('-checked_at')
                    .first()
                )
                if prior and cache_age_state(prior.checked_at) == 'stale':
                    data.warnings = list(data.warnings) + [
                        f'Search cache was stale (last checked {prior.checked_at.isoformat()})'
                    ]

            search_result = (
                SupplierSearchResult.objects.filter(
                    buying_request=request, supplier=supplier, url=cand.url
                )
                .order_by('-id')
                .first()
            )
            offer = offer_data_to_supplier_offer(
                buying_request=request,
                supplier=supplier,
                data=data,
                search_result=search_result,
                match_status=verdict.match_status,
                match_score=verdict.match_score,
                match_details=verdict.details,
                requested_variant={
                    'size': query.size,
                    'grip_size': query.grip_size,
                    'color': query.color,
                    'court': query.court,
                    'gender': query.gender,
                },
            )
            # Onex unsupported / manual_review warnings
            if supplier.onex_applicability == OnexApplicability.UNSUPPORTED:
                offer.warnings = list(offer.warnings) + ['Onex route unsupported']
            elif supplier.onex_applicability == OnexApplicability.MANUAL_REVIEW:
                offer.warnings = list(offer.warnings) + ['Onex route requires manual review']

            # Supersede previous current auto offers for this supplier
            old_offers = list(
                SupplierOffer.objects.filter(
                    buying_request=request,
                    supplier=supplier,
                    superseded_by__isnull=True,
                    is_manual=False,
                )
            )
            offer.save()
            for old in old_offers:
                if old.pk != offer.pk:
                    SupplierOffer.objects.filter(pk=old.pk).update(superseded_by=offer)
                    for sc in old.cost_scenarios.filter(
                        status__in=('calculated', 'calculation_blocked')
                    ):
                        from buying.models import CostScenario
                        CostScenario.objects.filter(pk=sc.pk).update(
                            status=CostScenario.Status.SUPERSEDED,
                            superseded_by=None,
                            rank=None,
                        )

            if supplier_supports_onex(supplier):
                rebuild_scenarios_for_offer(offer)
            offers_created += 1
            break  # one primary offer per supplier for MVP

        SupplierSearchRun.objects.filter(pk=run.pk).update(
            status=SupplierSearchRun.Status.COMPLETED,
            finished_at=timezone.now(),
            candidates_found=len(candidates),
            offers_created=offers_created,
            error_type='',
            error_message='' if offers_created else 'no_verified_offer',
        )
        return {'status': 'completed', 'candidates': len(candidates), 'offers': offers_created}
    except ConnectorError as exc:
        _fail_run(run, exc.error_type, str(exc))
        return {'status': 'failed', 'error_type': exc.error_type}
    except Exception as exc:
        logger.exception('Supplier search failed for %s', supplier.code)
        _fail_run(run, type(exc).__name__, str(exc)[:500])
        return {'status': 'failed', 'error_type': type(exc).__name__}
    finally:
        try:
            client.close()
        except Exception:
            pass


def _fail_run(run: SupplierSearchRun, error_type: str, message: str) -> None:
    SupplierSearchRun.objects.filter(pk=run.pk).update(
        status=SupplierSearchRun.Status.FAILED,
        finished_at=timezone.now(),
        error_type=error_type or 'error',
        error_message=(message or '')[:2000],
    )


def finalize_search(buying_request_id: int) -> dict:
    request = BuyingRequest.objects.get(pk=buying_request_id)
    runs = list(request.search_runs.all())
    if not runs:
        return {'status': request.status}
    pending = [r for r in runs if r.status in (
        SupplierSearchRun.Status.PENDING, SupplierSearchRun.Status.RUNNING
    )]
    if pending:
        return {'status': 'searching', 'pending': len(pending)}

    failed = sum(1 for r in runs if r.status == SupplierSearchRun.Status.FAILED)
    completed = sum(1 for r in runs if r.status == SupplierSearchRun.Status.COMPLETED)
    if failed and completed:
        request.status = BuyingRequest.Status.PARTIALLY_COMPLETED
    elif failed and not completed:
        request.status = BuyingRequest.Status.FAILED
    else:
        request.status = BuyingRequest.Status.COMPLETED
    request.save(update_fields=['status', 'updated_at'])
    return {'status': request.status, 'completed': completed, 'failed': failed}


def progress_payload(buying_request: BuyingRequest) -> dict:
    runs = list(buying_request.search_runs.select_related('supplier'))
    return {
        'request_id': buying_request.pk,
        'status': buying_request.status,
        'total': len(runs),
        'completed': sum(1 for r in runs if r.status == SupplierSearchRun.Status.COMPLETED),
        'failed': sum(1 for r in runs if r.status == SupplierSearchRun.Status.FAILED),
        'running': sum(1 for r in runs if r.status == SupplierSearchRun.Status.RUNNING),
        'pending': sum(1 for r in runs if r.status == SupplierSearchRun.Status.PENDING),
        'runs': [
            {
                'supplier': r.supplier.code,
                'supplier_name': r.supplier.name,
                'status': r.status,
                'candidates_found': r.candidates_found,
                'offers_created': r.offers_created,
                'error_type': r.error_type,
                'error_message': r.error_message,
            }
            for r in runs
        ],
    }


def run_health_check(supplier: Supplier) -> SupplierConnectorStatus:
    code = supplier.connector_class or supplier.code
    client = ConnectorHttpClient()
    try:
        connector = get_connector(code, http_client=client)
        health = connector.health_check()
    except Exception as exc:
        health_status = SupplierConnectorStatus(
            supplier=supplier,
            status=SupplierConnectorStatus.Status.FAILED,
            error_type=type(exc).__name__,
            error_message=str(exc)[:500],
            checked_url=supplier.base_url,
        )
        health_status.save()
        supplier.last_failed_check = timezone.now()
        supplier.save(update_fields=['last_failed_check', 'updated_at'])
        return health_status
    finally:
        client.close()

    row = SupplierConnectorStatus.objects.create(
        supplier=supplier,
        status=health.status,
        response_time_ms=health.response_time_ms,
        http_status=health.http_status,
        checked_url=health.checked_url or supplier.base_url,
        error_type=health.error_type or '',
        error_message=health.error_message or '',
        parser_version=getattr(connector, 'parser_version', ''),
        metadata=health.metadata or {},
    )
    if health.status == 'available':
        supplier.last_successful_check = timezone.now()
        supplier.save(update_fields=['last_successful_check', 'updated_at'])
    else:
        supplier.last_failed_check = timezone.now()
        supplier.save(update_fields=['last_failed_check', 'updated_at'])
    return row
