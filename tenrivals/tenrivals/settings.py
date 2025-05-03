from pathlib import Path
import environ
import django_heroku
import dj_database_url
import os
import logging
from django.contrib.messages import constants as messages
import ssl
import certifi
from celery.schedules import crontab
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env()

SECRET_KEY = env('SECRET_KEY')
# DEBUG = (os.environ.get('DEBUG_VALUE') == 'True')
DEBUG = (os.environ.get('DEBUG_VALUE', 'False') == 'True')

ALLOWED_HOSTS = [
    'https://ten-rivals-ee84d08ca066.herokuapp.com/',
    '127.0.0.1',
    'distinct-shrimp-slightly.ngrok-free.app'
]

CSRF_TRUSTED_ORIGINS = [
    'https://ten-rivals-ee84d08ca066.herokuapp.com/',
    'http://localhost:8000',
    'http://127.0.0.1:8000',
    'http://localhost:80',
    'http://127.0.0.1:80',
    'https://distinct-shrimp-slightly.ngrok-free.app'
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.sites',
    'allauth_ui',
    'django.contrib.staticfiles',
    'django_filters',
    'django.contrib.flatpages',

    'persons',
    'rivals.apps.RivalsConfig',
    
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'allauth.socialaccount.providers.facebook',
    'widget_tweaks',

    'django_celery_beat'


]


MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django.contrib.flatpages.middleware.FlatpageFallbackMiddleware',
    'persons.middleware.PlayerWizardMiddleware',
    
]


if DEBUG:
    INSTALLED_APPS.append('debug_toolbar')
    MIDDLEWARE.insert(0, 'debug_toolbar.middleware.DebugToolbarMiddleware')


    INTERNAL_IPS = [
        '127.0.0.1',
    ]
    DEBUG_TOOLBAR_CONFIG = {
       'SHOW_TOOLBAR_CALLBACK': lambda request: True,
       'RESULTS_CACHE_SIZE': 100,
   }

ROOT_URLCONF = 'tenrivals.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'rivals.context_processors.test_users',
                'rivals.context_processors.ticket_form',
                'persons.context_processors.telegram_settings',
            ],
        },
    },
]

WSGI_APPLICATION = 'tenrivals.wsgi.application'


DATABASE_URL = env('DATABASE_URL')
db_from_env = dj_database_url.config(default=DATABASE_URL)
DATABASES = {'default': db_from_env}
CONN_MAX_AGE = int(os.environ.get("CONN_MAX_AGE", 600))


AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

AUTH_USER_MODEL = 'persons.CustomUser'

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'
USE_TZ = True
USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/


STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
]
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'


IS_PRODUCTION = 'HEROKU' in os.environ

if IS_PRODUCTION:
    # --- Настройки для Heroku (используем AWS S3) ---

    # Читаем ключи и настройки S3 из переменных окружения Heroku
    AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
    AWS_STORAGE_BUCKET_NAME = os.environ.get('AWS_STORAGE_BUCKET_NAME')
    AWS_S3_REGION_NAME = os.environ.get('AWS_S3_REGION_NAME')
    AWS_S3_CUSTOM_DOMAIN = os.environ.get('AWS_S3_CUSTOM_DOMAIN', None) # Необязательно, для CDN

    # Проверка наличия обязательных переменных на Heroku
    if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_STORAGE_BUCKET_NAME, AWS_S3_REGION_NAME]):
        raise ValueError("AWS S3 credentials are not fully configured in Heroku environment variables.")

    # Настройки django-storages для S3
    AWS_S3_SIGNATURE_VERSION = 's3v4'
    AWS_S3_FILE_OVERWRITE = False # Не перезаписывать файлы при загрузке с тем же именем
    AWS_DEFAULT_ACL = None        # Используем Bucket Policy для публичного чтения
    AWS_S3_VERIFY = True          # Проверять SSL сертификат при подключении к S3

    # Указываем Django использовать S3 для хранения медиафайлов по умолчанию
    DEFAULT_FILE_STORAGE = 'storages.backends.s3.S3Storage'

    # Указываем папку внутри бакета, куда будут загружаться медиафайлы
    AWS_LOCATION = 'media'

    # Формируем публичный URL для доступа к медиафайлам
    if AWS_S3_CUSTOM_DOMAIN:
        MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/{AWS_LOCATION}/'
    else:
        MEDIA_URL = f'https://{AWS_STORAGE_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/{AWS_LOCATION}/'

    # MEDIA_ROOT не используется при хранении в S3
    MEDIA_ROOT = None # Явно указываем None для ясности

else:
    # Используем стандартное файловое хранилище
    DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'

    # URL для доступа к медиафайлам через сервер разработки Django
    MEDIA_URL = '/media/'

    # Локальная папка для хранения медиафайлов
    MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

    # Создаем папку media локально при запуске, если ее нет
    os.makedirs(MEDIA_ROOT, exist_ok=True)


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

SITE_ID = 1
LOGIN_URL = 'account_login'
LOGOUT_REDIRECT_URL = LOGIN_URL
LOGIN_REDIRECT_URL = '/today/'
ACCOUNT_LOGOUT_REDIRECT_URL = '/'

ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_AUTHENTICATION_METHOD = 'email'
ACCOUNT_EMAIL_VERIFICATION = 'mandatory'
ACCOUNT_CONFIRM_EMAIL_ON_GET = True
ACCOUNT_USER_MODEL_USERNAME_FIELD = 'username'
ACCOUNT_ADAPTER = 'persons.adapters.CustomAccountAdapter'

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
# EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'

EMAIL_HOST = 'smtp.office365.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_USE_SSL = False
EMAIL_HOST_USER = env('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD')
SERVER_EMAIL = env('EMAIL_HOST_USER')
DEFAULT_FROM_EMAIL = env('EMAIL_HOST_USER')
ACCOUNT_EMAIL_SUBJECT_PREFIX = '[TenRivals] '


ACCOUNT_FORMS = {
    'signup': 'persons.forms.CustomSignupForm',
    'login': 'persons.forms.CustomLoginForm',
}


django_heroku.settings(locals())

logging.basicConfig(level=logging.DEBUG)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {asctime} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': os.path.join(BASE_DIR, 'debug.log'),
            'formatter': 'verbose',
        },
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'loggers': {
        'persons': {
            'handlers': ['file', 'console'],
            'level': 'DEBUG',
            'propagate': False,
        },
        # 'rivals': {  # <--- Временно закомментируем этот блок
        #     'handlers': ['file', 'console'],
        #     'level': 'DEBUG',
        #     'propagate': False,
        # },
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'botocore': {
            'handlers': ['console', 'file'],
            'level': 'WARNING',
            'propagate': False,
        },
        'boto3': {
            'handlers': ['console', 'file'],
            'level': 'WARNING',
            'propagate': False,
        },
        'urllib3': {
             'handlers': ['console', 'file'],
             'level': 'WARNING',
             'propagate': False,
        },
    },
}

MESSAGE_TAGS = {
    messages.DEBUG: 'secondary',
    messages.INFO: 'info',
    messages.SUCCESS: 'success',
    messages.WARNING: 'warning',
    messages.ERROR: 'danger',
}

# Настройка SSL контекста с использованием certifi
EMAIL_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# Celery Configuration
CELERY_BROKER_URL = env.str('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = env.str('CELERY_RESULT_BACKEND', default='redis://localhost:6379/0')
CELERY_TIMEZONE = env.str('TIME_ZONE', default='UTC')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True


CELERY_BEAT_SCHEDULE = {
    'approve-matches-daily': {
        'task': 'rivals.tasks.approve_match_results_by_timeout',
        'schedule': crontab(hour=6, minute=0),  # Выполнять в 6 утра каждый день
        
    },

    'cleanup-old-events': {
        'task': 'rivals.tasks.cleanup_old_events',
        'schedule': crontab(hour=0, minute=0),  # Выполнять в полночь каждый день
    },

    'check-overdue-matches': {
        'task': 'rivals.tasks.check_overdue_matches',
        'schedule': crontab(hour=0, minute=0),  # Выполнять в полночь каждый день
    },

    'delete-outdated-password-reset-tokens': {
        'task': 'persons.tasks.delete_outdated_password_reset_tokens',
        'schedule': crontab(hour=0, minute=0),  # Выполнять в полночь каждый день
    },
}


SESSION_ENGINE = 'django.contrib.sessions.backends.db'  # или другой бэкенд по вашему выбору
SESSION_COOKIE_AGE = 86400  # время жизни сессии в секундах (24 часа)
SESSION_SAVE_EVERY_REQUEST = True  # сохранять сессию при каждом запросе
SESSION_COOKIE_SECURE = False  # для локальной разработки
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# Настройки для социальной аутентификации
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': [
            'profile',
            'email',
        ],
        'AUTH_PARAMS': {
            'access_type': 'online',
        }
    },
    'facebook': {
        'METHOD': 'oauth2',
        'SCOPE': ['email', 'public_profile'],
        'AUTH_PARAMS': {'auth_type': 'reauthenticate'},
        'FIELDS': [
            'id',
            'email',
            'name',
            'first_name',
            'last_name',
            'verified',
            'locale',
            'timezone',
            'link',
            'gender',
            'updated_time',
        ],
        'EXCHANGE_TOKEN': True,
        'VERIFIED_EMAIL': False,
        'VERSION': 'v13.0',
    }
}

# Telegram Bot settings
TELEGRAM_BOT_TOKEN = env.str('TELEGRAM_BOT_TOKEN', default=None)
TELEGRAM_BOT_USERNAME = env.str('TELEGRAM_BOT_USERNAME', default=None)
TELEGRAM_BOT_ID = env.str('TELEGRAM_BOT_ID', default=None)

# Добавляем URL сайта для формирования ссылок
WEBSITE_URL = 'https://distinct-shrimp-slightly.ngrok-free.app'  # Замените на реальный URL вашего сайта


PASSWORD_RESET_TELEGRAM_CODE_EXPIRY_MINUTES = 15