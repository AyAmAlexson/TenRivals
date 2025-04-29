from django.contrib.auth.models import BaseUserManager
from django.utils.text import slugify # Для создания username
import re # Для удаления неалфанумериков



class CustomUserManager(BaseUserManager):
    """Define a model manager for User model with no username field."""

    def _generate_unique_username(self, email):
        """Генерирует уникальный username из email."""
        if not email:
            return None # Или вызвать ошибку, если email обязателен
        local_part = email.split('@')[0]
        # Оставляем только буквы, цифры, подчеркивания
        base_username = re.sub(r'[^\w]', '', local_part).lower()
        # Если после очистки ничего не осталось, используем 'user'
        if not base_username:
            base_username = 'user'

        username = base_username
        counter = 1
        # Проверяем уникальность и добавляем счетчик при необходимости
        # self.model ссылается на модель CustomUser
        while self.model.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1
        return username

    def _create_user(self, email, password=None, **extra_fields):
        """Create and save a User with the given email and password."""
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)

        # Генерируем username, если он не передан явно
        if 'username' not in extra_fields or not extra_fields['username']:
             extra_fields['username'] = self._generate_unique_username(email)
        # Дополнительная проверка, если вдруг username все же не уникален
        # (маловероятно с _generate_unique_username, но для надежности)
        if self.model.objects.filter(username=extra_fields['username']).exists():
             raise ValueError('Generated username conflicts, please try again or provide one.')


        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        # Убедимся, что username не передается явно или пустой, чтобы он сгенерировался
        extra_fields.pop('username', None)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        """Create and save a SuperUser with the given email and password."""
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        # У суперюзера тоже генерируем username из email
        extra_fields.pop('username', None)
        return self._create_user(email, password, **extra_fields)