from django.contrib import admin

from .models import (
    BuyingRequest,
    CalculationRule,
    CanonicalProduct,
    ConnectorResponse,
    CostScenario,
    FulfillmentProvider,
    FulfillmentRoute,
    FulfillmentWarehouse,
    FxRate,
    ManualOverride,
    NormalizedProduct,
    OptimizationCandidate,
    OptimizationScenario,
    ProductMapping,
    RacquetSpecification,
    Supplier,
    SupplierConnectorStatus,
    SupplierOffer,
    SupplierSearchResult,
    SupplierSearchRun,
)


@admin.register(BuyingRequest)
class BuyingRequestAdmin(admin.ModelAdmin):
    list_display = ['id', 'original_query', 'status', 'staff_user', 'created_at']
    list_filter = ['status']
    search_fields = ['original_query']


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'country', 'currency', 'onex_applicability', 'enabled']
    list_filter = ['enabled', 'country', 'onex_applicability']


@admin.register(CalculationRule)
class CalculationRuleAdmin(admin.ModelAdmin):
    list_display = ['name', 'rule_type', 'version', 'enabled', 'priority', 'updated_at']
    list_filter = ['rule_type', 'enabled']


@admin.register(CostScenario)
class CostScenarioAdmin(admin.ModelAdmin):
    list_display = ['id', 'buying_request', 'supplier_offer', 'scenario_type', 'status', 'customer_price']
    list_filter = ['scenario_type', 'status', 'confidence']


@admin.register(FxRate)
class FxRateAdmin(admin.ModelAdmin):
    list_display = ['currency', 'rate_date', 'quantity', 'rate_gel', 'source', 'fetched_at']
    list_filter = ['source', 'currency']
    search_fields = ['currency']


@admin.register(RacquetSpecification)
class RacquetSpecificationAdmin(admin.ModelAdmin):
    list_display = [
        'brand', 'model_family', 'generation', 'variant',
        'head_size_sqin', 'weight_g_unstrung', 'string_pattern', 'enabled',
    ]
    list_filter = ['brand', 'enabled', 'generation']
    search_fields = ['brand', 'model_family', 'variant', 'manufacturer_code']


for model in (
    NormalizedProduct,
    CanonicalProduct,
    ConnectorResponse,
    SupplierConnectorStatus,
    SupplierSearchRun,
    SupplierSearchResult,
    SupplierOffer,
    ProductMapping,
    FulfillmentProvider,
    FulfillmentWarehouse,
    FulfillmentRoute,
    OptimizationScenario,
    OptimizationCandidate,
    ManualOverride,
):
    admin.site.register(model)
