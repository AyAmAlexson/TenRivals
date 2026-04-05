from django.shortcuts import redirect
from django.urls import reverse, NoReverseMatch
from rivals.models import Player
import logging

logger = logging.getLogger(__name__)

# Under /league/, new players (Player.is_new) may only use the onboarding wizard
# until it is completed. Real path prefix is /league/player-wizard/... (rivals.urls).
WIZARD_ACCESSIBLE_LEAGUE_PREFIXES = frozenset([
    '/league/player-wizard/',
])

ALWAYS_ACCESSIBLE_PATHS_START = [
    '/admin/',
    '/static/',
    '/media/',
    '/__debug__/',
]


class PlayerWizardMiddleware:
    """Enforce league onboarding wizard for *league* routes only.

    Shop (/shop/), account (/persons/), and allauth (/accounts/) are not part of
    Rivals onboarding; they are never blocked or redirected by this middleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        try:
            self.wizard_start_url = reverse('rivals:player_wizard_step1')
        except NoReverseMatch:
            logger.error(
                "PlayerWizardMiddleware: URL 'rivals:player_wizard_step1' not found."
            )
            self.wizard_start_url = '/league/player-wizard/step1/'

    def __call__(self, request):
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return self.get_response(request)

        current_path = request.path

        for path_start in ALWAYS_ACCESSIBLE_PATHS_START:
            if current_path.startswith(path_start):
                return self.get_response(request)

        if not current_path.startswith('/league/'):
            return self.get_response(request)

        try:
            player_data = Player.objects.filter(user=request.user).values('is_new').first()
            is_new = player_data['is_new'] if player_data else True
        except Exception as e:
            logger.error(
                "PlayerWizardMiddleware: Error fetching player for user %s: %s",
                request.user.email,
                e,
            )
            return self.get_response(request)

        if not is_new:
            return self.get_response(request)

        for allowed_start in WIZARD_ACCESSIBLE_LEAGUE_PREFIXES:
            if current_path.startswith(allowed_start):
                return self.get_response(request)

        logger.info(
            "Redirecting new player %s from %s to league onboarding",
            request.user.email,
            current_path,
        )
        return redirect(self.wizard_start_url)
