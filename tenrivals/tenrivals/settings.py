from pathlib import Path
import environ
import django_heroku
import dj_database_url
import os
from django.contrib.messages import constants as messages
from celery.schedules import crontab
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()

SECRET_KEY = env('SECRET_KEY')
# DEBUG controlled via env; default False (production-safe)
DEBUG = env.bool('DEBUG', default=False)
USE_S3 = env.bool('USE_S3', default=('DYNO' in os.environ))

ALLOWED_HOSTS = [
    '127.0.0.1',
    'localhost',
    'tenrivals.com',
    '.tenrivals.com',
]
_EXTRA_ALLOWED = env.list('EXTRA_ALLOWED_HOSTS', default=[])
if _EXTRA_ALLOWED:
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, *_EXTRA_ALLOWED]

CSRF_TRUSTED_ORIGINS = [
    'http://localhost:8000',
    'http://127.0.0.1:8000',
    'http://localhost:80',
    'http://127.0.0.1:80',
    'https://tenrivals.com',
    'https://www.tenrivals.com',
]
_EXTRA_CSRF = env.list('EXTRA_CSRF_TRUSTED_ORIGINS', default=[])
if _EXTRA_CSRF:
    CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, *_EXTRA_CSRF]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.sites',
    'allauth_ui',
    'django.contrib.staticfiles',
    'django.contrib.sitemaps',
    'django_filters',
    'django.contrib.flatpages',

    'persons',
    'rivals.apps.RivalsConfig',
    'shop.apps.ShopConfig',
    'buying.apps.BuyingConfig',

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
    'tenrivals.canonical_host_middleware.CanonicalHostMiddleware',
    # Reject crawl/filter spam before session/auth work (keeps dyno alive under bot floods).
    'shop.middleware.AbuseShieldMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'shop.middleware.AcquisitionAttributionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'shop.middleware.SiteLocaleMiddleware',
    'shop.middleware.MaintenanceModeMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
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

APPEND_SLASH = True
ROOT_URLCONF = 'tenrivals.urls'
CSRF_FAILURE_VIEW = 'tenrivals.error_views.csrf_failure'

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
                'shop.context_processors.shop_cart',
                'shop.context_processors.shop_i18n',
            ],
        },
    },
]

WSGI_APPLICATION = 'tenrivals.wsgi.application'


_CONN_MAX_AGE = int(os.environ.get('CONN_MAX_AGE', 600))
_DATABASE_URL = env.str('DATABASE_URL', default='').strip()
if _DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.config(
            default=_DATABASE_URL,
            conn_max_age=_CONN_MAX_AGE,
            ssl_require=bool(os.environ.get('DYNO')),
        )
    }
    # Reuse pooled SSL connections but verify before each request (avoids
    # OperationalError: SSL error: unexpected eof while reading on Heroku/RDS).
    if _CONN_MAX_AGE:
        DATABASES['default']['CONN_HEALTH_CHECKS'] = True
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': env.str('PGDATABASE', default='tenrivals_local_dev'),
            'USER': env.str('PGUSER', default='postgres'),
            'PASSWORD': env.str('PGPASSWORD', default=''),
            'HOST': env.str('PGHOST', default='localhost'),
            'PORT': env.str('PGPORT', default='5432'),
        }
    }


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

LANGUAGE_COOKIE_NAME = 'django_language'

LOCALE_PATHS = [
    os.path.join(BASE_DIR, 'locale'),
]

LANGUAGES = [
    ('en', 'English'),
    ('ru', 'Russian'),
    ('ka', 'Georgian'),
]

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/


STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
]
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'


IS_PRODUCTION = USE_S3

if IS_PRODUCTION:
    # --- Настройки для Heroku (используем AWS S3) ---

    # Читаем ключи и настройки S3 из переменных окружения Heroku
    AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
    AWS_STORAGE_BUCKET_NAME = os.environ.get('AWS_STORAGE_BUCKET_NAME')
    AWS_S3_REGION_NAME = os.environ.get('AWS_S3_REGION_NAME')
    # Set AWS_S3_CUSTOM_DOMAIN to a CloudFront (or other CDN) hostname so media is served
    # edge-cached globally — biggest win for large hero/blog images without code changes.
    AWS_S3_CUSTOM_DOMAIN = os.environ.get('AWS_S3_CUSTOM_DOMAIN', None)  # e.g. dxxxx.cloudfront.net

    # Проверка наличия обязательных переменных на Heroku
    if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_STORAGE_BUCKET_NAME, AWS_S3_REGION_NAME]):
        raise ValueError("AWS S3 credentials are not fully configured in Heroku environment variables.")

    # Настройки django-storages для S3
    AWS_S3_SIGNATURE_VERSION = 's3v4'
    AWS_S3_FILE_OVERWRITE = False # Не перезаписывать файлы при загрузке с тем же именем
    AWS_DEFAULT_ACL = None        # Используем Bucket Policy для публичного чтения
    AWS_S3_VERIFY = True          # Проверять SSL сертификат при подключении к S3
    # Публичные URL без подписи для лучшего кэширования CDN/браузером
    AWS_QUERYSTRING_AUTH = False
    # Агрессивное кэширование для медиа (можно переопределить на уровне CDN)
    AWS_S3_OBJECT_PARAMETERS = {
        'CacheControl': 'public, max-age=31536000, s-maxage=31536000, immutable'
    }

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


def _normalize_site_host(host: str) -> str:
    """Strip scheme/path noise and www. prefix; default apex tenrivals.com."""
    h = (host or '').strip().lower()
    if '://' in h:
        from urllib.parse import urlparse

        h = urlparse(h).hostname or ''
    h = h.split('/', 1)[0]
    if h.startswith('www.'):
        h = h[4:]
    return h or 'tenrivals.com'


# django.contrib.sites, sitemaps, canonical/og URLs — apex host only (no www).
CANONICAL_HOST = _normalize_site_host(
    env.str('CANONICAL_HOST', default=env.str('SITE_DOMAIN', default='tenrivals.com'))
)
SITE_DOMAIN = CANONICAL_HOST
SITE_DISPLAY_NAME = env.str('SITE_DISPLAY_NAME', default='Tennis Rivals')

LOGIN_URL = 'account_login'
LOGOUT_REDIRECT_URL = LOGIN_URL
LOGIN_REDIRECT_URL = '/shop/'
ACCOUNT_LOGOUT_REDIRECT_URL = '/'

ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_AUTHENTICATION_METHOD = 'email'
ACCOUNT_EMAIL_VERIFICATION = 'mandatory'
ACCOUNT_CONFIRM_EMAIL_ON_GET = True
ACCOUNT_EMAIL_CONFIRMATION_AUTHENTICATED_REDIRECT_URL = '/persons/my_account/'
ACCOUNT_EMAIL_CONFIRMATION_ANONYMOUS_REDIRECT_URL = '/persons/my_account/'
ACCOUNT_USER_MODEL_USERNAME_FIELD = 'username'
ACCOUNT_ADAPTER = 'persons.adapters.CustomAccountAdapter'
ACCOUNT_CHANGE_EMAIL = True


# Optional SendGrid SMTP (off unless EMAIL_USE_SENDGRID=true). Requires SENDGRID_API_KEY
# (username "apikey") or SENDGRID_USERNAME / SENDGRID_PASSWORD. Set DEFAULT_FROM_EMAIL to a
# verified sender when using API key login.
if env.bool("EMAIL_USE_SENDGRID", default=False):
    EMAIL_HOST = env("SENDGRID_SMTP_HOST", default="smtp.sendgrid.net")
    EMAIL_PORT = env.int("SENDGRID_SMTP_PORT", default=587)
    EMAIL_USE_TLS = True
    EMAIL_USE_SSL = False
    _sendgrid_key = env("SENDGRID_API_KEY", default=None)
    if _sendgrid_key:
        EMAIL_HOST_USER = "apikey"
        EMAIL_HOST_PASSWORD = _sendgrid_key
    else:
        EMAIL_HOST_USER = env("SENDGRID_USERNAME")
        EMAIL_HOST_PASSWORD = env("SENDGRID_PASSWORD")
else:
    EMAIL_HOST = env("EMAIL_HOST", default="smtpout.secureserver.net")
    EMAIL_PORT = env.int("EMAIL_PORT", default=465)
    EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
    EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=True)
    EMAIL_HOST_USER = env("EMAIL_HOST_USER")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")

EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=30)

_explicit_default_from = env("DEFAULT_FROM_EMAIL", default=None)
if _explicit_default_from:
    DEFAULT_FROM_EMAIL = _explicit_default_from
elif EMAIL_HOST_USER == "apikey":
    DEFAULT_FROM_EMAIL = env(
        "DEFAULT_FROM_EMAIL",
        default="Tennis Rivals Shop <noreply@tenrivals.com>",
    )
else:
    DEFAULT_FROM_EMAIL = f"Tennis Rivals Shop <{EMAIL_HOST_USER}>"

SERVER_EMAIL = env("SERVER_EMAIL", default=None) or (
    EMAIL_HOST_USER if EMAIL_HOST_USER != "apikey" else DEFAULT_FROM_EMAIL
)
ACCOUNT_EMAIL_SUBJECT_PREFIX = ""

_ADMINS_RAW = env.str('DJANGO_ADMINS', default='').strip()
ADMINS = []
if _ADMINS_RAW:
    for _part in _ADMINS_RAW.split(','):
        _part = _part.strip()
        if not _part:
            continue
        if '<' in _part and _part.endswith('>'):
            _name, _, _rest = _part.partition('<')
            _email = _rest[:-1].strip()
            if _email:
                ADMINS.append((_name.strip() or 'Admin', _email))
        elif '@' in _part:
            ADMINS.append(('Admin', _part))
MANAGERS = ADMINS


ACCOUNT_FORMS = {
    'signup': 'persons.forms.CustomSignupForm',
    'login': 'persons.forms.CustomLoginForm',
}


# Do not let django-heroku replace ALLOWED_HOSTS with ['*'], duplicate WhiteNoise, or wipe LOGGING.
django_heroku.settings(locals(), allowed_hosts=False, staticfiles=False, logging=False)

_IS_HEROKU = 'DYNO' in os.environ
_LOG_TO_FILE = env.bool('DJANGO_LOG_FILE', default=(not _IS_HEROKU and DEBUG))
_APP_LOG_LEVEL = env.str('DJANGO_LOG_LEVEL', default='DEBUG' if DEBUG else 'INFO')
_LOG_HANDLERS = ['console', 'file'] if _LOG_TO_FILE else ['console']

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
            'handlers': _LOG_HANDLERS,
            'level': _APP_LOG_LEVEL,
            'propagate': False,
        },
        'rivals': {
            'handlers': _LOG_HANDLERS,
            'level': _APP_LOG_LEVEL,
            'propagate': False,
        },
        'django': {
            'handlers': _LOG_HANDLERS,
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': _LOG_HANDLERS,
            'level': 'ERROR',
            'propagate': False,
        },
        'botocore': {
            'handlers': _LOG_HANDLERS,
            'level': 'WARNING',
            'propagate': False,
        },
        'boto3': {
            'handlers': _LOG_HANDLERS,
            'level': 'WARNING',
            'propagate': False,
        },
        'urllib3': {
            'handlers': _LOG_HANDLERS,
            'level': 'WARNING',
            'propagate': False,
        },
        'shop': {
            'handlers': _LOG_HANDLERS,
            'level': _APP_LOG_LEVEL,
            'propagate': False,
        },
        'buying': {
            'handlers': _LOG_HANDLERS,
            'level': _APP_LOG_LEVEL,
            'propagate': False,
        },
    },
}

# Buying module (staff sourcing tool). AI provider is pluggable; see buying/ai/.
# All AI knobs are Config Vars — change models/provider without code deploys.
BUYING_AI_PROVIDER = env.str('BUYING_AI_PROVIDER', default='openai')
BUYING_NORMALIZATION_MODEL = env.str(
    'BUYING_NORMALIZATION_MODEL',
    default=env.str('BUYING_OPENAI_MODEL', default='gpt-5.5'),
)
BUYING_MATCH_MODEL = env.str(
    'BUYING_MATCH_MODEL',
    default=env.str('BUYING_OPENAI_MODEL', default='gpt-5.5'),
)
# Legacy alias kept for older env files; prefer BUYING_NORMALIZATION_MODEL.
BUYING_OPENAI_MODEL = BUYING_NORMALIZATION_MODEL
BUYING_OPENAI_API_KEY = env.str('OPENAI_API_KEY', default='')
BUYING_AI_TEMPERATURE = env.float('BUYING_AI_TEMPERATURE', default=0)
BUYING_AI_TIMEOUT = env.int(
    'BUYING_AI_TIMEOUT',
    default=env.int('BUYING_AI_TIMEOUT_SECONDS', default=45),
)
BUYING_AI_TIMEOUT_SECONDS = BUYING_AI_TIMEOUT  # backwards-compatible alias
BUYING_AI_MAX_RETRIES = env.int('BUYING_AI_MAX_RETRIES', default=2)
BUYING_NBG_CURRENCIES = env.list('BUYING_NBG_CURRENCIES', default=['USD', 'EUR', 'GBP', 'CNY'])
BUYING_NBG_TIMEOUT_SECONDS = env.int('BUYING_NBG_TIMEOUT_SECONDS', default=15)
BUYING_NBG_API_URL = env.str(
    'BUYING_NBG_API_URL',
    default='https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/',
)
BUYING_FX_TIMEZONE = env.str('BUYING_FX_TIMEZONE', default='Asia/Tbilisi')
BUYING_NBG_LIVE_FETCH_ON_MISS = env.bool('BUYING_NBG_LIVE_FETCH_ON_MISS', default=True)
BUYING_CONNECTOR_TIMEOUT_SECONDS = env.int('BUYING_CONNECTOR_TIMEOUT_SECONDS', default=25)
BUYING_CONNECTOR_MAX_RETRIES = env.int('BUYING_CONNECTOR_MAX_RETRIES', default=2)
BUYING_SEARCH_FRESH_HOURS = env.int('BUYING_SEARCH_FRESH_HOURS', default=6)
BUYING_SEARCH_STALE_HOURS = env.int('BUYING_SEARCH_STALE_HOURS', default=48)
BUYING_SEARCH_SYNC_FALLBACK = env.bool('BUYING_SEARCH_SYNC_FALLBACK', default=True)
# When sync fallback is on: False = background thread (avoids Heroku H12 on Force Refresh).
# True = run inline (unit tests / local debugging).
BUYING_SEARCH_SYNC_BLOCKING = env.bool('BUYING_SEARCH_SYNC_BLOCKING', default=False)
BUYING_ITF_TENNIS_POINT_USERNAME = env.str('BUYING_ITF_TENNIS_POINT_USERNAME', default='')
BUYING_ITF_TENNIS_POINT_PASSWORD = env.str('BUYING_ITF_TENNIS_POINT_PASSWORD', default='')
BUYING_CENTRAL_TENNIS_USERNAME = env.str('BUYING_CENTRAL_TENNIS_USERNAME', default='')
BUYING_CENTRAL_TENNIS_PASSWORD = env.str('BUYING_CENTRAL_TENNIS_PASSWORD', default='')
BUYING_AUTH_SESSION_TTL_SECONDS = env.int('BUYING_AUTH_SESSION_TTL_SECONDS', default=86400)

MESSAGE_TAGS = {
    messages.DEBUG: 'secondary',
    messages.INFO: 'info',
    messages.SUCCESS: 'success',
    messages.WARNING: 'warning',
    messages.ERROR: 'danger',
}

# SMTP over SSL: use certifi CA bundle (see tenrivals.smtp_backend.FlexibleSSLEmailBackend).
# If the server still fails TLS verification (e.g. self-signed chain), set
# EMAIL_SMTP_ALLOW_UNVERIFIED_SSL=true — only as a last resort (weakens security).
EMAIL_SMTP_ALLOW_UNVERIFIED_SSL = env.bool(
    "EMAIL_SMTP_ALLOW_UNVERIFIED_SSL", default=False
)
EMAIL_BACKEND = "tenrivals.smtp_backend.FlexibleSSLEmailBackend"

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

    'snapshot-stock-value-daily': {
        'task': 'shop.tasks.snapshot_stock_value',
        'schedule': crontab(hour=23, minute=55),  # end-of-day measured stock value
    },

    # Buying FX: Georgia is UTC+4 — 05:05 UTC ≈ 09:05 Tbilisi (morning),
    # 09:05 UTC ≈ 13:05 Tbilisi (midday retry if today's rate is not yet out).
    'fetch-nbg-rates-morning': {
        'task': 'buying.tasks.fetch_nbg_rates',
        'schedule': crontab(hour=5, minute=5),
    },
    'fetch-nbg-rates-midday': {
        'task': 'buying.tasks.fetch_nbg_rates',
        'schedule': crontab(hour=9, minute=5),
    },
}


SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_AGE = 86400
SESSION_SAVE_EVERY_REQUEST = env.bool('SESSION_SAVE_EVERY_REQUEST', default=False)
_SESSION_SECURE_DEFAULT = not DEBUG
SESSION_COOKIE_SECURE = env.bool(
    'SESSION_COOKIE_SECURE', default=_SESSION_SECURE_DEFAULT
)
CSRF_COOKIE_SECURE = env.bool(
    'CSRF_COOKIE_SECURE', default=_SESSION_SECURE_DEFAULT
)
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

# Public absolute URLs (canonical, email links, sitemap in robots.txt).
_website_url_raw = env.str('WEBSITE_URL', default=f'https://{CANONICAL_HOST}').strip().rstrip('/')
if '://' in _website_url_raw:
    from urllib.parse import urlparse

    _parsed_site = urlparse(_website_url_raw)
    WEBSITE_URL = f'https://{_normalize_site_host(_parsed_site.hostname or CANONICAL_HOST)}'
else:
    WEBSITE_URL = f'https://{_normalize_site_host(_website_url_raw or CANONICAL_HOST)}'

# 301 www → apex on Heroku/production; off locally unless CANONICAL_REDIRECT_WWW=true.
CANONICAL_REDIRECT_WWW = env.bool(
    'CANONICAL_REDIRECT_WWW',
    default=bool(os.environ.get('DYNO')),
)

PASSWORD_RESET_TELEGRAM_CODE_EXPIRY_MINUTES = 15

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)
SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False)
SECURE_HSTS_PRELOAD = env.bool('SECURE_HSTS_PRELOAD', default=False)

_RATELIMIT_REDIS = env.str('RATELIMIT_REDIS_URL', default='').strip()
if _RATELIMIT_REDIS:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': _RATELIMIT_REDIS,
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }

RATELIMIT_VIEW = 'tenrivals.error_views.ratelimit_response'
RATELIMIT_ENABLE = env.bool('RATELIMIT_ENABLE', default=True)
# When Redis (RATELIMIT_REDIS_URL) is misconfigured or unreachable, allow requests instead of 500.
RATELIMIT_FAIL_OPEN_ON_REDIS_DOWN = env.bool(
    'RATELIMIT_FAIL_OPEN_ON_REDIS_DOWN', default=True
)

from tenrivals.ratelimit_cache_patch import install as _install_ratelimit_redis_fail_open

_install_ratelimit_redis_fail_open()

_SENTRY_DSN = env.str('SENTRY_DSN', default='').strip()
if _SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=env.float('SENTRY_TRACES_SAMPLE_RATE', default=0.05),
        # Match Sentry wizard when SENTRY_SEND_DEFAULT_PII=true (headers, IP). Default off for storefront privacy.
        send_default_pii=env.bool('SENTRY_SEND_DEFAULT_PII', default=False),
    )