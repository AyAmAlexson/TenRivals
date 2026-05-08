#!/usr/bin/env python3
"""Merge locale/data/shop_msgids.json + shop_ru.json + shop_ka.json into django.po (templates/shop/* only)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "locale" / "data"


def _ph(s: str) -> list[str]:
    return sorted(re.findall(r"%\([a-zA-Z0-9_]+\)s", s))


def _is_broken(en: str, tr: str) -> bool:
    if not tr.strip():
        return True
    if _ph(en) != _ph(tr):
        return True
    if "</a>" in en and tr.count("</a>") < en.count("</a>"):
        return True
    if en.count("<a ") > 0 and tr.count("<a ") < en.count("<a "):
        return True
    return False


def load_tables() -> tuple[dict[str, str], dict[str, str]]:
    ids: list[str] = json.loads((DATA / "shop_msgids.json").read_text(encoding="utf-8"))
    ru_list: list[str] = json.loads((DATA / "shop_ru.json").read_text(encoding="utf-8"))
    ka_list: list[str] = json.loads((DATA / "shop_ka.json").read_text(encoding="utf-8"))
    if not (len(ids) == len(ru_list) == len(ka_list)):
        raise SystemExit(f"len mismatch ids={len(ids)} ru={len(ru_list)} ka={len(ka_list)}")
    return dict(zip(ids, ru_list, strict=True)), dict(zip(ids, ka_list, strict=True))


def apply_po(po_path: Path, table: dict[str, str]) -> tuple[int, int]:
    po = polib.pofile(str(po_path))
    applied = 0
    skipped = 0
    for e in po:
        if e.obsolete:
            continue
        if not any("templates/shop/" in o[0] for o in (e.occurrences or [])):
            continue
        if e.msgid not in table:
            continue
        tr = table[e.msgid]
        if _is_broken(e.msgid, tr):
            skipped += 1
            continue
        e.msgstr = tr
        e.flags = [f for f in e.flags if f != "fuzzy"]
        applied += 1
    po.save()
    return applied, skipped


def main() -> None:
    ru, ka = load_tables()
    n_ru, s_ru = apply_po(ROOT / "locale/ru/LC_MESSAGES/django.po", ru)
    n_ka, s_ka = apply_po(ROOT / "locale/ka/LC_MESSAGES/django.po", ka)
    print(f"ru: applied={n_ru} skipped_broken={s_ru}")
    print(f"ka: applied={n_ka} skipped_broken={s_ka}")


if __name__ == "__main__":
    main()
