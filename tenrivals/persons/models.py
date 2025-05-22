from django.db import models
from django.contrib.auth.models import AbstractUser
from rivals.const import TR_GEOS, TR_CITIES
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from .managers import CustomUserManager


class CustomUser(AbstractUser):
    # AbstractUser уже имеет поле username с unique=True, max_length=150
    # Мы его не переопределяем здесь, а используем как есть.

    # Email делаем главным и уникальным
    email = models.EmailField(_('email address'), unique=True)

    mobile = models.CharField(max_length=15, null=True, blank=True)
    telegram = models.CharField(max_length=32, null=True, blank=True)
    is_player = models.BooleanField(default=True)
    is_author = models.BooleanField(default=False)
    is_test_user = models.BooleanField(default=False)
    preferred_city = models.CharField(max_length=3, choices=TR_CITIES, default='TBI')
    preferred_geo = models.CharField(max_length=2, choices=TR_GEOS, default='GE')
    is_telegram_verified = models.BooleanField(default=False)

    newsletter_opt_in = models.BooleanField(
        _('agreed to receive newsletter'),
        default=False,
        help_text=_('Indicates whether the user agreed to receive news and offers.')
    )


    # Указываем email как поле для аутентификации
    USERNAME_FIELD = 'email'
    # Указываем, что email ОБЯЗАТЕЛЕН при создании через createsuperuser
    # username не указываем, так как он будет сгенерирован менеджером
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    def __str__(self):
        return self.username


class Author(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='author')
    bio = models.TextField()
    articles_written = models.PositiveIntegerField()


class TelegramVerification(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='telegram_verification')
    telegram_username = models.CharField(max_length=255, blank=True)
    telegram_id = models.CharField(max_length=255, blank=True)
    verification_code = models.CharField(max_length=255, blank=True)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.email} - {self.telegram_username}"
    
    def save(self, *args, **kwargs):
        self.user.is_telegram_verified = self.is_verified
        self.user.save()
        super().save(*args, **kwargs)


class PasswordResetToken(models.Model):
    """
    Stores temporary codes for password reset via Telegram.
    """
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='password_reset_tokens'
    )
    code = models.CharField(max_length=6) # 6-digit code
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    def is_valid(self):
        """
        Checks if the token is still valid (not used and not expired).
        """
        if self.is_used:
            return False
        # Используем настройку, если она есть, иначе 15 минут
        expiry_minutes = getattr(settings, 'PASSWORD_RESET_TELEGRAM_CODE_EXPIRY_MINUTES', 15)
        expiry_time = self.created_at + timedelta(minutes=expiry_minutes)
        return timezone.now() <= expiry_time

    def __str__(self):
        return f"Password Reset Code for {self.user.username} created at {self.created_at}"

    class Meta:
        ordering = ['-created_at']