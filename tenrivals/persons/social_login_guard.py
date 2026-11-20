"""Stop unconfigured Google/Facebook login from raising a 500.

allauth looks up a SocialApp before it renders anything. With no app (or two
apps for one provider) that lookup raises, and the storefront shows
"Something went wrong" instead of the sign-in form.
"""

import logging

from django.core.exceptions import ImproperlyConfigured, MultipleObjectsReturned

logger = logging.getLogger(__name__)


def install() -> None:
    from allauth.socialaccount.helpers import render_authentication_error
    from allauth.socialaccount.models import SocialApp
    from allauth.socialaccount.providers.base.mixins import OAuthLoginMixin
    from allauth.socialaccount.providers.oauth2.views import OAuth2CallbackView

    if getattr(OAuthLoginMixin, '_tenrivals_social_guard', False):
        return
    OAuthLoginMixin._tenrivals_social_guard = True

    config_errors = (
        SocialApp.DoesNotExist,
        MultipleObjectsReturned,
        ImproperlyConfigured,
    )

    def _guard(original):
        def wrapped(self, request, *args, **kwargs):
            try:
                return original(self, request, *args, **kwargs)
            except config_errors as exc:
                provider_id = getattr(getattr(self, 'adapter', None), 'provider_id', '') or ''
                logger.warning(
                    "Social login unavailable for %s: %s",
                    provider_id or 'unknown',
                    type(exc).__name__,
                )
                return render_authentication_error(
                    request,
                    provider_id,
                    exception=exc,
                )

        return wrapped

    OAuthLoginMixin.dispatch = _guard(OAuthLoginMixin.dispatch)
    OAuth2CallbackView.dispatch = _guard(OAuth2CallbackView.dispatch)
