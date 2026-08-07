"""Orchestrate multi-supplier search for a BuyingRequest."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from buying.connectors.base import PurchaseContext
from buying.connectors.exceptions import ConnectorError, CredentialsMissing
from buying.connectors.http import ConnectorHttpClient
from buying.connectors.registry import get_connector, get_connector_class
from buying.connectors.readiness import readiness_for
from buying.engine.routes import supplier_supports_onex
from buying.models import (
    BuyingRequest,
    OnexApplicability,
    OfferEligibility,
    Supplier,
    SupplierConnectorStatus,
    SupplierOffer,
    SupplierSearchResult,
    SupplierSearchRun,
)
from buying.services.matching import MATCHING_RULES_VERSION, pick_best_candidates, query_from_normalized, score_candidate
from buying.services.offer_persist import offer_data_to_supplier_offer
from buying.services.offers import rebuild_scenarios_for_offer
from buying.services.product_identity import (
    GRIP_STATUS_UNAVAILABLE,
    apply_grip_to_offer,
    cache_logic_compatible,
    logic_versions,
    rescore_with_product_page,
)

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


def _reject_current_auto_offers(request, supplier, *, reason: str) -> int:
    """Mark live auto offers as rejected / no_match and supersede their scenarios.

    Used when Force Refresh or re-matching finds that prior Mini/wrong-family
    offers must disappear from valid Supplier Offers.
    """
    from buying.models import CostScenario

    offers = list(
        SupplierOffer.objects.filter(
            buying_request=request,
            supplier=supplier,
            superseded_by__isnull=True,
            is_manual=False,
        )
    )
    for offer in offers:
        warnings = list(offer.warnings or [])
        if reason not in warnings:
            warnings.append(reason)
        offer.match_status = 'no_match'
        offer.eligibility_status = OfferEligibility.REJECTED
        offer.product_identity_confirmed = 'unavailable'
        offer.warnings = warnings
        offer.save(update_fields=[
            'match_status', 'eligibility_status', 'product_identity_confirmed',
            'warnings', 'updated_at',
        ])
        CostScenario.objects.filter(
            supplier_offer=offer,
            status__in=('calculated', 'calculation_blocked'),
        ).update(status=CostScenario.Status.SUPERSEDED, rank=None)
    return len(offers)


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

    # Search cache: reuse only when TTL-fresh AND logic versions still match,
    # and the cached offer still passes current matching + eligibility rules.
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
            code_probe = supplier.connector_class or supplier.code
            cls = get_connector_class(code_probe)
            parser_v = getattr(cls, 'parser_version', '') if cls else ''
            prompt_v = getattr(np, 'prompt_version', '') or ''
            stored_versions = (existing.match_details or {}).get('logic_versions') or {}
            query_probe = query_from_normalized(np)
            from buying.connectors.base import SearchCandidate
            cached_cand = SearchCandidate(
                title=existing.title,
                url=existing.product_url or 'https://cache.local/',
                raw_data={
                    'page_text': (existing.match_details or {}).get('page_text') or existing.title,
                },
            )
            recheck = score_candidate(query_probe, cached_cand)
            still_ok = (
                cache_logic_compatible(
                    stored_versions, parser_version=parser_v, prompt_version=prompt_v
                )
                and recheck.match_status != 'no_match'
                and existing.eligibility_status != OfferEligibility.REJECTED
                and existing.match_status != 'no_match'
            )
            if still_ok:
                SupplierSearchRun.objects.filter(pk=run.pk).update(
                    status=SupplierSearchRun.Status.COMPLETED,
                    finished_at=timezone.now(),
                    candidates_found=0,
                    offers_created=0,
                    error_message='cache_hit_fresh',
                )
                return {'status': 'completed', 'cache': 'fresh'}
            # Stale logic / now-rejected product: invalidate cached offer and re-search
            logger.info(
                'Cache invalidated for %s on request #%s (match=%s versions_ok=%s)',
                supplier.code, request.pk, recheck.match_status,
                cache_logic_compatible(stored_versions, parser_version=parser_v, prompt_version=prompt_v),
            )
            if recheck.match_status == 'no_match' or existing.match_status == 'no_match':
                _reject_current_auto_offers(
                    request,
                    supplier,
                    reason=(
                        f'Rejected by matching {MATCHING_RULES_VERSION}: '
                        + ', '.join(recheck.details.get('hard_mismatches') or ['identity'])
                    ),
                )

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
            readiness = readiness_for(supplier.code)
            msg = 'no_matching_product'
            if readiness.get('tier') in ('anti_bot_blocked', 'authenticated_blocked'):
                msg = readiness.get('notes') or readiness.get('tier')
            elif readiness.get('search_ok') == 'credentials_required':
                msg = 'credentials_missing'
            elif candidates and not best:
                msg = 'candidates_rejected_by_matching'
            elif not candidates:
                msg = 'search_empty'
            # Drop prior Mini / wrong-family offers from the live list
            _reject_current_auto_offers(
                request,
                supplier,
                reason=f'No valid match after {MATCHING_RULES_VERSION} ({msg})',
            )
            SupplierSearchRun.objects.filter(pk=run.pk).update(
                status=SupplierSearchRun.Status.COMPLETED,
                finished_at=timezone.now(),
                candidates_found=len(candidates),
                offers_created=0,
                error_type='',
                error_message=msg,
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

            # Re-score using product-page text — title-only Mini Pure Drive etc. die here
            identity = rescore_with_product_page(query, cand, data)
            if identity.match_status == 'no_match':
                logger.info(
                    'Rejected %s candidate after page identity: %s (%s)',
                    supplier.code, data.title, identity.details.get('hard_mismatches'),
                )
                continue

            grip = apply_grip_to_offer(data, query)
            verdict_status = identity.match_status
            verdict_score = identity.match_score
            match_details = dict(identity.details)
            match_details['grip_verification'] = {
                'status': grip.status,
                'available_grips': grip.available_grips,
                'note': grip.note,
            }
            match_details['page_text'] = (identity.page_text or '')[:4000]
            match_details['logic_versions'] = logic_versions(
                parser_version=getattr(connector, 'parser_version', '') or data.parser_version,
                prompt_version=getattr(np, 'prompt_version', '') or '',
            )
            match_details['matching_rules_version'] = MATCHING_RULES_VERSION

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
                match_status=verdict_status,
                match_score=verdict_score,
                match_details=match_details,
                requested_variant={
                    'size': query.size,
                    'grip_size': query.grip_size,
                    'color': query.color,
                    'court': query.court,
                    'gender': query.gender,
                },
            )
            if grip.status == GRIP_STATUS_UNAVAILABLE:
                offer.eligibility_status = OfferEligibility.UNAVAILABLE
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
            # Never persist rejected identity as a live offer
            from buying.services.eligibility import apply_offer_eligibility
            apply_offer_eligibility(offer)
            if offer.eligibility_status == OfferEligibility.REJECTED or offer.match_status == 'no_match':
                logger.info('Skipping rejected offer for %s: %s', supplier.code, offer.title)
                continue

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

            if supplier_supports_onex(supplier) and offer.eligibility_status != OfferEligibility.REJECTED:
                rebuild_scenarios_for_offer(offer)
            offers_created += 1
            break  # one primary offer per supplier for MVP

        if offers_created == 0:
            _reject_current_auto_offers(
                request,
                supplier,
                reason=f'No offer survived page identity / grip checks ({MATCHING_RULES_VERSION})',
            )

        SupplierSearchRun.objects.filter(pk=run.pk).update(
            status=SupplierSearchRun.Status.COMPLETED,
            finished_at=timezone.now(),
            candidates_found=len(candidates),
            offers_created=offers_created,
            error_type='',
            error_message='' if offers_created else 'no_verified_offer',
        )
        return {'status': 'completed', 'candidates': len(candidates), 'offers': offers_created}
    except CredentialsMissing as exc:
        SupplierSearchRun.objects.filter(pk=run.pk).update(
            status=SupplierSearchRun.Status.COMPLETED,
            finished_at=timezone.now(),
            candidates_found=0,
            offers_created=0,
            error_type='credentials_missing',
            error_message=str(exc)[:2000],
        )
        return {'status': 'completed', 'error_type': 'credentials_missing'}
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
                'readiness': readiness_for(r.supplier.code),
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
