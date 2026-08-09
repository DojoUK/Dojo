from pathlib import Path
import os
from django.core.management.utils import get_random_secret_key
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# No SECRET_KEY needs to be configured by hand: a fresh instance generates its
# own on first boot and persists it here so it survives container restarts.
_secret_key_file = BASE_DIR / '.secret_key'
if _secret_key_file.exists():
    SECRET_KEY = _secret_key_file.read_text().strip()
else:
    SECRET_KEY = get_random_secret_key()
    _secret_key_file.write_text(SECRET_KEY)

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

# Host restriction is deliberately permissive out of the box. A browser-
# configurable allow-list is planned as a replacement for setting this via
# the environment; until then, self-hosters wanting to restrict it can still
# set ALLOWED_HOSTS explicitly.
_allowed_hosts = os.environ.get('ALLOWED_HOSTS', '').strip()
ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts.split(',') if h.strip()] if _allowed_hosts else ['*']

SITE_URL = os.environ.get('SITE_URL', 'http://localhost:8000')

# Secure-cookie/HTTPS-redirect settings are inferred from SITE_URL's scheme
# rather than DEBUG. Self-hosted instances are frequently served over plain
# HTTP (no reverse proxy/TLS termination in front) — forcing HTTPS on them
# regardless would redirect every request to a scheme the server can't
# actually serve. Set SITE_URL to an https:// address once you have TLS
# terminated in front of the instance to enable these.
if SITE_URL.startswith('https://'):
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'organisations',
    'members',
    'classes',
    'progression',
    'gradings',
    'billing',
    'documents',
    'inventory',
    'django_htmx',
    'auditlog',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
    'auditlog.middleware.AuditlogMiddleware',
]

ROOT_URLCONF = 'dojo.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'dojo.context_processors.dojo_licence',
                'dojo.context_processors.dojo_debug',
            ],
        },
    },
]

WSGI_APPLICATION = 'dojo.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ['DB_NAME'],
        'USER': os.environ['DB_USER'],
        'PASSWORD': os.environ['DB_PASSWORD'],
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '3306'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-gb'

TIME_ZONE = 'Europe/London'

USE_I18N = True

USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DOJO_LICENCE_KEY = os.environ.get('DOJO_LICENCE_KEY', '')
DOJO_LICENCE_HOLDER = os.environ.get('DOJO_LICENCE_HOLDER', '')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = '/'

# Email — defaults to console (prints to docker logs) if SMTP not configured
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend',
)
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'Dojo <noreply@example.com>')

# Stripe — set these in .env when you have your Stripe account
STRIPE_PUBLIC_KEY = os.environ.get('STRIPE_PUBLIC_KEY', '')
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
