"""Supplier connector layer: PurchaseContext-aware shop integrations."""

from .base import (
    AuthenticationResult,
    AuthenticationStatus,
    ConnectorCapabilities,
    HealthCheckResult,
    OfferData,
    PromotionData,
    PurchaseContext,
    SearchCandidate,
    SupplierConnector,
)
from .registry import get_connector, list_connector_codes, register_connector

__all__ = [
    'AuthenticationResult',
    'AuthenticationStatus',
    'ConnectorCapabilities',
    'HealthCheckResult',
    'OfferData',
    'PromotionData',
    'PurchaseContext',
    'SearchCandidate',
    'SupplierConnector',
    'get_connector',
    'list_connector_codes',
    'register_connector',
]
