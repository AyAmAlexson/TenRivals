from django.shortcuts import redirect
from django.urls import reverse, NoReverseMatch
from rivals.models import Player # Предполагаем, что модель Player здесь
import logging

logger = logging.getLogger(__name__)

# Используй ИМЕНА URL для надежности
ALWAYS_ACCESSIBLE_URL_NAMES = {
    'rivals:player_wizard', # Сам визард
    'account_logout',      # Выход из системы
    'persons:telegram_callback', # Для обработки ответа от Telegram
    # URLы сброса/смены пароля (если они должны быть доступны всегда)
    'account_password_request_tg',
    'account_password_reset_code_entry_tg',
    'account_reset_password',
    'account_reset_password_done',
    'account_reset_password_from_key',
    'account_reset_password_from_key_done',
    'account_change_password',
    # Добавь сюда другие URL-имена, которые не должны блокироваться
}

# Пути, которые должны быть доступны (например, админка, статика)
ALWAYS_ACCESSIBLE_PATHS_START = [
    '/admin/',
    '/static/',
    '/media/',
    # Добавь другие пути, если нужно
]

class PlayerWizardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        try:
            # Кэшируем URL визарда для производительности
            self.wizard_url = reverse('rivals:player_wizard')
        except NoReverseMatch:
            logger.error("PlayerWizardMiddleware: URL 'rivals:player_wizard' not found. Middleware might not work correctly.")
            self.wizard_url = '/player/wizard/' # Fallback

    def __call__(self, request):
        response = self.get_response(request) # Сначала получаем ответ

        # Работаем только с аутентифицированными пользователями
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return response

        # Получаем информацию о текущем запросе
        current_url_name = getattr(request.resolver_match, 'url_name', None)
        current_path = request.path

        # Пропускаем всегда доступные URL по имени или пути
        if current_url_name in ALWAYS_ACCESSIBLE_URL_NAMES:
            return response
        for path_start in ALWAYS_ACCESSIBLE_PATHS_START:
            if current_path.startswith(path_start):
                return response

        # Пропускаем URLы allauth, кроме тех, что явно разрешены (осторожно)
        if current_path.startswith('/accounts/') and current_url_name not in ALWAYS_ACCESSIBLE_URL_NAMES:
             # Дополнительная проверка: не блокируем сам визард, если его URL начинается с /accounts/
             if current_path != self.wizard_url:
                 return response


        # Основная логика: проверяем флаг is_new
        try:
            # Оптимизируем запрос, получая только нужное поле
            player = Player.objects.values('is_new').get(user=request.user)
            is_new = player['is_new']
        except Player.DoesNotExist:
            # Если профиля нет, считаем пользователя новым
            is_new = True
        except Exception as e:
            logger.error(f"PlayerWizardMiddleware: Error fetching player for user {request.user.email}: {e}")
            # При ошибке не блокируем пользователя
            return response

        # Если пользователь новый и находится НЕ на странице визарда -> редирект
        if is_new and current_path != self.wizard_url:
            logger.info(f"Redirecting new user {request.user.email} to wizard from {current_path}")
            return redirect(self.wizard_url)

        # Если все проверки пройдены, возвращаем исходный ответ
        return response
