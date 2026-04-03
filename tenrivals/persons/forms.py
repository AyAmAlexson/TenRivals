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
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.contrib.sites.shortcuts import get_current_site
import logging
from allauth.account import app_settings as allauth_account_settings
from allauth.account.models import EmailAddress
from allauth.account.utils import send_email_confirmation
from django.db import transaction
from django.contrib import messages
logger = logging.getLogger(__name__)

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
    old_email = forms.EmailField(
        label='Your Current Email Address',
        widget=forms.EmailInput(attrs={
            'placeholder': 'Your Current Email Address',
            'readonly': True,
            'autocomplete': 'email',
        }),
        required=False
    )
    email = forms.EmailField(
        label='New Email Address',
        widget=forms.EmailInput(attrs={
            'placeholder': 'New Email Address',
            'autocomplete': 'email',
        })
    )
    password = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Current Password',
            'autocomplete': 'current-password',
        })
    )

    def __init__(self, user, request, *args, **kwargs):
        self.user = user
        self.request = request
        super().__init__(*args, **kwargs)
        if self.user:
            self.fields['old_email'].initial = self.user.email

    def clean_email(self):
        email = self.cleaned_data['email']
        normalized = email.lower()
        User = get_user_model()
        if EmailAddress.objects.filter(email__iexact=email).exclude(user=self.user).exists():
            messages.error(
                self.request,
                'This email is already in use. Please check the email address you enter and try again.',
            )
            raise forms.ValidationError('This email is already in use.')
        if User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            messages.error(
                self.request,
                'This email is already registered to another account.',
            )
            raise forms.ValidationError('This email is already in use.')
        if self.user.email.lower() == normalized:
            messages.error(self.request, 'This is already your current email address.')
            raise forms.ValidationError('This is already your current email address.')
        return email

    def clean_password(self):
        password = self.cleaned_data['password']
        if not self.user.check_password(password):
            messages.error(self.request, 'Invalid password. Please check your current password and try again.')
            raise forms.ValidationError('Invalid password.')
        return password

    def save(self):
        new_email = self.cleaned_data['email'].lower()
        user = self.user
        request = self.request

        # With ACCOUNT_CHANGE_EMAIL, allauth expects add_new_email() so the pending
        # address is created/updated and send_confirmation() always runs. Using
        # send_email_confirmation() goes through add_email(), which skips sending if
        # the row already exists and also applies the confirmation cooldown.
        if allauth_account_settings.CHANGE_EMAIL:
            EmailAddress.objects.add_new_email(request, user, new_email)
        else:
            send_email_confirmation(request, user, signup=False, email=new_email)

        logger.info(
            "Confirmation email sent to %s for user %s via allauth (change_email=%s).",
            new_email,
            user.username,
            allauth_account_settings.CHANGE_EMAIL,
        )

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


class AccountUpdateForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ['first_name', 'last_name', 'mobile', 'telegram']
        widgets = {
            'first_name': forms.TextInput(attrs={'placeholder': 'First name'}),
            'last_name': forms.TextInput(attrs={'placeholder': 'Last name'}),
            'mobile': forms.TextInput(attrs={'placeholder': '+995 ...'}),
            'telegram': forms.TextInput(attrs={'placeholder': '@username'}),
        }
