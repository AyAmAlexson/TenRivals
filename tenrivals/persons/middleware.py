from django.shortcuts import redirect
from django.urls import reverse, NoReverseMatch
from rivals.models import Player # Убедитесь, что импорт корректен
import logging

logger = logging.getLogger(__name__)

# Множество имен URL, которые ДОСТУПНЫ для новых пользователей
# (проходящих визард)
WIZARD_ACCESSIBLE_URL_NAMES = {
    # Все шаги визарда
    'rivals:player_wizard_step1',
    'rivals:player_wizard_step2',
    'rivals:player_wizard_step3',
    'rivals:player_wizard_step4',
    'rivals:player_wizard_step5',

    # Стандартные операции с аккаунтом
    'account_logout',
    'account_change_password', # Смена пароля должна быть доступна
    'account_reset_password',
    'account_reset_password_done',
    'account_reset_password_from_key',
    'account_reset_password_from_key_done',

    # Возможно, связанные с Telegram callback/verify, если они нужны до завершения визарда
    'persons:telegram_callback',
    'persons:verify_telegram',
    'persons:resend_verification_code',

    # Добавьте СЮДА другие имена URL, которые должны быть доступны ВСЕГДА
    # например, страница ошибки, страница поддержки?
}

# Пути, которые должны быть доступны всегда (например, админка, статика)
ALWAYS_ACCESSIBLE_PATHS_START = [
    '/',
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

        # 2. Получаем информацию о текущем запросе
        current_url_name = getattr(request.resolver_match, 'url_name', None)
        current_path = request.path

        # 3. Пропускаем всегда доступные пути (админка, статика и т.д.)
        for path_start in ALWAYS_ACCESSIBLE_PATHS_START:
            if current_path.startswith(path_start):
                return self.get_response(request)

        # 4. Получаем статус игрока (is_new)
        try:
            # Оптимизируем запрос, получая только нужное поле
            # Используем filter().first() чтобы избежать ошибки, если игрока еще нет
            player_data = Player.objects.filter(user=request.user).values('is_new').first()
            # Если профиля игрока нет, считаем его новым (is_new=True)
            is_new = player_data['is_new'] if player_data else True
        except Exception as e:
            # При любой другой ошибке получения профиля - пропускаем middleware
            logger.error(f"PlayerWizardMiddleware: Error fetching player for user {request.user.email}: {e}")
            return self.get_response(request)

        # 5. Основная логика редиректа
        # Если пользователь "новый" (is_new=True)...
        if is_new:
            # ...и он пытается зайти НЕ на одну из разрешенных для визарда страниц...
            if current_url_name not in WIZARD_ACCESSIBLE_URL_NAMES:
                # ...перенаправляем его на первый шаг визарда.
                logger.info(f"Redirecting new user {request.user.email} to wizard from {current_path} (url_name: {current_url_name})")
                return redirect(self.wizard_start_url)
        # 6. Если пользователь не новый или находится на разрешенной странице,
        # пропускаем запрос дальше.
        return self.get_response(request)
