"""Helpers for per-size stock quantities stored as JSON dicts on Product subclasses."""

from __future__ import annotations

from typing import Any


def eu_shoe_size_labels() -> list[str]:
    """Common EU tennis shoe sizes (half sizes)."""
    labels: list[str] = []
    for whole in range(35, 50):
        labels.append(f"EU {whole}")
        labels.append(f"EU {whole}.5")
    return labels


GRIP_SIZE_LABELS = [f"L{i}" for i in range(6)]

APPAREL_SIZE_LABELS = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "3XL"]


def normalize_sizes_to_qty_map(raw: Any, *, fallback_total: int = 0) -> dict[str, int]:
    """
    DB may store list (legacy) or dict.
    If list + fallback_total > 0, assign total to the first listed size (inventory hint).
    """
    if raw is None:
        raw = []
    if isinstance(raw, list):
        keys = [str(x).strip() for x in raw if str(x).strip()]
        out = {k: 0 for k in keys}
        if fallback_total > 0 and keys:
            out[keys[0]] = int(fallback_total)
        return out
    if isinstance(raw, dict):
        return {str(k).strip(): int(v or 0) for k, v in raw.items() if str(k).strip()}
    return {}


def labels_for_size_grid(allowed: list[str], qty_map: dict[str, int]) -> list[str]:
    """Union of standard labels and any keys already saved (custom / import)."""
    seen: dict[str, None] = {}
    for label in allowed:
        seen.setdefault(label, None)
    for k in sorted(qty_map.keys()):
        seen.setdefault(k, None)
    return list(seen.keys())


def sizes_for_pdp_display(raw: Any) -> list[str]:
    """Labels to show as in-stock variants on PDP."""
    if isinstance(raw, dict):
        return [k for k, v in sorted(raw.items()) if int(v or 0) > 0]
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []
