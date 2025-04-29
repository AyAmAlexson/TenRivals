from django.shortcuts import redirect
from django.urls import reverse, NoReverseMatch
from rivals.models import Player # Убедитесь, что импорт корректен
import logging

logger = logging.getLogger(__name__)

# Определяем НАЧАЛА ПУТЕЙ, которые ДОСТУПНЫ для новых пользователей
# Стараемся получить их через reverse, где это возможно, чтобы избежать хардкодинга
WIZARD_ACCESSIBLE_PATH_STARTS = set([
    '/player-wizard/', # Разрешает все шаги визарда
    # Добавляем другие критичные пути
    '/accounts/logout/',
    '/accounts/password/change/',
    '/accounts/password/reset/',
    # Добавьте другие НАЧАЛА путей, если нужно (например, для telegram)
    '/persons/telegram/', # Разрешит /persons/telegram/callback/, /persons/telegram/change/ и т.д.
    '/persons/verify-telegram/',
    '/persons/resend-verification-code/',
])
# Можно добавить и полные пути для большей точности, если нужно
# try:
#     WIZARD_ACCESSIBLE_PATH_STARTS.add(reverse('account_logout'))
#     # ... добавить другие с reverse ...
# except NoReverseMatch:
#     logger.error("Could not reverse a required path start in PlayerWizardMiddleware")


# Пути, которые должны быть доступны всегда (например, админка, статика)
ALWAYS_ACCESSIBLE_PATHS_START = [
    '/admin/',
    '/static/',
    '/media/',
    '/__debug__/', # Для Django Debug Toolbar
    # Добавьте другие пути, если нужно
]

class PlayerWizardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        try:
            # URL, на который перенаправляем новых пользователей
            self.wizard_start_url = reverse('rivals:player_wizard_step1')
        except NoReverseMatch:
            logger.error("PlayerWizardMiddleware: URL 'rivals:player_wizard_step1' not found. Middleware might not work correctly.")
            # Используем статический путь как запасной вариант
            self.wizard_start_url = '/player-wizard/step1/'

    def __call__(self, request):
        # 1. Пропускаем неаутентифицированных пользователей
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return self.get_response(request)

        current_path = request.path

        # 2. Пропускаем всегда доступные ПУТИ (админка, статика и т.д.)
        for path_start in ALWAYS_ACCESSIBLE_PATHS_START:
            if current_path.startswith(path_start):
                return self.get_response(request)

        # 3. Получаем статус игрока (is_new)
        try:
            player_data = Player.objects.filter(user=request.user).values('is_new').first()
            is_new = player_data['is_new'] if player_data else True
        except Exception as e:
            logger.error(f"PlayerWizardMiddleware: Error fetching player for user {request.user.email}: {e}")
            return self.get_response(request)

        # 4. Основная логика редиректа для НОВЫХ пользователей
        if is_new:
            # Проверяем, начинается ли текущий путь с одного из разрешенных НАЧАЛ путей
            is_path_allowed = False
            for allowed_start in WIZARD_ACCESSIBLE_PATH_STARTS:
                if current_path.startswith(allowed_start):
                    is_path_allowed = True
                    break # Нашли совпадение, дальше проверять не нужно

            # Если путь НЕ начинается ни с одного из разрешенных...
            if not is_path_allowed:
                # ...перенаправляем на первый шаг визарда.
                # Логируем для отладки, какой путь был заблокирован
                logger.info(f"Redirecting new user {request.user.email} to wizard from path {current_path} (Path not allowed for new users)")
                return redirect(self.wizard_start_url)

        # 5. Если пользователь не новый или находится на разрешенном пути, пропускаем.
        return self.get_response(request)
