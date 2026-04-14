"""Helpers for per-size stock quantities stored as JSON dicts on Product subclasses."""

from __future__ import annotations

from typing import Any


def us_shoe_size_labels() -> list[str]:
    """US sizes from 3.5 through 16.0 in half-size steps (typical tennis / running retail range)."""
    labels: list[str] = []
    for tick in range(7, 33):  # 3.5 .. 16.0
        v = tick / 2
        if v == int(v):
            labels.append(f"US {int(v)}")
        else:
            labels.append(f"US {v}")
    return labels


GRIP_SIZE_LABELS = [f"L{i}" for i in range(6)]

APPAREL_SIZE_LABELS = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "3XL"]

# Common tennis string diameters (mm); staff grid can add custom keys via saved JSON.
STRING_GAUGE_MM_LABELS = [
    "1.10 mm",
    "1.12 mm",
    "1.15 mm",
    "1.18 mm",
    "1.20 mm",
    "1.22 mm",
    "1.23 mm",
    "1.24 mm",
    "1.25 mm",
    "1.26 mm",
    "1.27 mm",
    "1.28 mm",
    "1.29 mm",
    "1.30 mm",
    "1.31 mm",
    "1.32 mm",
    "1.33 mm",
    "1.35 mm",
]


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
