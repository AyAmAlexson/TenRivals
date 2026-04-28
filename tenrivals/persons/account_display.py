"""Shared display helpers for account UI (shop header, sidebar)."""


def normalize_telegram_username(value: str | None) -> str:
    s = (value or '').strip()
    while s.startswith('@'):
        s = s[1:].strip()
    return s


def account_initials_for_user(user) -> str:
    fn = (getattr(user, 'first_name', None) or '').strip()
    ln = (getattr(user, 'last_name', None) or '').strip()
    if fn or ln:
        parts = []
        if fn:
            parts.append(fn[0])
        if ln:
            parts.append(ln[0])
        out = ''.join(parts)
        return (out[:2] or '?').upper()

    email = (getattr(user, 'email', None) or '').strip().lower()
    if not email or '@' not in email:
        return '?'
    local, _, _domain = email.partition('@')
    local = local.strip()
    if not local:
        return '?'
    if '.' in local:
        segments = [s for s in local.split('.') if s]
        if len(segments) >= 2 and segments[0] and segments[1]:
            return (segments[0][0] + segments[1][0]).upper()
    return local[0].upper()
