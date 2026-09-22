"""
Django settings for the Event Registration System.

Configuration that differs between machines (secret key, debug flag, allowed
hosts, time zone) is read from environment variables / a local `.env` file.
See `.env.example`.
"""
import os
import sys
from pathlib import Path

from django.core.management.utils import get_random_secret_key
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load variables from .env (if present). Real environment variables win.
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------
# Core / security
# --------------------------------------------------------------------------
DEBUG = env_bool("DEBUG", False)
TESTING = "test" in sys.argv

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or TESTING:
        # Ephemeral key: fine for local development and tests, never for production.
        SECRET_KEY = get_random_secret_key()
    else:
        # SystemExit (rather than ImproperlyConfigured) so a first-time user sees this
        # message instead of Django's confusing "Models aren't loaded yet" error.
        raise SystemExit(
            "\nERROR: SECRET_KEY is not set.\n"
            "Copy .env.example to .env and set SECRET_KEY (the README shows how to generate one)."
        )

if not (DEBUG or TESTING) and SECRET_KEY.startswith("change-me"):
    raise SystemExit("\nERROR: SECRET_KEY is still the placeholder from .env.example. Generate a real one.")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "rest_framework.authtoken",
    # Local
    "accounts",
    "events",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --------------------------------------------------------------------------
# Database (SQLite)
# --------------------------------------------------------------------------
# transaction_mode=IMMEDIATE makes every atomic() block take SQLite's write
# lock up-front. That serialises "check capacity -> insert registration" so two
# requests cannot both grab the last seat. `timeout` is how long a request waits
# for the lock (seconds) before failing with a safe 503. See README.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "timeout": 20,
        },
        # A file-based test database (instead of the default in-memory one) is
        # required so the multi-threaded concurrency test uses real SQLite locking.
        # It is created and destroyed automatically by `manage.py test`.
        "TEST": {"NAME": BASE_DIR / "test_db.sqlite3"},
    }
}

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

if TESTING:
    # Fast (insecure) hasher, used ONLY while running the test suite.
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# --------------------------------------------------------------------------
# Internationalisation / time zones
# --------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
# All datetimes are stored in UTC (USE_TZ=True). TIME_ZONE controls how they are
# displayed in the Admin and rendered in API responses. Default: UTC.
TIME_ZONE = os.getenv("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Django REST Framework
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    # Token authentication: `Authorization: Token <key>`
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
    ],
    # Secure by default: every endpoint requires login unless it opts out.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "config.api.exception_handler",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}
