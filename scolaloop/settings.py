# Scooloop settings
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

from django.core.exceptions import ImproperlyConfigured

# Accept explicit DEBUG override for local development without DATABASE_URL.
if os.environ.get('DATABASE_URL') is None and os.environ.get('DEBUG') is None:
    os.environ['DEBUG'] = 'True'

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY') or 'django-insecure-change-me-in-production-abc123xyz'

# Fallback SECRET_KEY for local/test environments without DATABASE_URL or DJANGO_SECRET_KEY.
if os.environ.get('DJANGO_SECRET_KEY') is None:
    os.environ['DJANGO_SECRET_KEY'] = SECRET_KEY

DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'

# Explicit password validation via ORM backend (Django Auth is otherwise silent here).
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'routing',
]

STORAGES = {
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.StaticFilesStorage',
    },
}

# Use DATABASE_URL if present, else SQLite sqlite3 (local dev only).
if os.environ.get('DATABASE_URL'):
    import dj_database_url
    DATABASES = {
        'default': dj_database_url.config(
            default=os.environ.get('DATABASE_URL'),
            conn_max_age=600,
        ),
    }
else:
    if not DEBUG:
        raise ImproperlyConfigured(
            "DATABASE_URL is required in production. "
            "Set DATABASE_URL to a Postgres connection string (Render, Heroku, etc.). "
            "In local development, set DEBUG=True or provide DATABASE_URL."
        )
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# URL vérification de santé : connexion DB sans template.
from django.http import JsonResponse
from django.db import connection
from django.views.decorators.http import require_GET


@require_GET
def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        return JsonResponse({'status': 'ok'})
    except Exception as exc:
        return JsonResponse(
            {'status': 'error', 'reason': str(exc)},
            status=503,
        )

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'scolaloop.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

AUTHENTICATION_BACKENDS = [
    'routing.backends.MatriculeBackend',
    'django.contrib.auth.backends.ModelBackend',
]

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'

WSGI_APPLICATION = 'scolaloop.wsgi.application'

LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# LOGGING
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} - {name} - {levelname} - {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} - {name} - {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'routing': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}

# ---------------------------------------------------------------- SEO / Référencement Google
SITE_NAME = os.environ.get('SITE_NAME', 'Scolaloop')
SITE_URL = os.environ.get('SITE_URL', 'https://scolaloop.onrender.com')
SITE_DESCRIPTION = (
    'Scolaloop — Système intelligent de transport scolaire : optimisation des tournées, '
    'suivi GPS en temps réel, communication parents-école et sécurité routière. '
    'Développé par ESTECH.'
)
SITE_KEYWORDS = (
    'transport scolaire, bus scolaire, tournées bus, suivi GPS, '
    'optimisation tournées, application scolaire, ESTECH, Scolaloop'
)
GOOGLE_SITE_VERIFICATION = os.environ.get('GOOGLE_SITE_VERIFICATION', '')
