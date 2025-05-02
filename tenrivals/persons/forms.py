from allauth.account.forms import SignupForm, LoginForm
from django import forms
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from .models import CustomUser
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.contrib.auth.tokens import default_token_generator
from django.conf import settings
from .services import generate_verification_code_service
from django.utils.translation import gettext_lazy as _
from django.urls import reverse


def generate_verification_code():
    # Генерация случайного кода верификации
    return generate_verification_code_service()

class CustomLoginForm(LoginForm):
    def __init__(self, *args, **kwargs):
        super(CustomLoginForm, self).__init__(*args, **kwargs)
        self.fields['login'].widget = forms.EmailInput(attrs={
            'class': 'form-control form-control-solid',
            'placeholder': 'Enter your email address',
            'autofocus': True
        })
        
        self.fields['password'].widget = forms.PasswordInput(attrs={
            'class': 'form-control form-control-solid',
            'placeholder': 'Password'
        })
        self.fields['remember'].widget = forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
        self.fields['login'].label = 'Email'
        self.fields['password'].label = 'Password'
        self.fields['remember'].label = 'Remember me'
    


class CustomSignupForm(SignupForm):
    terms_accepted = forms.BooleanField(
        label=_('I accept the Terms and Conditions'),
        required=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    newsletter_opt_in = forms.BooleanField(
        label=_('I agree to receive news and offers'),
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    def __init__(self, *args, **kwargs):
        super(CustomSignupForm, self).__init__(*args, **kwargs)
        self.fields['email'].widget = forms.EmailInput(
            attrs={'class': 'form-control form-control-solid',
                   'placeholder': 'Email address',
                   'autofocus': True}
        )
        self.fields['password1'].widget = forms.PasswordInput(
            attrs={'class': 'form-control form-control-solid',
                   'placeholder': 'Password'}
        )
        self.fields['password2'].widget = forms.PasswordInput(
            attrs={'class': 'form-control form-control-solid',
                   'placeholder': 'Password (Again)'}
        )
        self.fields['email'].label = 'Email'
        self.fields['password1'].label = 'Password'
        self.fields['password2'].label = 'Confirm Password'

    def save(self, request):
        user = super(CustomSignupForm, self).save(request)
        user.newsletter_opt_in = self.cleaned_data.get('newsletter_opt_in', False)
        user.save(update_fields=['newsletter_opt_in'])
        return user

    class Meta:
        model = CustomUser


class TelegramVerificationForm(forms.Form):
    verification_code = forms.CharField(max_length=32)


class ChangeEmailForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        'class': 'form-control form-control-solid',
        'placeholder': 'New Email Address'
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'form-control form-control-solid',
        'placeholder': 'Current Password'
    }))

    def __init__(self, user, request, *args, **kwargs):
        self.user = user
        self.request = request
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data['email']
        User = get_user_model()
        if User.objects.filter(email=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError('This email is already in use.')
        return email

    def clean_password(self):
        password = self.cleaned_data['password']
        if not self.user.check_password(password):
            raise forms.ValidationError('Invalid password.')
        return password

    def save(self):
        new_email = self.cleaned_data['email']
        user = self.user
        
        # Сохраняем новый email
        user.email = new_email
        user.is_email_verified = False
        user.save(update_fields=['email', 'is_email_verified'])

        # Отправляем письмо для подтверждения
        token = default_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))

        # Получаем ключ подтверждения от allauth (он объединяет uid и token)
        # Для allauth ключ передается как один параметр в URL
        key = f"{uid}:{token}" # Формат может зависеть от версии allauth, но обычно так
                              # Или используем метод allauth для генерации, если он есть

        # Генерируем ПОЛНЫЙ URL с помощью reverse и имени allauth-шаблона
        # Стандартное имя для подтверждения email в allauth - 'account_confirm_email'
        relative_url = reverse('account_confirm_email', kwargs={'key': key})
        confirmation_link = self.request.build_absolute_uri(relative_url)

        context = {
            'user': user,
            'confirmation_link': confirmation_link,
        }

        # Пример использования стандартного текстового шаблона allauth:
        email_subject = _('Confirm Your Email Address Change')
        email_template_name = 'account/email/email_confirmation_message.txt' # Текстовый шаблон allauth
        # Если хотите HTML, укажите 'account/email/email_confirmation_message.html'
        # и передайте html_message=message в send_mail
        
        message = render_to_string(email_template_name, context)

        send_mail(
            email_subject, # Используем переменную для темы
            message,
            settings.DEFAULT_FROM_EMAIL,
            [new_email],
            # html_message=message, # Раскомментируйте, если используете HTML шаблон
        )
        logger.info(f"Email change confirmation sent to {new_email} for user {user.username}")

        return user


class PasswordResetRequestTelegramForm(forms.Form):
    """
    Форма для запроса сброса пароля через Telegram.
    """
    telegram_username = forms.CharField(
        label="Telegram Username",
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your Telegram username (without @)',
            'autofocus': True,
        })
    )

    def clean_telegram_username(self):
        """
        Удаляем '@' в начале, если он есть.
        """
        username = self.cleaned_data.get('telegram_username')
        if username and username.startswith('@'):
            username = username[1:]
        return username


class PasswordResetCodeEntryForm(forms.Form):
    """
    Форма для ввода 6-значного кода, полученного в Telegram.
    """
    verification_code = forms.CharField(
        label="Verification Code",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg text-center',
            'placeholder': '000000',
            'autocomplete': 'off',
            'inputmode': 'numeric', # Подсказка для мобильных устройств
            'pattern': '[0-9]*',   # Дополнительная валидация на клиенте
            'style': 'letter-spacing: 0.5rem; font-weight: 500;', # Стиль как в verify_telegram
            'autofocus': True,
        }),
        help_text="Enter the 6-digit code sent to your Telegram.",
        error_messages={
            'min_length': "Code must be exactly 6 digits long.",
            'max_length': "Code must be exactly 6 digits long.",
        }
    )

    def clean_verification_code(self):
        """
        Проверяем, что код состоит только из цифр.
        """
        code = self.cleaned_data.get('verification_code')
        if code and not code.isdigit():
            raise forms.ValidationError("Code must contain only digits.")
        return code


