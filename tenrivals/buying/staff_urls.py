"""Buying staff URLs. Included into the administration namespace:
path('buying/', include('buying.staff_urls')) in persons/administration_urls.py."""

from django.urls import path

from . import staff_views

urlpatterns = [
    path('', staff_views.buying_requests, name='buying_requests'),
    path('new/', staff_views.buying_request_new, name='buying_request_new'),
    path('<int:pk>/', staff_views.buying_request_detail, name='buying_request_detail'),
    path(
        '<int:pk>/progress/',
        staff_views.buying_request_progress,
        name='buying_request_progress',
    ),
    path(
        '<int:request_pk>/offers/new/',
        staff_views.buying_offer_edit,
        name='buying_offer_new',
    ),
    path(
        '<int:request_pk>/offers/<int:pk>/',
        staff_views.buying_offer_edit,
        name='buying_offer_edit',
    ),
    path('scenarios/<int:pk>/', staff_views.buying_scenario_detail, name='buying_scenario_detail'),
    path('suppliers/', staff_views.buying_suppliers, name='buying_suppliers'),
    path('suppliers/new/', staff_views.buying_supplier_edit, name='buying_supplier_new'),
    path('suppliers/<int:pk>/', staff_views.buying_supplier_edit, name='buying_supplier_edit'),
    path('connectors/', staff_views.buying_connectors, name='buying_connectors'),
    path('mappings/', staff_views.buying_mappings, name='buying_mappings'),
    path('routes/', staff_views.buying_routes, name='buying_routes'),
    path('routes/new/', staff_views.buying_route_edit, name='buying_route_new'),
    path('routes/<int:pk>/', staff_views.buying_route_edit, name='buying_route_edit'),
    path('providers/new/', staff_views.buying_provider_edit, name='buying_provider_new'),
    path('providers/<int:pk>/', staff_views.buying_provider_edit, name='buying_provider_edit'),
    path('warehouses/new/', staff_views.buying_warehouse_edit, name='buying_warehouse_new'),
    path('warehouses/<int:pk>/', staff_views.buying_warehouse_edit, name='buying_warehouse_edit'),
    path('pricing-rules/', staff_views.buying_pricing_rules, name='buying_pricing_rules'),
    path('pricing-rules/new/', staff_views.buying_rule_edit, name='buying_rule_new'),
    path('pricing-rules/<int:pk>/', staff_views.buying_rule_edit, name='buying_rule_edit'),
    path('fx-rates/', staff_views.buying_fx_rates, name='buying_fx_rates'),
    path(
        'optimization-rules/',
        staff_views.buying_optimization_rules,
        name='buying_optimization_rules',
    ),
    path(
        'optimization-rules/new/',
        staff_views.buying_candidate_edit,
        name='buying_candidate_new',
    ),
    path(
        'optimization-rules/<int:pk>/',
        staff_views.buying_candidate_edit,
        name='buying_candidate_edit',
    ),
]
