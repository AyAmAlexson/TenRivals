import hashlib
from django.utils import timezone

def generate_verification_code_service():
    # Генерация случайного кода верификации
    return hashlib.sha256(str(timezone.now().timestamp()).encode()).hexdigest()[:6]

def delete_outdated_password_reset_tokens_service():
    from .models import PasswordResetToken
    from django.utils import timezone
    from datetime import timedelta
    from django.conf import settings
    
    # Определяем порог удаления (например, все старше 1 дня)
    delete_threshold = timezone.now() - timedelta(days=1)

    # Получаем срок действия из настроек + небольшой буфер (например, 1 час)
    expiry_minutes = getattr(settings, 'PASSWORD_RESET_TELEGRAM_CODE_EXPIRY_MINUTES', 15)
    absolute_expiry_threshold = timezone.now() - timedelta(minutes=expiry_minutes, hours=1)

    # Удаляем использованные токены старше порога
    deleted_used_count, _ = PasswordResetToken.objects.filter(
        is_used=True,
        created_at__lt=delete_threshold
    ).delete()

    # Удаляем неиспользованные, но точно просроченные токены
    deleted_expired_count, _ = PasswordResetToken.objects.filter(
        is_used=False,
        created_at__lt=absolute_expiry_threshold
    ).delete()

    total_deleted = deleted_used_count + deleted_expired_count
    
    return f'Successfully deleted {total_deleted} old password reset tokens.'
