"""Access control for the Buying staff section.

Uses the standard Django permissions framework (permissions declared on
buying.models.BuyingPermissions). Superusers pass every check via has_perm().
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def buying_permission_required(perm: str):
    """Decorator: require login + a buying.* permission (e.g. 'buying.run_search')."""

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.has_perm(perm):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return login_required(_wrapped)

    return decorator
