import fcntl
import hashlib
from contextlib import contextmanager
from functools import wraps
from urllib.parse import urlsplit
from django.conf import settings
from django.contrib.auth import logout
from django.core.exceptions import DisallowedHost
from django.db import connections, transaction
from django.http import HttpResponseForbidden
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import redirect
from django.urls import set_script_prefix
from django.utils import timezone
from .models import RateLimit
from . import rights

@contextmanager
def file_lock(name, exclusive=True, blocking=True):
    with open(settings.DATA_DIR / (name + '.lock'), 'a') as handle:
        flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if not blocking:
            flags |= fcntl.LOCK_NB
        fcntl.flock(handle, flags)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

class TrustingCsrfViewMiddleware(CsrfViewMiddleware):
    """CSRF middleware that tolerates an opaque (``Origin: null``) or absent Origin header.

    ``CsrfViewMiddleware`` rejects any POST whose ``Origin`` is not one of
    ``CSRF_TRUSTED_ORIGINS``. Some browsers set ``Origin`` to the literal string
    ``null`` (opaque origin) on same-origin form POSTs, e.g. when the app is
    reached as ``http://localhost:8098`` in Edge. The request token check and the
    ``SameSite=Strict`` session cookie already provide CSRF protection, so the
    Origin pre-check is only relaxed for missing/opaque origins; genuine
    token-mismatch requests are still rejected by the parent middleware.
    """
    def _origin_verified(self, request):
        origin = request.META.get('HTTP_ORIGIN', '')
        if not origin or origin == 'null':
            try:
                host = request.get_host()
            except DisallowedHost:
                return False
            referer = request.META.get('HTTP_REFERER')
            if referer:
                try:
                    parsed = urlsplit(referer)
                except (ValueError, TypeError):
                    return False
                return parsed.netloc.lower() == host.lower()
            # No Origin and no Referer: rely on the token check and SameSite=Strict cookie.
            return True
        return super()._origin_verified(request)

class MaintenanceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        with file_lock('maintenance', exclusive=False):
            connections.close_all()
            try:
                response = self.get_response(request)
                response['Cache-Control'] = 'no-store'
                response['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
                return response
            finally:
                connections.close_all()

class BasePathMiddleware:
    """Macht die App unter einem Unterpfad (SCRIPT_NAME) erreichbar.

    Django generiert aus ``{% url %}`` und Formular-Aktionen normalerweise Pfade ohne
    Präfix. Diese Middleware stellt sicher, dass alle internen Links (URLs, Formulare,
    Redirects) den SCRIPT_NAME-Präfix tragen, wenn die App hinter einem Reverse-Proxy
    unter einem Unterpfad läuft. Bei Root-Installation (SCRIPT_NAME leer) ist sie ein
    No-op. ``set_script_prefix`` ist thread-lokal und gilt nur für die aktuelle Anfrage.
    """
    def __init__(self, get_response):
        self.get_response = get_response
        self.prefix = settings.SCRIPT_NAME
    def __call__(self, request):
        if self.prefix:
            set_script_prefix(self.prefix)
        return self.get_response(request)

class AccessMiddleware:
    """Ermittelt bei jedem Request die aktuellen Rechte der Person (Entzug wirkt sofort)."""
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        request.is_admin = False
        request.grants = {}
        request.grant_details = {}
        if request.user.is_authenticated:
            request.is_admin = rights.is_admin(request.user)
            if not request.is_admin:
                request.grants = rights.grants_for(request.user)
                request.grant_details = rights.details_for(request.user)
        return self.get_response(request)

def _guard(check):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            if not check(request):
                return HttpResponseForbidden('Diese Aktion ist für dein Konto nicht erlaubt.')
            return view(request, *args, **kwargs)
        return wrapped
    return decorator

# Administratoren
admin_only = _guard(lambda request: request.is_admin)
# Jede angemeldete, aktive Person (z. B. Passwort ändern)
signed_in = _guard(lambda request: True)
# Mindestens ein Bereich sichtbar (oder Administrator)
has_access = _guard(lambda request: request.is_admin or bool(request.grants))
# Mindestens ein Bereich schaltbar (oder Administrator)
can_operate = _guard(lambda request: request.is_admin or 'control' in request.grants.values())

def context(request):
    is_admin = getattr(request, 'is_admin', False)
    grants = getattr(request, 'grants', {})
    return {'is_admin': is_admin, 'has_view': is_admin or bool(grants), 'has_control': is_admin or 'control' in grants.values(), 'instance_name': settings.INSTANCE_NAME}

def limited(request, purpose, limit=10, scope=''):
    # Database-backed: shared by all Gunicorn workers, not a process-local counter.
    key = hashlib.sha256((purpose + ':' + request.META.get('REMOTE_ADDR', '') + ':' + scope).encode()).hexdigest()
    with transaction.atomic():
        item, _ = RateLimit.objects.get_or_create(key=key)
        if (timezone.now() - item.since).total_seconds() > 900:
            item.count, item.since = 0, timezone.now()
        item.count += 1
        item.save()
        return item.count > limit
