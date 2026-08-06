"""Resolution of versioned CalculationRules by scope.

Among enabled rules of the requested type that are active at the given moment
and whose scope fields are either empty or equal to the context, the most
specific one wins; ties break by priority, then by newest version, then by
newest row (pk) — the order is always deterministic, never database order.

Genuinely conflicting rules (same type, identical scope, same priority,
overlapping active window) are rejected at save time by
find_conflicting_rules() / CalculationRuleForm; if legacy data still contains
such a pair, resolve_rule_checked() reports the conflict so the engine can
attach a warning to the scenario.
"""

from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from buying.models import CalculationRule


def _candidates(
    rule_type: str,
    *,
    country: str = '',
    supplier=None,
    provider=None,
    warehouse=None,
    category: str = '',
    at=None,
) -> list[CalculationRule]:
    at = at or timezone.now()
    qs = (
        CalculationRule.objects.filter(rule_type=rule_type, enabled=True)
        .filter(Q(active_from__isnull=True) | Q(active_from__lte=at))
        .filter(Q(active_to__isnull=True) | Q(active_to__gte=at))
        .filter(Q(country='') | Q(country=country))
        .filter(Q(supplier__isnull=True) | Q(supplier=supplier))
        .filter(Q(provider__isnull=True) | Q(provider=provider))
        .filter(Q(warehouse__isnull=True) | Q(warehouse=warehouse))
        .filter(Q(category='') | Q(category=category))
    )
    rules = list(qs)
    rules.sort(
        key=lambda r: (r.scope_specificity(), r.priority, r.version, r.pk),
        reverse=True,
    )
    return rules


def resolve_rule(rule_type: str, **context) -> CalculationRule | None:
    rules = _candidates(rule_type, **context)
    return rules[0] if rules else None


def resolve_rule_checked(rule_type: str, **context) -> tuple[CalculationRule | None, bool]:
    """Like resolve_rule, but also report whether the winner had a genuine
    conflict (another rule with equal specificity and priority but a different
    scope combination, i.e. the tie-breaker had to decide)."""
    rules = _candidates(rule_type, **context)
    if not rules:
        return None, False
    winner = rules[0]
    conflict = any(
        r.scope_specificity() == winner.scope_specificity()
        and r.priority == winner.priority
        and r.pk != winner.pk
        for r in rules[1:2]
    )
    return winner, conflict


def find_conflicting_rules(rule: CalculationRule):
    """Enabled rules of the same type with the exact same scope and priority
    whose active windows overlap the given rule's window. Used by form
    validation to refuse saving ambiguous rules."""
    qs = CalculationRule.objects.filter(
        rule_type=rule.rule_type,
        enabled=True,
        country=rule.country,
        supplier=rule.supplier,
        provider=rule.provider,
        warehouse=rule.warehouse,
        category=rule.category,
        priority=rule.priority,
    )
    if rule.pk:
        qs = qs.exclude(pk=rule.pk)
    if rule.active_from:
        qs = qs.filter(Q(active_to__isnull=True) | Q(active_to__gte=rule.active_from))
    if rule.active_to:
        qs = qs.filter(Q(active_from__isnull=True) | Q(active_from__lte=rule.active_to))
    return qs
