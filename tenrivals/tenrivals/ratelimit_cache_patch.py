"""Keep sign-in working when the Redis cache rejects a rate-limit read or write.

django-ratelimit only catches socket.gaierror around cache.add. redis-py also
raises ConnectionError, ResponseError (for example when memory is full), and
the Django Redis backend can raise while unpickling a stored value. Any of
those used to become a 500 on POST /accounts/login/.
"""

import logging
import pickle

logger = logging.getLogger(__name__)


def is_cache_backend_error(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, OSError, pickle.UnpicklingError)):
        return True
    module = type(exc).__module__ or ''
    return module == 'redis' or module.startswith('redis.') or module.startswith('django_redis')


def install() -> None:
    from django_ratelimit import core as rl_core

    if getattr(rl_core, '_tenrivals_ratelimit_patch', False):
        return
    rl_core._tenrivals_ratelimit_patch = True

    _orig_get_usage = rl_core.get_usage

    def get_usage(*args, **kwargs):
        try:
            return _orig_get_usage(*args, **kwargs)
        except Exception as exc:
            from django.conf import settings

            if is_cache_backend_error(exc) and getattr(
                settings, 'RATELIMIT_FAIL_OPEN_ON_REDIS_DOWN', True
            ):
                logger.warning(
                    'Rate limit cache unavailable (%s); allowing the request',
                    type(exc).__name__,
                )
                return None
            raise

    rl_core.get_usage = get_usage
