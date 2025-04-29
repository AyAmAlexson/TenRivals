from allauth.account.adapter import DefaultAccountAdapter
from django.forms import ValidationError
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model

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
