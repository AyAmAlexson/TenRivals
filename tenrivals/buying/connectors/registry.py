"""Connector registry: Supplier.code → SupplierConnector class."""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from buying.connectors.base import PurchaseContext, SupplierConnector

logger = logging.getLogger('buying')

_REGISTRY: dict[str, type] = {}
_IMPORTED = False


def register_connector(cls: type) -> type:
    code = getattr(cls, 'code', '') or ''
    if not code:
        raise ValueError(f'Connector {cls} missing code')
    _REGISTRY[code] = cls
    return cls


def list_connector_codes() -> list[str]:
    _ensure_imported()
    return sorted(_REGISTRY.keys())


def get_connector_class(code: str) -> type | None:
    _ensure_imported()
    return _REGISTRY.get(code)


def get_connector(
    code: str,
    *,
    http_client=None,
    purchase_context: PurchaseContext | None = None,
) -> SupplierConnector:
    cls = get_connector_class(code)
    if cls is None:
        raise KeyError(f'No connector registered for code={code!r}')
    return cls(http_client=http_client, purchase_context=purchase_context)


_MODULES = (
    'buying.connectors.stores.tennis_warehouse_eu',
    'buying.connectors.stores.tennis_warehouse_us',
    'buying.connectors.stores.midwest_racquet_sports',
    'buying.connectors.stores.tennis_express',
    'buying.connectors.stores.mister_tennis',
    'buying.connectors.stores.direct_tennis',
    'buying.connectors.stores.tennis_point_de',
    'buying.connectors.stores.tennis_point_com',
    'buying.connectors.stores.itf_tennis_point',
    'buying.connectors.stores.ole_tennis',
    'buying.connectors.stores.holabird_sports',
    'buying.connectors.stores.saburi_sports',
    'buying.connectors.stores.smashinn',
    'buying.connectors.stores.tennispro_eu',
    'buying.connectors.stores.passa_sports',
    'buying.connectors.stores.extreme_tennis',
    'buying.connectors.stores.m1_tennis',
    'buying.connectors.stores.central_tennis',
    'buying.connectors.stores.tennis_nuts',
    'buying.connectors.stores.prodirect_sport',
)


def _ensure_imported() -> None:
    global _IMPORTED
    if _IMPORTED:
        return
    for module in _MODULES:
        try:
            importlib.import_module(module)
        except Exception:
            logger.exception('Failed to import connector module %s', module)
    _IMPORTED = True
