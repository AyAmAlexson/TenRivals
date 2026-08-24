"""Merge two shop.Customer rows: reassign related records and resolve field collisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from .customer_sync import sync_customer_to_user
from .models import Customer, OrderForMe, SalesOrder


# (field_name, human label) — profile fields staff resolve on collision.
MERGEABLE_FIELDS: tuple[tuple[str, str], ...] = (
    ('first_name', 'First name'),
    ('last_name', 'Last name'),
    ('name_local', 'First name (local)'),
    ('surname_local', 'Last name (local)'),
    ('phone', 'Phone'),
    ('email', 'Email'),
    ('tg_account', 'Telegram'),
    ('address', 'Address'),
    ('source', 'Source'),
    ('comment', 'Comment / notes'),
    ('newsletter_opt_in', 'Newsletter opt-in'),
)

MERGE_FIELD_NAMES = frozenset(name for name, _ in MERGEABLE_FIELDS)
MERGE_FIELD_LABELS = {name: label for name, label in MERGEABLE_FIELDS}


@dataclass(frozen=True)
class FieldConflict:
    field: str
    label: str
    survivor_value: Any
    donor_value: Any
    survivor_display: str
    donor_display: str


@dataclass(frozen=True)
class FieldResolution:
    """How to set one field on the survivor."""

    choice: str  # 'survivor' | 'donor' | 'custom' | 'none' (user only)
    custom_value: str = ''


def _strip_str(value: Any) -> str:
    if value is None:
        return ''
    return str(value).strip()


def normalize_field_value(field: str, value: Any) -> Any:
    if field == 'newsletter_opt_in':
        return bool(value)
    if field == 'email':
        return _strip_str(value).lower()
    if field == 'user':
        return value  # pk or None
    return _strip_str(value)


def is_empty_value(field: str, value: Any) -> bool:
    if field == 'newsletter_opt_in':
        return False  # bool always "filled"
    if field == 'user':
        return value is None
    return normalize_field_value(field, value) == ''


def format_field_display(field: str, value: Any) -> str:
    if field == 'newsletter_opt_in':
        return 'Yes' if value else 'No'
    if field == 'user':
        if value is None:
            return '(none)'
        user = value
        email = getattr(user, 'email', '') or ''
        return f'#{getattr(user, "pk", "?")} {email}'.strip()
    text = _strip_str(value)
    return text if text else '(empty)'


def get_raw_field(customer: Customer, field: str) -> Any:
    if field == 'user':
        return customer.user
    return getattr(customer, field)


def find_field_conflicts(survivor: Customer, donor: Customer) -> list[FieldConflict]:
    conflicts: list[FieldConflict] = []
    for field, label in MERGEABLE_FIELDS:
        s_raw = get_raw_field(survivor, field)
        d_raw = get_raw_field(donor, field)
        if is_empty_value(field, s_raw) or is_empty_value(field, d_raw):
            continue
        if normalize_field_value(field, s_raw) == normalize_field_value(field, d_raw):
            continue
        conflicts.append(
            FieldConflict(
                field=field,
                label=label,
                survivor_value=s_raw,
                donor_value=d_raw,
                survivor_display=format_field_display(field, s_raw),
                donor_display=format_field_display(field, d_raw),
            )
        )

    # Site account: only conflict when both linked to different users.
    s_user = survivor.user
    d_user = donor.user
    if s_user is not None and d_user is not None and s_user.pk != d_user.pk:
        conflicts.append(
            FieldConflict(
                field='user',
                label='Site account',
                survivor_value=s_user,
                donor_value=d_user,
                survivor_display=format_field_display('user', s_user),
                donor_display=format_field_display('user', d_user),
            )
        )
    return conflicts


def auto_fill_values(survivor: Customer, donor: Customer) -> dict[str, Any]:
    """Non-conflicting fills: empty survivor takes donor value."""
    fills: dict[str, Any] = {}
    for field, _label in MERGEABLE_FIELDS:
        s_raw = get_raw_field(survivor, field)
        d_raw = get_raw_field(donor, field)
        if field == 'newsletter_opt_in':
            # Prefer True if either opted in when no conflict UI (same or only one "matter").
            # Handled only via conflict when they differ; otherwise keep survivor.
            continue
        if is_empty_value(field, s_raw) and not is_empty_value(field, d_raw):
            fills[field] = d_raw
    return fills


def _append_merge_notes(existing: str, lines: list[str]) -> str:
    block = '\n'.join(lines)
    existing = (existing or '').rstrip()
    if not existing:
        return block
    return f'{existing}\n\n{block}'


def _coerced_field_value(field: str, value: Any) -> Any:
    if field == 'newsletter_opt_in':
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(value)
    if field == 'user':
        return value
    return _strip_str(value)


def resolve_field_value(
    field: str,
    survivor: Customer,
    donor: Customer,
    resolution: FieldResolution | None,
    *,
    in_conflict: bool,
) -> tuple[Any, list[str]]:
    """
    Return (value_to_set, merge_alternative_note_lines).
    Note lines use: Merge alternative <field> <value>
    """
    s_raw = get_raw_field(survivor, field)
    d_raw = get_raw_field(donor, field)
    notes: list[str] = []

    if field == 'user':
        if not in_conflict:
            if survivor.user_id:
                return survivor.user, notes
            if donor.user_id:
                return donor.user, notes
            return None, notes
        choice = (resolution.choice if resolution else 'survivor').strip().lower()
        if choice == 'donor':
            notes.append(
                f'Merge alternative user {format_field_display("user", s_raw)}'
            )
            return d_raw, notes
        if choice == 'none':
            notes.append(
                f'Merge alternative user {format_field_display("user", s_raw)}'
            )
            notes.append(
                f'Merge alternative user {format_field_display("user", d_raw)}'
            )
            return None, notes
        # survivor (default)
        notes.append(
            f'Merge alternative user {format_field_display("user", d_raw)}'
        )
        return s_raw, notes

    if not in_conflict:
        if field == 'newsletter_opt_in':
            return bool(s_raw), notes
        if is_empty_value(field, s_raw) and not is_empty_value(field, d_raw):
            return d_raw, notes
        return s_raw, notes

    choice = (resolution.choice if resolution else 'survivor').strip().lower()
    if choice == 'custom':
        custom = _coerced_field_value(field, resolution.custom_value if resolution else '')
        if field == 'first_name' and not _strip_str(custom):
            custom = _strip_str(s_raw) or _strip_str(d_raw) or 'Customer'
        notes.append(
            f'Merge alternative {field} {format_field_display(field, s_raw)}'
        )
        notes.append(
            f'Merge alternative {field} {format_field_display(field, d_raw)}'
        )
        return custom, notes
    if choice == 'donor':
        notes.append(
            f'Merge alternative {field} {format_field_display(field, s_raw)}'
        )
        return d_raw, notes
    # survivor
    notes.append(
        f'Merge alternative {field} {format_field_display(field, d_raw)}'
    )
    return s_raw, notes


def count_related(customer: Customer) -> dict[str, int]:
    return {
        'sales_orders': customer.sales_orders.count(),
        'order_for_me': customer.order_for_me_orders.count(),
        'buying_requests': customer.buying_requests.count(),
    }


@transaction.atomic
def merge_customers(
    survivor: Customer,
    donor: Customer,
    resolutions: dict[str, FieldResolution],
) -> Customer:
    """
    Apply field resolutions, reassign related FKs from donor → survivor, delete donor.
    Unchosen / both conflicting values are appended to survivor.comment as
    ``Merge alternative <field> <value>`` lines.
    """
    if survivor.pk == donor.pk:
        raise ValueError('Survivor and donor must be different customers.')

    survivor = Customer.objects.select_related('user').select_for_update().get(pk=survivor.pk)
    donor = Customer.objects.select_related('user').select_for_update().get(pk=donor.pk)

    conflicts = {c.field: c for c in find_field_conflicts(survivor, donor)}
    note_lines: list[str] = [
        f'Merged from customer #{donor.pk} ({donor.display_name()}) '
        f'on {timezone.localdate().isoformat()}',
    ]
    update_fields: list[str] = []

    # Resolve profile fields
    final_values: dict[str, Any] = {}
    for field, _label in MERGEABLE_FIELDS:
        value, alt_notes = resolve_field_value(
            field,
            survivor,
            donor,
            resolutions.get(field),
            in_conflict=field in conflicts,
        )
        final_values[field] = value
        note_lines.extend(alt_notes)

    # Resolve user separately (may be in conflicts)
    user_value, user_notes = resolve_field_value(
        'user',
        survivor,
        donor,
        resolutions.get('user'),
        in_conflict='user' in conflicts,
    )
    note_lines.extend(user_notes)

    # Apply scalar fields (comment last with notes appended)
    for field, _label in MERGEABLE_FIELDS:
        if field == 'comment':
            continue
        new_val = final_values[field]
        if field == 'newsletter_opt_in':
            if bool(getattr(survivor, field)) != bool(new_val):
                setattr(survivor, field, bool(new_val))
                update_fields.append(field)
        else:
            coerced = _coerced_field_value(field, new_val)
            if _strip_str(getattr(survivor, field)) != _strip_str(coerced):
                setattr(survivor, field, coerced)
                update_fields.append(field)

    comment_base = _coerced_field_value('comment', final_values['comment'])
    survivor.comment = _append_merge_notes(comment_base, note_lines)
    update_fields.append('comment')

    # Re-link site user carefully (OneToOne)
    target_user = user_value
    target_user_id = getattr(target_user, 'pk', None) if target_user is not None else None

    # Detach donor's user first if we are moving it or discarding both links.
    if donor.user_id and donor.user_id != target_user_id:
        donor.user = None
        donor.save(update_fields=['user', 'updated_at'])

    if survivor.user_id != target_user_id:
        if survivor.user_id and survivor.user_id != target_user_id:
            survivor.user = None
            survivor.save(update_fields=['user', 'updated_at'])
        if target_user_id:
            # Ensure no other customer still holds this user.
            Customer.objects.filter(user_id=target_user_id).exclude(pk=survivor.pk).update(
                user=None
            )
            survivor.user_id = target_user_id
            update_fields.append('user')

    if update_fields:
        if 'updated_at' not in update_fields:
            update_fields.append('updated_at')
        survivor.save(update_fields=list(dict.fromkeys(update_fields)))

    # Reassign related records
    SalesOrder.objects.filter(customer_id=donor.pk).update(customer_id=survivor.pk)
    OrderForMe.objects.filter(customer_id=donor.pk).update(customer_id=survivor.pk)
    from buying.models import BuyingRequest

    BuyingRequest.objects.filter(client_id=donor.pk).update(client_id=survivor.pk)

    # Clear donor user link (if still set to same target — already on survivor) then delete.
    if donor.user_id:
        donor.user = None
        donor.save(update_fields=['user', 'updated_at'])
    donor.delete()

    survivor.refresh_from_db()
    sync_customer_to_user(survivor)
    return survivor


def parse_resolutions_from_post(post, conflict_fields: list[str]) -> dict[str, FieldResolution]:
    out: dict[str, FieldResolution] = {}
    for field in conflict_fields:
        choice = (post.get(f'choice_{field}') or 'survivor').strip().lower()
        if choice not in ('survivor', 'donor', 'custom', 'none'):
            choice = 'survivor'
        custom = (post.get(f'custom_{field}') or '').strip()
        out[field] = FieldResolution(choice=choice, custom_value=custom)
    return out
