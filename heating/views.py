import csv
import json
from .archives import iter_samples, storage_stats
from .graph_samples import graph_samples
import hashlib
import io
import math
import re
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, SetPasswordForm
from django.contrib.auth.models import User, Group
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpResponse, JsonResponse, FileResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST, require_GET
from .models import Point, Sample, State, Audit, Command, AccessToken, Job, ChangeEvent, Grant, ControlDetail
from .security import admin_only, signed_in, has_access, can_operate, limited, file_lock
from . import rights
from .services import get_state, set_state, audit
from .eta import EtaClient, EtaError
from . import backups

WIESENEGGER_DAY_KEYS = [
    ('mon', 'Montag'),
    ('tue', 'Dienstag'),
    ('wed', 'Mittwoch'),
    ('thu', 'Donnerstag'),
    ('fri', 'Freitag'),
    ('sat', 'Samstag'),
    ('sun', 'Sonntag'),
]
WIESENEGGER_TIME_KEYS = {
    'mon': ('wiese-time-mon', 'wiese-time-mon-1', 'wiese-time-mon-2', 'wiese-time-mon-3'),
    'tue': ('wiese-time-tue', 'wiese-time-tue-1', 'wiese-time-tue-2', 'wiese-time-tue-3'),
    'wed': ('wiese-time-wed', 'wiese-time-wed-1', 'wiese-time-wed-2', 'wiese-time-wed-3'),
    'thu': ('wiese-time-thu', 'wiese-time-thu-1', 'wiese-time-thu-2', 'wiese-time-thu-3'),
    'fri': ('wiese-time-fri', 'wiese-time-fri-1', 'wiese-time-fri-2', 'wiese-time-fri-3'),
    'sat': ('wiese-time-sat', 'wiese-time-sat-1', 'wiese-time-sat-2', 'wiese-time-sat-3'),
    'sun': ('wiese-time-sun', 'wiese-time-sun-1', 'wiese-time-sun-2', 'wiese-time-sun-3'),
}
WIESENEGGER_LIMITED_KEYS = {
    'wiese-switch',
    'wiese-time-state', 'wiese-time-temp',
    'wiese-time-mon', 'wiese-time-mon-1', 'wiese-time-mon-2', 'wiese-time-mon-3',
    'wiese-time-tue', 'wiese-time-tue-1', 'wiese-time-tue-2', 'wiese-time-tue-3',
    'wiese-time-wed', 'wiese-time-wed-1', 'wiese-time-wed-2', 'wiese-time-wed-3',
    'wiese-time-thu', 'wiese-time-thu-1', 'wiese-time-thu-2', 'wiese-time-thu-3',
    'wiese-time-fri', 'wiese-time-fri-1', 'wiese-time-fri-2', 'wiese-time-fri-3',
    'wiese-time-sat', 'wiese-time-sat-1', 'wiese-time-sat-2', 'wiese-time-sat-3',
    'wiese-time-sun', 'wiese-time-sun-1', 'wiese-time-sun-2', 'wiese-time-sun-3',
}
WIESENEGGER_PROFILE_LIMITED = 'switch_schedule'
WIESENEGGER_PROFILE_FULL = 'full'
WIESENEGGER_MODE_KEY = 'wiesenegger-control'
WIESENEGGER_RELEASE_KEY = 'wiesenegger-esp-release'
WIESENEGGER_RELEASE_MAX_AGE_SECONDS = 120
DAY_SHORT = {'mon': 'Mo', 'tue': 'Di', 'wed': 'Mi', 'thu': 'Do', 'fri': 'Fr', 'sat': 'Sa', 'sun': 'So'}
SLOT_RE = re.compile(r'^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$')
TIME_RE = re.compile(r'^(\d{2}):(\d{2})$')

def tick_text(tick):
    """Viertelstunden seit Mitternacht -> 'HH:MM' (96 -> '24:00')."""
    return '%02d:%02d' % divmod(tick * 15, 60)

def slot_ticks(text):
    """'06:00 - 09:30' (ETA-strValue) -> (24, 38). None, wenn kein gültiges Zeitfenster."""
    match = SLOT_RE.match(text or '')
    if not match:
        return None
    h1, m1, h2, m2 = map(int, match.groups())
    if m1 % 15 or m2 % 15 or m1 > 45 or m2 > 45:
        return None
    begin, end = h1 * 4 + m1 // 15, h2 * 4 + m2 // 15
    return (begin, end) if 0 <= begin <= end <= 96 else None

def time_ticks(value, is_end=False):
    """Eingabe 'HH:MM' -> Viertelstunden; als Ende bedeutet 00:00 Mitternacht (24:00)."""
    match = TIME_RE.match(value or '')
    if not match:
        raise ValueError('Uhrzeit fehlt oder ist ungültig')
    hours, minutes = map(int, match.groups())
    if hours > 24 or minutes > 59 or minutes % 15 or (hours == 24 and minutes):
        raise ValueError('Uhrzeiten nur im Viertelstundenraster (z. B. 06:15)')
    tick = hours * 4 + minutes // 15
    return 96 if is_end and tick == 0 else tick

def point_fresh(point):
    return point.latest.get('quality') == 'ok' and point.updated is not None and (timezone.now() - point.updated).total_seconds() <= 2.5 * settings.POLL_SECONDS

def wiesenegger_control_profile(request):
    if request.is_admin:
        return WIESENEGGER_PROFILE_FULL
    profile = getattr(request, 'grant_details', {}).get('Wiesenegger', WIESENEGGER_PROFILE_FULL)
    return profile if profile in (WIESENEGGER_PROFILE_LIMITED, WIESENEGGER_PROFILE_FULL) else WIESENEGGER_PROFILE_FULL

def control_allowed(request, point):
    if request.is_admin:
        return True
    if not rights.can_control(request, point.section):
        return False
    if point.section == 'Wiesenegger' and wiesenegger_control_profile(request) == WIESENEGGER_PROFILE_LIMITED:
        return point.key in WIESENEGGER_LIMITED_KEYS
    return True

def wiesenegger_mode():
    mode = get_state(WIESENEGGER_MODE_KEY, {'mode': 'permanent'}).get('mode')
    return mode if mode in ('permanent', 'esp_release') else 'permanent'

def wiesenegger_release_status():
    release = get_state(WIESENEGGER_RELEASE_KEY, {'enabled': False, 'at': None})
    at_raw = release.get('at')
    at = parse_datetime(at_raw) if isinstance(at_raw, str) and at_raw else None
    fresh = bool(at and timezone.is_aware(at) and (timezone.now() - at).total_seconds() <= WIESENEGGER_RELEASE_MAX_AGE_SECONDS)
    enabled = bool(release.get('enabled'))
    return {'enabled': enabled, 'fresh': fresh, 'at': at_raw}

def wiesenegger_controls_active():
    mode = wiesenegger_mode()
    if mode == 'permanent':
        return True, mode, wiesenegger_release_status()
    status = wiesenegger_release_status()
    return status['enabled'] and status['fresh'], mode, status

def csrf_failure(request, reason=''):
    import logging
    logging.getLogger('django.security.csrf').warning('CSRF: %s', reason)
    return render(request, 'auth.html', {'heading':'Formular bitte neu laden', 'subtitle':'Die Sitzung oder das Sicherheitsmerkmal des Formulars ist nicht mehr gültig. Öffne die Anmeldung erneut.'}, status=403)

def health(request):
    return JsonResponse({'status': 'ok'})

def sign_in(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST':
        if limited(request, 'login'):
            form.add_error(None, 'Zu viele Versuche. Bitte in 15 Minuten erneut versuchen.')
        elif form.is_valid():
            login(request, form.get_user())
            return redirect('home')
    return render(request, 'auth.html', {'form': form, 'heading': 'Willkommen zurück', 'subtitle': 'Melde dich an, um deine Heizung im Blick zu behalten.', 'button': 'Anmelden', 'is_login': True})

@require_POST
def sign_out(request):
    logout(request)
    return redirect('login')

def home(request):
    if not request.user.is_authenticated:
        return redirect('login')
    if request.is_admin:
        return redirect('admin_home')
    if 'control' in request.grants.values():
        return redirect('control')
    if request.grants:
        return redirect('dashboard')
    return render(request, 'roles.html')

@signed_in
def password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            form.save()
            AccessToken.objects.filter(user=request.user).update(used=True)
            audit(request, 'password-change', {})
        logout(request)
        messages.success(request, 'Passwort geändert. Bitte neu anmelden.')
        return redirect('login')
    return render(request, 'auth.html', {'form': form, 'heading': 'Passwort ändern', 'button': 'Passwort speichern'})

def issue_token(user, purpose):
    token = secrets.token_urlsafe(32)
    AccessToken.objects.create(digest=hashlib.sha256(token.encode()).hexdigest(), user=user, purpose=purpose, expires=timezone.now()+timedelta(hours=1 if purpose == 'reset' else 48))
    return settings.PUBLIC_URL + '/access/' + token + '/'

def password_reset(request):
    if request.method == 'POST':
        if not limited(request, 'reset', 5):
            user = User.objects.filter(email__iexact=request.POST.get('email', '').strip(), is_active=True).first()
            if user and settings.EMAIL_HOST:
                link = issue_token(user, 'reset')
                try:
                    send_mail('ETA · Passwort zurücksetzen', 'Der Link ist eine Stunde gültig:\n' + link, settings.DEFAULT_FROM_EMAIL, [user.email])
                    set_state('mail', {'status': 'An SMTP übergeben', 'at': timezone.now().isoformat()})
                except Exception:
                    set_state('mail', {'status': 'SMTP-Versand fehlgeschlagen', 'at': timezone.now().isoformat()})
        return render(request, 'auth.html', {'heading': 'Anfrage entgegengenommen', 'subtitle': 'Wenn ein passender Zugang und E-Mail-Versand eingerichtet sind, erhältst du einen Link.'})
    return render(request, 'auth.html', {'heading': 'Passwort vergessen?', 'reset': True, 'button': 'Wiederherstellung anfordern'})

def access(request, token):
    digest = hashlib.sha256(token.encode()).hexdigest()
    item = AccessToken.objects.filter(pk=digest, used=False, expires__gt=timezone.now(), user__is_active=True).select_related('user').first()
    if not item:
        return render(request, 'auth.html', {'heading': 'Link ungültig oder abgelaufen', 'subtitle': 'Bitte einen neuen Zugang anfordern.'}, status=400)
    form = SetPasswordForm(item.user, request.POST or None)
    if request.method == 'POST':
        if limited(request, 'token', 10):
            return HttpResponse('Zu viele Versuche', status=429)
        if form.is_valid():
            with transaction.atomic():
                if AccessToken.objects.filter(pk=digest, used=False, expires__gt=timezone.now()).update(used=True) != 1:
                    return HttpResponseBadRequest('Link wurde bereits verwendet')
                form.save()
                AccessToken.objects.filter(user=item.user).update(used=True)
                Audit.objects.create(actor=item.user.username, role='access', action=item.purpose, detail={})
            logout(request)
            messages.success(request, 'Passwort gespeichert. Du kannst dich anmelden.')
            return redirect('login')
    return render(request, 'auth.html', {'heading': 'Zugang einrichten' if item.purpose == 'invite' else 'Neues Passwort', 'form': form, 'button': 'Passwort speichern'})

def visible_points(request):
    return Point.objects.all() if request.is_admin else Point.objects.filter(section__in=rights.visible_sections(request))

def default_series(request):
    """Voreingestellte Messgröße im Verlauf: Kesselleistung, sonst der erste sichtbare Messwert."""
    if rights.can_view(request, 'Kessel'):
        return 'power'
    first = visible_points(request).filter(control=False).first()
    return first.key if first else ''

@has_access
def dashboard(request):
    points = list(visible_points(request).filter(control=False, enabled=True))
    preference = get_state('dashboard-user-%s' % request.user.pk)
    saved = preference.get('points')
    allowed = {p.key for p in points}
    selected = [key for key in saved if key in allowed] if isinstance(saved, list) else [default_series(request)]
    if saved and not selected:
        selected = [default_series(request)]
    for point in points:
        point.chart_selected = point.key in selected
    hours = preference.get('hours', 24 if rights.can_view(request, 'Kessel') else 1)
    if hours not in (1, 24, 168, 720):
        hours = 24 if rights.can_view(request, 'Kessel') else 1
    return render(request, 'dashboard.html', {'points': points, 'chart_hours': hours, 'logger': get_state('logger'), 'default_series': default_series(request), 'show_boiler': rights.can_view(request, 'Kessel')})


@has_access
@require_POST
def dashboard_preferences(request):
    try:
        data = json.loads(request.body)
        keys, hours = data['points'], data['hours']
        if not isinstance(keys, list) or len(keys) > 100 or any(not isinstance(key, str) for key in keys) or type(hours) is not int or hours not in (1, 24, 168, 720):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        return JsonResponse({'error': 'Ungültige Diagrammeinstellung.'}, status=400)
    allowed = set(visible_points(request).filter(control=False, enabled=True).values_list('key', flat=True))
    if any(key not in allowed for key in keys):
        return JsonResponse({'error': 'Für einen Datenpunkt fehlt die Berechtigung.'}, status=403)
    set_state('dashboard-user-%s' % request.user.pk, {'points': list(dict.fromkeys(keys)), 'hours': hours})
    return JsonResponse({'saved': True})

@admin_only
def admin_home(request):
    storage = storage_stats()
    return render(request, 'admin_home.html', {'logger': get_state('logger'), 'worker': get_state('worker'), 'backup': get_state('backup-last'), 'people': User.objects.count(), 'samples': storage['total'], 'storage': storage, 'commands': Command.objects.count(), 'events': Audit.objects.all()[:8], 'smtp': bool(settings.EMAIL_HOST), 'mail': get_state('mail')})

@has_access
@require_GET
def live(request):
    now = timezone.now()
    data = []
    for p in visible_points(request):
        if p.metadata.get('derived'):
            continue
        data.append({'key': p.key, 'label': p.label, 'section': p.section, 'latest': p.latest, 'at': p.updated.isoformat() if p.updated else None, 'stale': p.updated is None or (now-p.updated).total_seconds() > settings.POLL_SECONDS*3, 'control': p.control})
    worker = get_state('worker')
    worker['stale'] = not worker.get('at') or (now-parse_datetime(worker['at'])).total_seconds() > 120
    return JsonResponse({'points': data, 'logger': get_state('logger'), 'worker': worker, 'errors': get_state('errors') if rights.can_view(request, 'Kessel') else {}, 'now': now.isoformat()})

def period(request):
    end = timezone.now()
    start = end-timedelta(hours=24)
    try:
        if request.GET.get('start'):
            start = parse_datetime(request.GET['start'])
            end = parse_datetime(request.GET.get('end', '')) or end
            if start is None or timezone.is_naive(start) or timezone.is_naive(end):
                raise ValueError
        else:
            start = end-timedelta(hours=int(request.GET.get('hours', 24)))
        if not 0 < (end-start).total_seconds() <= 31*86400 or end > timezone.now()+timedelta(minutes=1):
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise ValueError('Zeitraum muss zwischen 1 Minute und 31 Tagen liegen und eine Zeitzone enthalten')
    return start, end

NO_STATS = {'energy': None, 'average': None, 'utilization': None, 'runtime': None, 'runtime_share': None, 'coverage': None, 'starts': None, 'counter_reset': False}

def power_stats(start, end):
    point = Point.objects.filter(key='power').first()
    rows = [{'at': r.at, 'value': r.value, 'quality': r.quality} for r in iter_samples(point, start, end)] if point else []
    energy, covered, running = 0., 0., 0.
    for left, right in zip(rows, rows[1:]):
        dt = (right['at']-left['at']).total_seconds()
        if left['quality'] != 'ok' or right['quality'] != 'ok' or left['value'] is None or right['value'] is None or dt > settings.POLL_SECONDS*2.5 or dt <= 0:
            continue
        energy += (left['value']+right['value'])/2*dt/3600
        covered += dt
        running += dt if left['value'] > 0 else 0
    counter = Point.objects.filter(key='starts').first()
    starts = [r.value for r in iter_samples(counter, start, end) if r.quality == 'ok'] if counter else []
    reset = any(b < a for a,b in zip(starts, starts[1:]) if a is not None and b is not None)
    return {'energy': round(energy, 2) if covered else None, 'average': round(energy*3600/covered, 2) if covered else None, 'utilization': round(energy*3600/covered/50*100, 1) if covered else None, 'runtime': round(running/3600, 2) if covered else None, 'runtime_share': round(running/covered*100,1) if covered else None, 'coverage': round(covered/(end-start).total_seconds()*100, 1), 'starts': starts[-1]-starts[0] if len(starts)>1 and not reset else None, 'counter_reset': reset}

@has_access
@require_GET
def history(request):
    try:
        start,end = period(request)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    keys = [k.strip() for k in request.GET.get('point', '').split(',') if k.strip()] or [default_series(request)]
    points = {p.key: p for p in Point.objects.filter(key__in=keys)}
    missing = [k for k in keys if k not in points]
    if missing:
        return JsonResponse({'error': 'Unbekannter Datenpunkt: ' + ', '.join(missing)}, status=400)
    if any(not rights.can_view(request, p.section) for p in points.values()):
        return JsonResponse({'error': 'Für diesen Datenpunkt fehlt die Berechtigung.'}, status=403)
    span = (end-start).total_seconds()/600
    series = []
    for key in keys:
        point = points[key]
        rows = graph_samples(point, start, end)
        buckets = {}
        previous = None
        for sample in rows:
            index = min(599, int((sample.at-start).total_seconds()/span))
            bucket = buckets.setdefault(index, {'sum': 0., 'count': 0, 'bad': False})
            if sample.quality != 'ok' or sample.value is None or (previous and (sample.at-previous).total_seconds() > settings.POLL_SECONDS*2.5):
                bucket['bad'] = True
                bucket['explicit_gap'] = True
            else:
                bucket['sum'] += sample.value
                bucket['count'] += 1
            previous = sample.at
        values = [{'at': (start+timedelta(seconds=(i+.5)*span)).isoformat(), 'value': round(b['sum']/b['count'],3) if b['count'] and not b['bad'] else None, 'gap': b.get('explicit_gap', False)} for i in range(600) for b in [buckets.get(i, {'sum':0,'count':0,'bad':True})]]
        series.append({'key': key, 'label': point.label, 'unit': point.latest.get('unit',''), 'values': values})
    return JsonResponse({'series': series, 'sample_interval': settings.POLL_SECONDS, 'stats': power_stats(start,end) if rights.can_view(request, 'Kessel') else NO_STATS, 'start': start.isoformat(), 'end': end.isoformat()})

@has_access
@require_GET
def export(request):
    try:
        start,end = period(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    keys = [k.strip() for k in request.GET.get('point', '').split(',') if k.strip()] or [default_series(request)]
    points = Point.objects.filter(key__in=keys).order_by('id')
    missing = [k for k in keys if not points.filter(key=k).exists()]
    if missing:
        return HttpResponseBadRequest('Unbekannter Datenpunkt: ' + ', '.join(missing))
    if any(not rights.can_view(request, p.section) for p in points):
        return HttpResponseForbidden('Für diesen Datenpunkt fehlt die Berechtigung.')
    if any(p.metadata.get('derived') for p in points):
        return HttpResponseBadRequest('Temperaturdifferenzen werden nur für das Diagramm berechnet. Bitte die gemessenen Temperaturen exportieren.')
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Zeit UTC','Datenpunkt','Wert','Einheit','Qualität','Rohwert'])
    for point in points:
        for s in iter_samples(point, start, end):
            writer.writerow([s.at.isoformat(),point.key,s.value if s.value is not None else '',s.unit,s.quality,s.raw])
    return HttpResponse('\ufeff'+output.getvalue(), content_type='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="eta_export.csv"'})

@can_operate
def control(request):
    sections = rights.controllable_sections(request)
    points = list(Point.objects.filter(control=True) if request.is_admin else Point.objects.filter(control=True, section__in=sections))
    points = [p for p in points if control_allowed(request, p)]
    wiese_limited_profile = rights.can_control(request, 'Wiesenegger') and wiesenegger_control_profile(request) == WIESENEGGER_PROFILE_LIMITED
    wiese_active, wiese_mode, wiese_release = wiesenegger_controls_active()
    for p in points:
        meta = p.metadata
        p.wiese_blocked = wiese_limited_profile and p.section == 'Wiesenegger' and not wiese_active
        p.minimum = p.maximum = None
        if meta.get('min') is not None:
            try:
                p.minimum = Decimal(str(meta['min']).strip())/Decimal(str(meta['scale']).strip())
                p.maximum = Decimal(str(meta['max']).strip())/Decimal(str(meta['scale']).strip())
            except (InvalidOperation, ZeroDivisionError, KeyError, TypeError):
                pass
        p.current_input = p.latest.get('value', '')
        choices = meta.get('choices', [])
        labels = {c['label'].strip().casefold(): c['raw'] for c in choices}
        p.is_switch = len(choices) == 2 and set(labels) == {'ein', 'aus'}
        p.is_timeslot = meta.get('type') == 'TIMESLOT'
        if p.is_timeslot:
            p.current_input = p.latest.get('text', p.latest.get('raw', ''))
        if p.is_switch:
            raw = p.latest.get('raw')
            p.switch_known = (p.latest.get('quality') == 'ok' and raw in labels.values()
                              and p.updated is not None and
                              (timezone.now() - p.updated).total_seconds() <= 2.5 * settings.POLL_SECONDS)
            p.switch_on = raw == labels['ein']
            p.switch_target = labels['aus'] if p.switch_on else labels['ein']
    order = rights.SECTIONS
    groups = []
    schedule_points = {}
    for section in order:
        items = [p for p in points if p.section == section]
        if section == 'Wiesenegger':
            for item in items:
                schedule_points[item.key] = item
            items = [p for p in items if not p.key.startswith('wiese-time-')]
        if items:
            groups.append((rights.label(section), items))
    for p in points:
        if p.section not in order:
            groups.append((p.section, [p]))
    commands = Command.objects.select_related('point','user').order_by('-created')
    if not request.is_admin:
        commands = commands.filter(user=request.user)
    control_enabled = get_state('controls').get('enabled', False)
    wiese_schedule = []
    for day_key, day_label in WIESENEGGER_DAY_KEYS:
        key_outside, *slot_keys = WIESENEGGER_TIME_KEYS[day_key]
        slots = []
        for number, key in enumerate(slot_keys, 1):
            p = schedule_points.get(key)
            if not p:
                continue
            ticks = slot_ticks(p.latest.get('text', '')) if point_fresh(p) else None
            if ticks and ticks[0] == ticks[1]:
                ticks = (0, 0)
            active = bool(ticks and ticks[1] > ticks[0])
            slots.append({
                'point': p, 'key': p.key, 'number': number, 'known': ticks is not None, 'active': active,
                'orig': '%d-%d' % ticks if ticks else '',
                'begin': tick_text(ticks[0]) if active else '',
                'end': (tick_text(ticks[1]) if ticks[1] < 96 else '00:00') if active else '',
                'editable': bool(ticks is not None and control_enabled and p.metadata.get('writable') and not p.wiese_blocked),
            })
        if not slots:
            continue
        outside = schedule_points.get(key_outside)
        if outside:
            outside.known = point_fresh(outside) and outside.latest.get('value') is not None
            outside.editable = bool(outside.known and control_enabled and outside.metadata.get('writable') and not outside.wiese_blocked)
        wiese_schedule.append({'key': day_key, 'day': day_label, 'short': DAY_SHORT[day_key], 'outside': outside, 'slots': slots})
    wiese_time_state = schedule_points.get('wiese-time-state') or Point.objects.filter(key='wiese-time-state').first()
    return render(request, 'control.html', {
        'groups': groups,
        'control_enabled': control_enabled,
        'commands': commands[:15],
        'wiese_time_state': wiese_time_state if wiese_schedule else None,
        'wiese_schedule_editable': any(s['editable'] for row in wiese_schedule for s in row['slots']),
        'wiese_limited_profile': wiese_limited_profile,
        'wiese_profile': wiesenegger_control_profile(request),
        'wiese_mode': wiese_mode,
        'wiese_active': wiese_active,
        'wiese_release': wiese_release,
        'wiese_schedule': wiese_schedule,
    })

@admin_only
@require_POST
def control_toggle(request):
    if limited(request, 'sensitive', 20):
        return HttpResponse('Zu viele Versuche', status=429)
    if not request.user.check_password(request.POST.get('password', '')):
        messages.error(request, 'Passwort nicht bestätigt.')
    else:
        enabled = request.POST.get('enabled') == '1'
        set_state('controls', {'enabled': enabled})
        audit(request, 'control-access', {'enabled': enabled})
        messages.success(request, 'Heizungssteuerung freigegeben.' if enabled else 'Heizungssteuerung gesperrt.')
    return redirect('control')

@admin_only
@require_POST
def control_wiesenegger_mode(request):
    if limited(request, 'sensitive', 20):
        return HttpResponse('Zu viele Versuche', status=429)
    if not request.user.check_password(request.POST.get('password', '')):
        messages.error(request, 'Passwort nicht bestätigt.')
        return redirect('control')
    mode = request.POST.get('mode')
    if mode not in ('permanent', 'esp_release'):
        messages.error(request, 'Ungültiger Wiesenegger-Steuermodus.')
        return redirect('control')
    set_state(WIESENEGGER_MODE_KEY, {'mode': mode})
    audit(request, 'wiesenegger-control-mode', {'mode': mode})
    messages.success(request, 'Wiesenegger-Steuermodus gespeichert.')
    return redirect('control')

@admin_only
@require_POST
def control_confirm_setting(request, pk):
    point = get_object_or_404(Point, pk=pk, control=True)
    point.confirm = request.POST.get('confirm') == 'on'
    point.mqtt = request.POST.get('mqtt') == 'on'
    point.save(update_fields=['confirm', 'mqtt'])
    audit(request, 'control-confirm-setting', {'point': point.key, 'confirm': point.confirm, 'mqtt': point.mqtt})
    messages.success(request, '„' + point.label + '“: Rückfrage ' + ('ein' if point.confirm else 'aus') + ', über MQTT ' + ('schaltbar.' if point.mqtt else 'nicht schaltbar.'))
    return redirect('control')

def prepare_write(client, point, value):
    """Liest varinfo und Ist-Wert und prüft den Sollwert. Liefert (info, before, raw, ticks); EtaError bei ungültigem Wert."""
    info, before = client.info(point.uri), client.read(point.uri)
    ticks = None
    if info.get('type') == 'TIMESLOT':
        ticks = slot_ticks(value)
        if not info.get('writable') or ticks is None:
            raise EtaError('Zeitfenster bitte als HH:MM - HH:MM im Viertelstundenraster angeben')
        raw = '%d-%d' % ticks
    else:
        raw = client.validate(info, value)
    return info, before, raw, ticks

def perform_write(client, point, cmd, before, raw, ticks):
    """Sendet einen geprüften Befehl, liest zurück und speichert das Ergebnis im Command. Kein automatischer Neuversuch."""
    try:
        if ticks:
            client.write_timeslot(point.uri, before['raw'], *ticks)
            actual = client.read(point.uri)
            same = slot_ticks(actual.get('text', '')) == ticks
        else:
            client.write(point.uri, raw)
            actual = client.read(point.uri)
            try:
                same = Decimal(actual['raw']) == Decimal(raw)
            except (InvalidOperation, TypeError):
                same = str(actual.get('raw', '')).strip() == str(raw).strip()
        Point.objects.filter(pk=point.pk).update(latest={**point.latest, **actual}, updated=timezone.now())
        cmd.status = 'confirmed' if same else 'different'
        cmd.result = 'Rückgelesen: ' + actual.get('text', '') + ' ' + actual.get('unit', '')
    except EtaError:
        actual = None
        cmd.status = 'uncertain'
        cmd.result = 'Ergebnis unklar. Anlagenzustand prüfen. Kein automatischer Neuversuch.'
    cmd.save()
    return actual

@can_operate
@require_POST
def control_set(request, pk):
    """Schaltet einen Punkt direkt. Nur bei Punkten mit Rückfrage folgt vorher eine einfache Ja/Nein-Seite (ohne Passwort)."""
    point = get_object_or_404(Point, pk=pk, control=True, enabled=True)
    if not control_allowed(request, point):
        return HttpResponseForbidden('Für diesen Bereich fehlt das Recht zum Schalten.')
    if point.section == 'Wiesenegger' and wiesenegger_control_profile(request) == WIESENEGGER_PROFILE_LIMITED:
        active, _, _ = wiesenegger_controls_active()
        if not active:
            return HttpResponseForbidden('Wiesenegger-Steuerung ist nur mit aktiver ESP32-Freigabe erlaubt.')
    if not get_state('controls').get('enabled'):
        return HttpResponseForbidden('Steuerung ist nicht freigegeben')
    if limited(request, 'control', 30, scope=str(request.user.pk)):
        return HttpResponse('Zu viele Steuerungsanfragen', status=429)
    with file_lock('control'):
        client = EtaClient()
        try:
            value = request.POST.get('value', '')
            info, before, raw, ticks = prepare_write(client, point, value)
            if point.confirm and request.POST.get('confirmed') != '1':
                if ticks:
                    proposed = tick_text(ticks[0]) + ' - ' + tick_text(ticks[1])
                else:
                    proposed = next((v['label'] for v in info['choices'] if v['raw'] == raw), str(Decimal(raw)/Decimal(info['scale'])))
                return render(request, 'confirm_control.html', {'point': point, 'before': before, 'proposed': proposed, 'unit': info['unit'], 'value': value})
            cmd = Command.objects.create(id=uuid.uuid4(), user=request.user, role='admin' if request.is_admin else 'person', point=point, raw=raw, before=(before['text'] if ticks else before['raw'])[:80], expires=timezone.now(), status='sending')
        except EtaError as exc:
            messages.error(request, str(exc))
            return redirect('control')
        audit(request, 'control-request', {'command': str(cmd.id), 'point': point.key, 'before': cmd.before, 'raw': raw})
        perform_write(client, point, cmd, before, raw, ticks)
        audit(request, 'control-result', {'command': str(cmd.id), 'status': cmd.status, 'result': cmd.result})
        messages.info(request, point.label + ': ' + cmd.result)
    return redirect('control')

def schedule_changes(post, points):
    """Ermittelt aus dem Formular nur die vom Benutzer geänderten Felder (Vergleich mit '-orig').

    So überschreibt ein Speichern keine Zeitfenster, die inzwischen an der Anlage geändert wurden
    und im Formular unverändert blieben.
    """
    changes = []
    for day_key, day_label in WIESENEGGER_DAY_KEYS:
        key_outside, *slot_keys = WIESENEGGER_TIME_KEYS[day_key]
        windows = []
        for number, key in enumerate(slot_keys, 1):
            point = points.get(key)
            orig = post.get(key + '-orig')
            if not point or not orig:
                continue
            try:
                orig_ticks = tuple(int(x) for x in orig.split('-'))
                if len(orig_ticks) != 2 or not 0 <= orig_ticks[0] <= orig_ticks[1] <= 96:
                    raise ValueError
            except ValueError:
                raise ValueError('Formular veraltet. Bitte Seite neu laden.')
            name = '%s, Zeitfenster %d' % (day_label, number)
            if post.get(key + '-on') == 'on':
                try:
                    wanted = (time_ticks(post.get(key + '-begin')), time_ticks(post.get(key + '-end'), is_end=True))
                except ValueError as exc:
                    raise ValueError(name + ': ' + str(exc))
                if wanted[1] <= wanted[0]:
                    raise ValueError(name + ': Ende muss nach dem Beginn liegen.')
            else:
                wanted = (0, 0)
            if wanted[1] > wanted[0]:
                windows.append((wanted, number))
            if wanted != orig_ticks:
                changes.append({'kind': 'slot', 'point': point, 'name': name, 'ticks': wanted, 'orig': orig_ticks,
                                'before': tick_text(orig_ticks[0]) + ' - ' + tick_text(orig_ticks[1]) if orig_ticks[1] > orig_ticks[0] else 'kein Zeitfenster',
                                'after': tick_text(wanted[0]) + ' - ' + tick_text(wanted[1]) if wanted[1] > wanted[0] else 'kein Zeitfenster'})
        windows.sort()
        for (a, na), (b, nb) in zip(windows, windows[1:]):
            if b[0] < a[1]:
                raise ValueError('%s: Zeitfenster %d und %d überschneiden sich.' % (day_label, na, nb))
        point = points.get(key_outside)
        orig = post.get(key_outside + '-orig')
        if point and orig is not None and post.get(key_outside + '-value') is not None:
            try:
                before_value, wanted_value = Decimal(orig), Decimal(post.get(key_outside + '-value').replace(',', '.'))
                if not wanted_value.is_finite():
                    raise InvalidOperation
            except InvalidOperation:
                raise ValueError(day_label + ': Temperatur außerhalb der Zeitfenster ist ungültig.')
            if wanted_value != before_value:
                changes.append({'kind': 'value', 'point': point, 'name': day_label + ', außerhalb der Zeitfenster', 'value': str(wanted_value), 'orig': before_value,
                                'before': '%s °C' % before_value.normalize(), 'after': '%s °C' % wanted_value.normalize()})
    return changes

@can_operate
@require_POST
def control_schedule(request):
    """Speichert die Wiesenegger-Wochenschaltuhr: nur geänderte Felder, je Feld schreiben und zurücklesen."""
    keys = [k for keys in WIESENEGGER_TIME_KEYS.values() for k in keys]
    points = {p.key: p for p in Point.objects.filter(key__in=keys, control=True, enabled=True)}
    try:
        changes = schedule_changes(request.POST, points)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('control')
    if not changes:
        messages.info(request, 'Zeitschaltuhr: keine Änderungen.')
        return redirect('control')
    if any(not control_allowed(request, c['point']) for c in changes):
        return HttpResponseForbidden('Für diesen Bereich fehlt das Recht zum Schalten.')
    if wiesenegger_control_profile(request) == WIESENEGGER_PROFILE_LIMITED and not wiesenegger_controls_active()[0]:
        return HttpResponseForbidden('Wiesenegger-Steuerung ist nur mit aktiver ESP32-Freigabe erlaubt.')
    if not get_state('controls').get('enabled'):
        return HttpResponseForbidden('Steuerung ist nicht freigegeben')
    if any(c['point'].confirm for c in changes) and request.POST.get('confirmed') != '1':
        fields = [(k, v) for k, v in request.POST.items() if k not in ('csrfmiddlewaretoken', 'confirmed')]
        return render(request, 'confirm_schedule.html', {'changes': changes, 'fields': fields})
    if limited(request, 'control', 30, scope=str(request.user.pk)):
        return HttpResponse('Zu viele Steuerungsanfragen', status=429)
    role = 'admin' if request.is_admin else 'person'
    done, problems = 0, []
    with file_lock('control'):
        client = EtaClient()
        for index, change in enumerate(changes):
            point = change['point']
            skipped = len(changes) - index - 1
            try:
                info, before = client.info(point.uri), client.read(point.uri)
                if not info.get('writable'):
                    raise EtaError('Dieser Wert ist nicht schreibbar')
                if change['kind'] == 'slot':
                    if info.get('type') != 'TIMESLOT':
                        raise EtaError('Datenpunkt ist kein Zeitfenster')
                    current = slot_ticks(before.get('text', ''))
                    if current is not None and current[0] == current[1]:
                        current = (0, 0)
                    if current != change['orig']:
                        problems.append(change['name'] + ': wurde inzwischen an der Anlage geändert (' + before.get('text', '?') + '), nicht überschrieben.')
                        continue
                    raw = '%d-%d' % change['ticks']
                else:
                    raw = client.validate(info, change['value'])
            except EtaError as exc:
                problems.append(change['name'] + ': ' + str(exc) + '. Nicht gesendet.')
                continue
            cmd = Command.objects.create(id=uuid.uuid4(), user=request.user, role=role, point=point, raw=raw, before=(before.get('text') if change['kind'] == 'slot' else before['raw'])[:80], expires=timezone.now(), status='sending')
            audit(request, 'control-request', {'command': str(cmd.id), 'point': point.key, 'before': cmd.before, 'raw': raw, 'source': 'schedule'})
            try:
                if change['kind'] == 'slot':
                    client.write_timeslot(point.uri, before['raw'], *change['ticks'])
                    actual = client.read(point.uri)
                    same = slot_ticks(actual.get('text', '')) in ({change['ticks']} | ({(0, 0), (96, 96)} if change['ticks'] == (0, 0) else set()))
                else:
                    client.write(point.uri, raw)
                    actual = client.read(point.uri)
                    try:
                        same = Decimal(actual['raw']) == Decimal(raw)
                    except (InvalidOperation, TypeError):
                        same = False
                # Rückgelesenen Wert sofort anzeigen; sonst wäre das Formular bis zum nächsten Abruf veraltet.
                Point.objects.filter(pk=point.pk).update(latest={**point.latest, **actual}, updated=timezone.now())
                cmd.status = 'confirmed' if same else 'different'
                cmd.result = 'Rückgelesen: ' + (actual.get('text', '') if change['kind'] == 'slot' else str(actual.get('value', actual.get('raw', ''))) + ' °C')
            except EtaError:
                cmd.status = 'uncertain'
                cmd.result = 'Ergebnis unklar. Anlagenzustand prüfen. Kein automatischer Neuversuch.'
            cmd.save()
            audit(request, 'control-result', {'command': str(cmd.id), 'status': cmd.status, 'result': cmd.result})
            if cmd.status == 'confirmed':
                done += 1
            else:
                problems.append(change['name'] + ': ' + cmd.result)
                if cmd.status == 'uncertain':
                    if skipped:
                        problems.append('%d weitere Änderung(en) wurden deshalb nicht gesendet.' % skipped)
                    break
    if done:
        messages.success(request, 'Zeitschaltuhr: %d von %d Änderung(en) gesendet und bestätigt.' % (done, len(changes)))
    for problem in problems:
        messages.error(request, problem)
    return redirect('control')

@admin_only
def people(request):
    link = None
    if request.method == 'POST':
        try:
            with transaction.atomic():
                action = request.POST.get('action')
                if action == 'create':
                    name = request.POST.get('username','').strip().lower()
                    email = request.POST.get('email','').strip().lower()
                    if not name or len(name)>150 or User.objects.filter(username__iexact=name).exists():
                        raise ValueError('Benutzername fehlt oder ist bereits vergeben')
                    if email:
                        validate_email(email)
                        if User.objects.filter(email__iexact=email).exists():
                            raise ValueError('E-Mail-Adresse ist bereits vergeben')
                    user = User.objects.create_user(name, email=email)
                else:
                    user = get_object_or_404(User, pk=request.POST.get('user'))
                if action in ('create','save'):
                    email = request.POST.get('email',user.email).strip().lower()
                    if email:
                        validate_email(email)
                        if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                            raise ValueError('E-Mail-Adresse ist bereits vergeben')
                    user.email = email
                    make_admin = request.POST.get('admin') == 'on'
                    preset = request.POST.get('preset', '') if action == 'create' else ''
                    if preset:
                        if preset not in rights.PRESETS:
                            raise ValueError('Ungültige Vorlage')
                        grants = dict(rights.PRESETS[preset][1])
                    else:
                        grants = {}
                        for i, section in enumerate(rights.SECTIONS):
                            level = request.POST.get('grant-%d' % i, '')
                            if level not in ('', 'view', 'control'):
                                raise ValueError('Ungültige Berechtigung')
                            if level:
                                grants[section] = level
                    if request.POST.get('wiese-profile', WIESENEGGER_PROFILE_LIMITED if preset == 'wiesenegger' else WIESENEGGER_PROFILE_FULL) not in (WIESENEGGER_PROFILE_LIMITED, WIESENEGGER_PROFILE_FULL):
                        raise ValueError('Ungültiges Wiesenegger-Detailrecht')
                    wiese_profile = request.POST.get('wiese-profile', WIESENEGGER_PROFILE_LIMITED if preset == 'wiesenegger' else WIESENEGGER_PROFILE_FULL)
                    active = request.POST.get('active') == 'on'
                    was_admin = user.is_active and user.groups.filter(name='admin').exists()
                    if was_admin and (not active or not make_admin) and not User.objects.filter(is_active=True,groups__name='admin').exclude(pk=user.pk).exists():
                        raise ValueError('Der letzte aktive Administrator muss erhalten bleiben')
                    make_device = request.POST.get('mqtt') == 'on'
                    if make_admin and make_device:
                        raise ValueError('Ein MQTT-Gerät kann kein Administrator sein')
                    user.groups.set([Group.objects.get_or_create(name=name)[0] for name, on in (('admin', make_admin), ('mqtt', make_device)) if on])
                    user.grants.all().delete()
                    user.control_details.all().delete()
                    Grant.objects.bulk_create([Grant(user=user, section=sec, level=lvl) for sec, lvl in grants.items()])
                    if grants.get('Wiesenegger') == 'control':
                        ControlDetail.objects.create(user=user, section='Wiesenegger', profile=wiese_profile)
                    user.is_active = active
                    user.save()
                    if not active:
                        AccessToken.objects.filter(user=user).update(used=True)
                        from django.contrib.sessions.models import Session
                        for session in Session.objects.filter(expire_date__gt=timezone.now()):
                            if str(session.get_decoded().get('_auth_user_id')) == str(user.pk):
                                session.delete()
                    audit(request, 'person-save', {'user':user.pk,'admin':make_admin,'mqtt':make_device,'grants':grants,'active':active})
                if action in ('create','invite'):
                    if not user.is_active:
                        raise ValueError('Gesperrte Person kann nicht eingeladen werden')
                    AccessToken.objects.filter(user=user, purpose='invite').update(used=True)
                    link = issue_token(user, 'invite')
                    audit(request, 'invite-created', {'user':user.pk})
                if action == 'revoke':
                    AccessToken.objects.filter(user=user).update(used=True)
                    audit(request, 'access-revoked', {'user':user.pk})
                messages.success(request, 'Zugang gespeichert.' if not link else 'Einladungslink erstellt. Er wurde nicht versendet und ist 48 Stunden gültig.')
        except (ValueError, ValidationError) as exc:
            messages.error(request, str(exc))
    users = list(User.objects.prefetch_related('groups', 'grants', 'control_details').order_by('username'))
    for user in users:
        user.is_admin_flag = any(g.name == 'admin' for g in user.groups.all())
        user.is_mqtt_flag = any(g.name == 'mqtt' for g in user.groups.all())
        levels = {g.section: g.level for g in user.grants.all()}
        details = {g.section: g.profile for g in user.control_details.all()}
        user.rows = [(i, rights.label(sec), levels.get(sec, '')) for i, sec in enumerate(rights.SECTIONS)]
        user.wiese_profile = details.get('Wiesenegger', WIESENEGGER_PROFILE_FULL)
        invitation = AccessToken.objects.filter(user=user, purpose='invite', used=False).order_by('-expires').first()
        user.access_status = 'Gesperrt' if not user.is_active else ('Aktiv' if user.has_usable_password() else ('Einladung offen' if invitation and invitation.expires>timezone.now() else 'Einladung abgelaufen' if invitation else 'Kein Zugang'))
    return render(request, 'people.html', {'people':users,'levels':rights.LEVELS,'presets':[(k, v[0]) for k, v in rights.PRESETS.items()],'sections':list(enumerate(rights.label(s) for s in rights.SECTIONS)),'wiese_profiles':rights.WIESENEGGER_CONTROL_PROFILES,'invitation':link, 'smtp':bool(settings.EMAIL_HOST)})

def mqtt_device(request, devices):
    device = request.GET.get('device', '')
    return device if device in devices else (devices[0] if devices else '<geraet>')

@admin_only
def mqtt_page(request):
    """Topic und Payload je Schaltpunkt; Freigabe einzelner Punkte für MQTT."""
    from . import mqtt as bridge
    from . import mqtt_sensors
    if request.method == 'POST':
        if request.POST.get('action') == 'sensor-section':
            try:
                mqtt_sensors.configure(request.POST.get('sensor-device', ''), request.POST.get('section', ''))
                audit(request, 'mqtt-sensor-section', {'device': request.POST.get('sensor-device'), 'section': request.POST.get('section')})
                messages.success(request, 'Sensorbereich gespeichert. Neue Messwerte erscheinen nach wenigen Sekunden in der Übersicht.')
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect('mqtt')
        allowed = set(request.POST.getlist('mqtt'))
        changed = []
        for point in Point.objects.filter(control=True):
            if point.mqtt != (point.key in allowed):
                point.mqtt = point.key in allowed
                point.save(update_fields=['mqtt'])
                changed.append({'point': point.key, 'mqtt': point.mqtt})
        audit(request, 'mqtt-points', {'changed': changed})
        messages.success(request, 'MQTT-Freigaben gespeichert (%d geändert).' % len(changed))
        return redirect(reverse('mqtt') + ('?device=' + request.POST['device'] if request.POST.get('device') else ''))
    devices = bridge.devices()
    device = mqtt_device(request, devices)
    state = get_state('mqtt')
    if state.get('at'):
        state['stale'] = (timezone.now() - parse_datetime(state['at'])).total_seconds() > 60
    mapping = get_state(mqtt_sensors.MAPPING_KEY)
    sensor_devices = [{'name': name, 'section': mapping.get(name, '')} for name in devices]
    return render(request, 'mqtt.html', {'docs': bridge.point_docs(device), 'data': bridge.data_docs(), 'devices': devices, 'device': device, 'state': state, 'sensor_devices': sensor_devices, 'sensor_sections': [(s, rights.label(s)) for s in rights.SECTIONS]})

@admin_only
@require_GET
def mqtt_markdown(request):
    from . import mqtt as bridge
    device = mqtt_device(request, bridge.devices())
    return HttpResponse(bridge.markdown(device), content_type='text/markdown; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="eta_mqtt_punkte.md"'})

@admin_only
def backup_page(request):
    return render(request, 'backups.html', {'backups':backups.manifests(),'policy':get_state('backup-policy'),'last':get_state('backup-last'),'retry':get_state('backup-retry'),'worker':get_state('worker'),'pending':ChangeEvent.objects.count(),'jobs':Job.objects.order_by('-id')[:10], 'original_path':str(settings.BACKUP_DIR)})

@admin_only
@require_POST
def backup_settings(request):
    try:
        mode = request.POST.get('mode')
        if mode not in ('time','event','hybrid'):
            raise ValueError('Ungültiger Sicherungsmodus')
        policy = {'enabled':request.POST.get('enabled')=='on','mode':mode}
        for key, low, high in [('interval',60,2592000),('quiet',30,86400),('minimum',60,86400),('maximum',60,86400),('keep',2,365)]:
            value = int(request.POST.get(key,''))
            if not low<=value<=high:
                raise ValueError('Ungültiger Bereich: '+key)
            policy[key] = value
        target = request.POST.get('target','').strip()
        if target and (not target.startswith('/targets/') or '..' in target):
            raise ValueError('Zielpfad muss unter /targets liegen')
        policy.update(target=target, target_marker=request.POST.get('target_marker','').strip())
        if target and not policy['target_marker']:
            raise ValueError('Datenträgerkennung erforderlich')
        set_state('backup-policy',policy)
        audit(request,'backup-policy',policy)
        messages.success(request,'Sicherungsregeln gespeichert.')
    except (ValueError, TypeError) as exc:
        messages.error(request,str(exc))
    return redirect('backups')

@admin_only
@require_POST
def backup_action(request):
    action = request.POST.get('action')
    try:
        ident = request.POST.get('id','')
        if action in ('backup','test'):
            if action == 'test':
                backups.artifact(ident)
            Job.objects.create(kind=action,payload={'id':ident,'actor':request.user.username})
            messages.success(request,'Auftrag eingeplant. Status wird unten angezeigt.')
        elif action == 'upload':
            if 'file' not in request.FILES:
                raise ValueError('Datei auswählen')
            backups.import_backup(request.FILES['file'])
            messages.success(request,'Sicherung geprüft und importiert.')
        else:
            raise ValueError('Unbekannte Aktion')
        audit(request,'backup-'+action,{'id':ident})
    except (ValueError, OSError, Exception) as exc:
        messages.error(request,str(exc)[:200])
    return redirect('backups')

@admin_only
def backup_confirm(request, ident, action):
    if action not in ('restore','delete'):
        return HttpResponseBadRequest('Ungültige Aktion')
    try:
        meta,path = backups.artifact(ident)
        if request.method == 'POST':
            if limited(request, 'sensitive', 20):
                return HttpResponse('Zu viele Versuche', status=429)
            if request.POST.get('instance') != settings.INSTANCE_NAME or not request.user.check_password(request.POST.get('password','')):
                raise ValueError('Instanzname oder Passwort stimmt nicht')
            if action == 'restore':
                backups.inspect_db(path)
                Job.objects.create(kind='restore',payload={'id':ident,'actor':request.user.username})
                messages.success(request,'Wiederherstellung eingeplant. Sitzungen werden beendet; automatische Sicherung und Steuerung werden danach gesperrt.')
            else:
                with file_lock('backup'):
                    backups.delete_backup(ident)
                messages.success(request,'Sicherung gelöscht.')
            audit(request,'backup-'+action,{'id':ident})
            return redirect('backups')
        return render(request,'confirm_backup.html',{'backup':meta,'action':action})
    except (ValueError,OSError) as exc:
        messages.error(request,str(exc))
        return redirect('backups')

@admin_only
@require_GET
def backup_download(request, ident):
    try:
        meta,path = backups.artifact(ident)
        audit(request,'backup-download',{'id':ident})
        return FileResponse(open(path,'rb'),as_attachment=True,filename=meta['filename'])
    except (ValueError,OSError) as exc:
        return HttpResponseBadRequest(str(exc))
