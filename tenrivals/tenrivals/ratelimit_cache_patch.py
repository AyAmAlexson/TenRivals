"""django-ratelimit only catches socket.gaierror on cache.add; redis-py raises ConnectionError."""

import socket


def install() -> None:
    from django_ratelimit import core as rl_core

    if getattr(rl_core, '_tenrivals_ratelimit_patch', False):
        return
    rl_core._tenrivals_ratelimit_patch = True

    _orig_get_usage = rl_core.get_usage

    def get_usage(*args, **kwargs):
        try:
            return _orig_get_usage(*args, **kwargs)
        except (
            ConnectionError,
            TimeoutError,
            OSError,
            socket.gaierror,
        ):
            from django.conf import settings

            if getattr(settings, 'RATELIMIT_FAIL_OPEN_ON_REDIS_DOWN', True):
                return None
            raise

    rl_core.get_usage = get_usage
