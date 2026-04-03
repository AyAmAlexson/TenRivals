from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.account.utils import user_email
from django.forms import ValidationError
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from rivals.models import Player, PlayerOnboarding
import logging

logger = logging.getLogger(__name__)
User = get_user_model()

class CustomAccountAdapter(DefaultAccountAdapter):

    def validate_unique_email(self, email):
        """
        Проверяет уникальность email и вызывает ValidationError, если он занят.
        Это переопределяет поведение по умолчанию, которое может отправлять email
        вместо отображения ошибки в форме при определенных настройках.
        """
        # Приводим email к нижнему регистру для сравнения без учета регистра
        email_lower = email.lower()
        # Проверяем, существует ли пользователь с таким email (без учета регистра)
        if User.objects.filter(email__iexact=email_lower).exists():
            # Вызываем ValidationError, которую форма сможет отобразить
            raise ValidationError(
                _("An account is already registered with this email address. Please log in or reset your password.")
            )
        # Вызывать super().validate_unique_email(email) не обязательно,
        # если нам нужна только эта проверка. Но можно оставить для будущей совместимости.
        # super().validate_unique_email(email)
        return email # Важно вернуть email, если он прошел проверку

    def confirm_email(self, request, email_address):
        """
        After allauth confirmation, ensure the verified row for the user's email is primary.

        With ACCOUNT_CHANGE_EMAIL=True, allauth can leave the new address verified but not
        primary after removing the old address, so is_primary_email_verified stays false.
        """
        result = super().confirm_email(request, email_address)
        if not result:
            return False
        try:
            email_address.refresh_from_db()
        except EmailAddress.DoesNotExist:
            return True
        user = email_address.user
        desired = (user_email(user) or email_address.email or "").strip().lower()
        if not desired:
            return True
        verified_row = EmailAddress.objects.filter(
            user=user, email__iexact=desired, verified=True
        ).first()
        if verified_row and not verified_row.primary:
            verified_row.set_as_primary(conditional=False)
        return True

    def save_user(self, request, user, form, commit=True):
        """
        Переопределяем метод save_user, чтобы автоматически создавать
        профиль Player после создания пользователя.
        """
        # Сначала вызываем стандартный метод save_user из allauth,
        # который фактически создает и сохраняет объект CustomUser.
        user = super().save_user(request, user, form, commit=False)

        # Дополнительные действия после создания пользователя:
        user.newsletter_opt_in = form.cleaned_data.get('newsletter_opt_in', False)

        # Сохраняем пользователя, если commit=True
        if commit:
            user.save()

            # --- ЛОГИКА СОЗДАНИЯ PLAYER ---
            # Убедимся, что Player создается только один раз, если commit=True
            # (хотя save_user обычно вызывается с commit=True при стандартной регистрации)
            if not hasattr(user, 'player'):
                try:
                    player = Player.objects.create(user=user)
                    logger.info(f"Player profile created via adapter for new user: {user.email}")
                    onboarding, created = PlayerOnboarding.objects.get_or_create(player=player)
                    if created:
                        logger.info(f"Player onboarding created via adapter for new user: {user.email}")
                except Exception as e:
                    # Логгируем ошибку, если создание Player не удалось
                    logger.error(f"Failed to create Player profile via adapter for user {user.email}: {e}")
            # -----------------------------

        return user
