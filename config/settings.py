import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv('DATA_DIR', '/data'))
BACKUP_DIR = Path(os.getenv('BACKUP_DIR', '/backups'))
DATA_DIR.mkdir(parents=True, exist_ok=True)
def secret(name, default=''):
    path = os.getenv(name + '_FILE')
    return Path(path).read_text().strip() if path else os.getenv(name, default)
SECRET_KEY = secret('SECRET_KEY')
if not SECRET_KEY:
    raise RuntimeError('SECRET_KEY_FILE fehlt')
DEBUG = False
ALLOWED_HOSTS = [h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',') if h.strip()]
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'heating']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'whitenoise.middleware.WhiteNoiseMiddleware', 'heating.security.MaintenanceMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'heating.security.BasePathMiddleware', 'django.middleware.common.CommonMiddleware', 'heating.security.TrustingCsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware', 'heating.security.AccessMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages', 'heating.security.context']}}]
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': DATA_DIR / 'eta.sqlite3', 'OPTIONS': {'timeout': 30, 'transaction_mode': 'IMMEDIATE'}}}
DATABASES['default']['TEST'] = {'NAME': '/tmp/eta-test-suite.sqlite3'}
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}}, {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'}, {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'}]
LANGUAGE_CODE = 'de-at'
TIME_ZONE = 'Europe/Vienna'
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
WHITENOISE_USE_FINDERS = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
SESSION_COOKIE_NAME = 'eta_session'
CSRF_COOKIE_NAME = 'eta_csrf'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Strict'
SESSION_COOKIE_AGE = 28800
SESSION_COOKIE_SECURE = os.getenv('COOKIE_SECURE') == '1'
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_REFERRER_POLICY = 'no-referrer'
X_FRAME_OPTIONS = 'DENY'
PUBLIC_URL = os.getenv('PUBLIC_URL', 'http://localhost:8098').rstrip('/')
INSTANCE_NAME = os.getenv('INSTANCE_NAME', '')
ETA_URL = os.getenv('ETA_URL', 'http://192.168.178.22:8080').rstrip('/')
POLL_SECONDS = max(10, int(os.getenv('POLL_SECONDS', '30')))
ARCHIVE_ENABLED = os.getenv('ARCHIVE_ENABLED', '1') == '1'
ARCHIVE_AFTER_DAYS = max(30, int(os.getenv('ARCHIVE_AFTER_DAYS', '30')))
# MQTT-Brücke (eigener Dienst mqtt_bridge). Leerer Host = ausgeschaltet.
MQTT_HOST = os.getenv('MQTT_HOST', '')
MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))
MQTT_USER = os.getenv('MQTT_USER', 'eta-app')
MQTT_PASSWORD = secret('MQTT_PASSWORD')
MQTT_COMMAND_MAX_AGE = int(os.getenv('MQTT_COMMAND_MAX_AGE', '120'))
# Alle Heizungsdaten auf eta/state/<punkt>, eta/status und eta/errors veröffentlichen (retained).
MQTT_PUBLISH_ALL = os.getenv('MQTT_PUBLISH_ALL', '1') == '1'
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_USER', '')
EMAIL_HOST_PASSWORD = secret('EMAIL_PASSWORD')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', '1') == '1'
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.getenv('EMAIL_FROM', 'heizung@example.invalid')
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
PASSWORD_RESET_TIMEOUT = 3600
CSRF_FAILURE_VIEW = 'heating.views.csrf_failure'
CSRF_TRUSTED_ORIGINS = [PUBLIC_URL]
for _extra in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(','):
    _extra = _extra.strip().rstrip('/')
    if _extra and _extra not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_extra)
# ---- Basispfad hinter einem Reverse-Proxy (Traefik) ----
# SCRIPT_NAME=/eta macht die App unter einem Unterpfad erreichbar. Traefik leitet den
# öffentlichen Pfad hierfür UNVERÄNDERT weiter (kein stripPrefix) und setzt dabei den
# X-Forwarded-Proto-Header auf https; die App erzeugt alle internen Links selbst mit
# dem SCRIPT_NAME-Präfix. Leer/unset = Root-Installation (lokale Instanz unverändert).
SCRIPT_NAME = os.getenv('SCRIPT_NAME', '').strip().rstrip('/')
if SCRIPT_NAME:
    FORCE_SCRIPT_NAME = SCRIPT_NAME
    STATIC_URL = SCRIPT_NAME + '/static/'
    LOGIN_URL = SCRIPT_NAME + '/login/'
    LOGIN_REDIRECT_URL = SCRIPT_NAME + '/'
    LOGOUT_REDIRECT_URL = SCRIPT_NAME + '/login/'
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    _public_host = os.getenv('PUBLIC_HOST', '')
    if not _public_host and PUBLIC_URL:
        _netloc = PUBLIC_URL.split('://')[-1].split('/')[0]
        if ':' in _netloc:
            _netloc = _netloc.split(':')[0]
        _public_host = _netloc
    _https_origin = 'https://' + _public_host
    if _public_host and _https_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_https_origin)
    if _public_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_public_host)
LOGGING = {'version': 1, 'disable_existing_loggers': False, 'handlers': {'console': {'class': 'logging.StreamHandler'}}, 'loggers': {'django.security.csrf': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False}, 'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False}}}
