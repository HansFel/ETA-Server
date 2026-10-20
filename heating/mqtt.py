"""MQTT-Brücke: ESP32-Geräte schalten über die App.

Nur die App schreibt in die ETA. Ein Gerät ist ein Konto in der Gruppe ``mqtt`` mit demselben
Namen wie sein MQTT-Benutzer; der Broker erlaubt jedem Benutzer nur seine eigenen Topics
(``eta/dev/<benutzer>/...``), daher identifiziert das Topic das Gerät. Es gelten dieselben Prüfungen
wie im Web: Rechte je Bereich, Wiesenegger-Profil und ESP32-Modus, globale Freigabe, Wertebereich,
Rücklesen, Protokoll. Zusätzlich muss der Administrator den Punkt für MQTT freigegeben haben
(``Point.mqtt``). Gerätekonten sind nie Administratoren.
"""
from decimal import Decimal, InvalidOperation
import json
import re
import uuid
from types import SimpleNamespace
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .eta import EtaClient, EtaError
from .models import Point, Command, Audit
from .security import file_lock, limited
from .services import get_state
from . import rights

DEVICE_GROUP = 'mqtt'
COMMAND_TOPIC = 'eta/dev/+/cmd/+/set'
TOPIC_RE = re.compile(r'^eta/dev/([A-Za-z0-9_-]{1,64})/cmd/([a-z0-9-]{1,50})/set$')
SWITCH_WORDS = {'on': 'ein', 'ein': 'ein', '1': 'ein', 'true': 'ein', 'off': 'aus', 'aus': 'aus', '0': 'aus', 'false': 'aus'}

def device_actor(device_id):
    """Gerätekonto als Request-Ersatz für die Rechteprüfung; None, wenn kein aktives Gerät."""
    user = User.objects.filter(username=device_id, is_active=True, groups__name=DEVICE_GROUP).first()
    if not user or rights.is_admin(user):
        return None
    return SimpleNamespace(user=user, is_admin=False, grants=rights.grants_for(user), grant_details=rights.details_for(user),
                           META={'REMOTE_ADDR': 'mqtt'})

def devices():
    return [u.username for u in User.objects.filter(is_active=True, groups__name=DEVICE_GROUP).order_by('username')]

def result_topic(device_id, key):
    return 'eta/dev/%s/cmd/%s/result' % (device_id, key)

def state_topic(device_id, key):
    return 'eta/dev/%s/state/%s' % (device_id, key)

def state_payload(point):
    fresh = point.updated is not None and (timezone.now() - point.updated).total_seconds() <= settings.POLL_SECONDS * 3
    latest = point.latest or {}
    quality = latest.get('quality', 'offline') if fresh else 'stale'
    meta = point.metadata or {}
    return {'schema': 1, 'key': point.key, 'label': point.label, 'section': point.section,
            'value': latest.get('value') if quality == 'ok' else None, 'text': latest.get('text', '') if quality == 'ok' else '',
            'raw': latest.get('raw', '') if quality == 'ok' else '', 'unit': latest.get('unit', ''), 'quality': quality,
            'at': point.updated.isoformat() if point.updated else None, 'control': point.control,
            'choices': [c['label'] for c in meta.get('choices', [])], 'type': meta.get('type', '')}

def visible_states(device_id):
    """Topic -> Payload für alle Punkte, die das Gerät sehen darf."""
    actor = device_actor(device_id)
    if not actor:
        return {}
    return {state_topic(device_id, p.key): state_payload(p) for p in Point.objects.filter(enabled=True, section__in=rights.visible_sections(actor)) if not p.metadata.get('derived')}

def public_states():
    """Alle Heizungsdaten für alle MQTT-Konten (nur lesen): jeder Datenpunkt, Abfragestatus und ETA-Störungen."""
    from .services import get_state
    states = {'eta/state/%s' % p.key: state_payload(p) for p in Point.objects.filter(enabled=True).exclude(uri__startswith='mqtt:')}
    now = timezone.now()
    logger, worker = get_state('logger'), get_state('worker')
    worker_at = parse_datetime(worker['at']) if worker.get('at') else None
    states['eta/status'] = {'schema': 1, 'instance': settings.INSTANCE_NAME, 'poll_seconds': settings.POLL_SECONDS,
                            'logger_at': logger.get('at'), 'logger_ok': bool(logger.get('ok')), 'points': logger.get('points'), 'failed': logger.get('failed'),
                            'worker_at': worker.get('at'), 'worker_ok': bool(worker.get('ok')) and worker_at is not None and (now - worker_at).total_seconds() <= 120}
    errors = get_state('errors')
    # Ohne aktuelle Abfrage ist die Störungsliste unbekannt, nicht leer.
    states['eta/errors'] = {'schema': 1, 'known': bool(errors.get('ok')), 'at': errors.get('at'), 'items': errors.get('items', []) if errors.get('ok') else None}
    return states

def switch_value(info, value):
    """Klartext einer Auswahl (z. B. 'Ein', 'Aus', 'ON') auf den Rohwert abbilden; sonst unverändert."""
    labels = {c['label'].strip().casefold(): c['raw'] for c in info.get('choices', [])}
    text = str(value).strip().casefold()
    if text in labels:
        return labels[text]
    word = SWITCH_WORDS.get(text)
    if word and set(labels) == {'ein', 'aus'}:
        return labels[word]
    return value

def parse_payload(payload):
    """JSON {"command_id", "value", "ts_utc"?} oder reiner Text als Wert. Liefert (command_id, value, ts_utc)."""
    text = payload.decode('utf-8') if isinstance(payload, bytes) else payload
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        return str(uuid.UUID(str(data.get('command_id', '')))), str(data['value']).strip(), data.get('ts_utc')
    if text.lstrip().startswith(('{', '[')):
        raise ValueError('Fehlerhaftes JSON')
    # Einfache Form für Aufgaben/Automationen: der ganze Inhalt ist der Wert.
    return str(uuid.uuid4()), text.strip(), None

def reject(device_id, key, command_id, reason, detail=None):
    Audit.objects.create(actor=device_id[:150], role='mqtt', action='mqtt-rejected', detail={'point': key, 'command': command_id, 'reason': reason, **(detail or {})})
    return {'schema': 1, 'command_id': command_id, 'point': key, 'status': 'rejected', 'reason': reason, 'ts_utc': timezone.now().isoformat()}

def handle_command(topic, payload, retained=False):
    """Verarbeitet einen Schaltbefehl. Liefert (Ergebnis-Topic, Ergebnis) oder None bei unbrauchbarem Topic."""
    # Import hier, damit views (Django-Views) nicht beim Laden der Brücke Templates braucht.
    from .views import control_allowed, wiesenegger_control_profile, wiesenegger_controls_active, prepare_write, perform_write, WIESENEGGER_PROFILE_LIMITED
    match = TOPIC_RE.match(topic)
    if not match:
        return None
    device_id, key = match.groups()
    out = result_topic(device_id, key)
    if retained:
        # Gespeicherte Befehle würden bei jedem Neustart erneut schalten.
        return out, reject(device_id, key, None, 'Gespeicherte (retained) Befehle werden nicht ausgeführt')
    try:
        command_id, value, ts_utc = parse_payload(payload)
        if not value or len(value) > 40:
            raise ValueError
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return out, reject(device_id, key, None, 'Ungültiger Befehl: Wert als Text oder JSON mit command_id (UUID) und value erwartet')
    sent = parse_datetime(str(ts_utc)) if ts_utc else None
    if ts_utc and (sent is None or timezone.is_naive(sent)):
        return out, reject(device_id, key, command_id, 'ts_utc ungültig (ISO 8601 mit Zeitzone erwartet)')
    if sent and abs((timezone.now() - sent).total_seconds()) > settings.MQTT_COMMAND_MAX_AGE:
        return out, reject(device_id, key, command_id, 'Befehl zu alt oder Geräteuhr falsch')
    previous = Command.objects.filter(pk=command_id).first()
    if previous:
        # Wiederholte Zustellung (QoS 1): nicht erneut schalten, altes Ergebnis melden.
        return out, {'schema': 1, 'command_id': command_id, 'point': key, 'status': previous.status, 'reason': 'Bereits verarbeitet', 'result': previous.result, 'ts_utc': timezone.now().isoformat()}
    actor = device_actor(device_id)
    if not actor:
        return out, reject(device_id, key, command_id, 'Unbekanntes oder gesperrtes Gerät')
    point = Point.objects.filter(key=key, control=True, enabled=True).first()
    if not point:
        return out, reject(device_id, key, command_id, 'Unbekannter Schaltpunkt')
    if not control_allowed(actor, point):
        return out, reject(device_id, key, command_id, 'Für diesen Bereich fehlt das Recht zum Schalten')
    if point.section == 'Wiesenegger' and wiesenegger_control_profile(actor) == WIESENEGGER_PROFILE_LIMITED and not wiesenegger_controls_active()[0]:
        return out, reject(device_id, key, command_id, 'Wiesenegger-Steuerung nur mit aktiver ESP32-Freigabe')
    if not get_state('controls').get('enabled'):
        return out, reject(device_id, key, command_id, 'Steuerung ist nicht freigegeben')
    if not point.mqtt:
        return out, reject(device_id, key, command_id, 'Punkt ist für MQTT nicht freigegeben')
    if limited(actor, 'mqtt-control', 30, scope=device_id):
        return out, reject(device_id, key, command_id, 'Zu viele Befehle (max. 30 in 15 Minuten)')
    with file_lock('control'):
        client = EtaClient()
        try:
            info = client.info(point.uri)
            _, before, raw, ticks = prepare_write(client, point, switch_value(info, value))
        except EtaError as exc:
            return out, reject(device_id, key, command_id, str(exc))
        cmd = Command.objects.create(id=command_id, user=actor.user, role='mqtt', point=point, raw=raw, before=(before['text'] if ticks else before['raw'])[:80], expires=timezone.now(), status='sending')
        Audit.objects.create(actor=device_id, role='mqtt', action='control-request', detail={'command': command_id, 'point': key, 'before': cmd.before, 'raw': raw})
        actual = perform_write(client, point, cmd, before, raw, ticks)
        Audit.objects.create(actor=device_id, role='mqtt', action='control-result', detail={'command': command_id, 'status': cmd.status, 'result': cmd.result})
    result = {'schema': 1, 'command_id': command_id, 'point': key, 'status': cmd.status, 'result': cmd.result, 'ts_utc': timezone.now().isoformat()}
    if actual:
        result.update(raw=actual.get('raw', ''), text=actual.get('text', ''), unit=actual.get('unit', ''))
    return out, result


def number(value):
    try:
        result = Decimal(str(value).strip())
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None

def payload_doc(point):
    """Beschreibt die erlaubten Werte eines Punktes aus den varinfo-Metadaten (keine erfundenen Werte)."""
    meta = point.metadata or {}
    if not meta:
        return {'kind': 'unbekannt', 'values': [], 'hint': 'Noch keine Datenpunktinformationen von der ETA gelesen (nächsten Abruf abwarten).'}
    if not meta.get('writable'):
        return {'kind': 'nur lesen', 'values': [], 'hint': 'Die ETA meldet diesen Punkt als nicht schreibbar.'}
    if meta.get('type') == 'TIMESLOT':
        return {'kind': 'Zeitfenster', 'values': ['06:00 - 09:30', '00:00 - 24:00', '00:00 - 00:00'],
                'hint': 'HH:MM - HH:MM im Viertelstundenraster; 24:00 = Mitternacht; 00:00 - 00:00 = kein Zeitfenster.'}
    choices = meta.get('choices') or []
    if choices:
        labels = {c['label'].strip().casefold() for c in choices}
        on_off = labels == {'ein', 'aus'}
        values = [c['label'] for c in choices] + (['ON', 'OFF'] if on_off else [])
        return {'kind': 'Auswahl', 'values': values,
                'hint': 'Klartext oder Rohwert: ' + ', '.join('%s = %s' % (c['label'], c['raw']) for c in choices) + ('; ON/OFF ebenfalls möglich.' if on_off else '.')}
    scale, low, high = number(meta.get('scale', '1')), number(meta.get('min')), number(meta.get('max'))
    if scale and low is not None and high is not None:
        low, high, step = low / scale, high / scale, Decimal(1) / scale
        unit = (' ' + meta['unit']) if meta.get('unit') else ''
        middle = ((low + high) / 2 / step).to_integral_value() * step
        fmt = lambda d: format(d.normalize(), 'f')
        return {'kind': 'Zahl', 'values': [fmt(middle), fmt(low), fmt(high)],
                'hint': 'Zahl von %s bis %s%s, Schrittweite %s, Dezimalpunkt.' % (fmt(low), fmt(high), unit, fmt(step))}
    return {'kind': 'unbekannt', 'values': [], 'hint': 'Die ETA liefert keinen sicheren Wertebereich; über MQTT nicht schaltbar.'}

def current_example(point, info):
    """Aktueller Wert als Beispiel: ein kopierter Befehl verändert dann nichts."""
    latest = point.latest or {}
    if not info['values'] or latest.get('quality') != 'ok':
        return None
    if info['kind'] == 'Auswahl':
        text = latest.get('text', '').strip()
        return text if text in info['values'] else None
    if info['kind'] == 'Zeitfenster':
        from .views import slot_ticks
        return latest.get('text') if slot_ticks(latest.get('text', '')) else None
    if info['kind'] == 'Zahl' and latest.get('value') is not None:
        return format(Decimal(str(latest['value'])).normalize(), 'f')
    return None

def point_docs(device='<geraet>'):
    """Topic- und Payload-Beschreibung für jeden Schaltpunkt."""
    order = {s: i for i, s in enumerate(rights.SECTIONS)}
    docs = []
    for point in sorted(Point.objects.filter(control=True), key=lambda p: (order.get(p.section, 99), p.id)):
        info = payload_doc(point)
        example = current_example(point, info) or (info['values'][0] if info['values'] else None)
        docs.append({
            'point': point, 'key': point.key, 'label': point.label, 'section': rights.label(point.section),
            'set_topic': 'eta/dev/%s/cmd/%s/set' % (device, point.key), 'result_topic': result_topic(device, point.key),
            'state_topic': state_topic(device, point.key), **info,
            'example_plain': example,
            'example_json': json.dumps({'command_id': '<neue UUID>', 'value': example}, ensure_ascii=False) if example else None,
            'usable': bool(point.mqtt and point.enabled and example),
        })
    return docs

def data_docs():
    """Alle veröffentlichten Datenpunkte (nur lesen) mit Topic und aktuellem Wert."""
    order = {s: i for i, s in enumerate(rights.SECTIONS)}
    return [{'point': p, 'key': p.key, 'label': p.label, 'section': rights.label(p.section), 'topic': 'eta/state/%s' % p.key,
             'unit': (p.latest or {}).get('unit', ''), 'kind': 'Einstellung' if p.control else 'Messwert'}
            for p in sorted(Point.objects.filter(enabled=True), key=lambda p: (order.get(p.section, 99), p.id))]

def markdown(device='<geraet>'):
    lines = ['# MQTT: Heizungsdaten und Schaltpunkte', '',
             'Automatisch erzeugt am %s aus den Datenpunktinformationen der ETA (`python manage.py mqtt_doc`).' % timezone.localtime().strftime('%d.%m.%Y %H:%M'),
             'Allgemeine Regeln: `docs/mqtt-schalten.md`. `%s` = MQTT-Benutzer des Geräts (Konto mit Häkchen „MQTT-Gerät“).' % device, '',
             'Payload als reiner Text (z. B. `Ein`) oder als JSON `{"command_id": "<UUID>", "value": "Ein"}`. QoS 1, **nicht** retained.',
             'Die Beispiele zeigen den Wert zum Erzeugungszeitpunkt (Senden ändert dann nichts); andere Werte laut „Werte“.',
             'Antwort auf `.../result`, aktueller Wert (retained) auf `.../state/<punkt>`.', '']
    lines += ['## Alle Heizungsdaten (nur lesen)', '',
              'Die App veröffentlicht jeden Datenpunkt retained auf `eta/state/<punkt>` (nach jeder Abfrage, nur bei Änderung).',
              'Lesen darf jedes MQTT-Konto, schreiben nur die App. Payload wie `.../state/<punkt>` oben: JSON mit `value`, `text`, `unit`, `quality`, `at`.',
              'Bei `quality` ungleich `ok` sind `value` und `text` leer (nicht als 0 behandeln).', '',
              '- `eta/status`: Abfragezustand (`logger_at`, `logger_ok`, `points`, `failed`, `worker_ok`, `poll_seconds`).',
              '- `eta/errors`: Störungsmeldungen der ETA (`known`, `items`; `items` = null, wenn unbekannt).', '',
              '| Bereich | Datenpunkt | Topic | Art | Einheit |', '|---|---|---|---|---|']
    lines += ['| %s | %s | `%s` | %s | %s |' % (d['section'], d['label'], d['topic'], d['kind'], d['unit'] or '–') for d in data_docs()]
    lines += ['', '## Schaltpunkte', '']
    section = None
    for d in point_docs(device):
        if d['section'] != section:
            section = d['section']
            lines += ['### ' + section, '']
        lines += ['#### %s (`%s`)%s' % (d['label'], d['key'], '' if d['usable'] else ' – derzeit nicht über MQTT schaltbar'), '',
                  '- Befehl: `%s`' % d['set_topic'], '- Ergebnis: `%s`' % d['result_topic'], '- Zustand: `%s`' % d['state_topic'],
                  '- Werte (%s): %s' % (d['kind'], d['hint'])]
        if d['example_plain']:
            lines += ['- Beispiel Text: `%s`' % d['example_plain'], '- Beispiel JSON: `%s`' % d['example_json']]
            if len(d['values']) > 1:
                lines.append('- Weitere Werte: ' + ', '.join('`%s`' % v for v in d['values'] if v != d['example_plain']))
        if not d['point'].mqtt:
            lines.append('- Für MQTT nicht freigegeben (Administrator: Häkchen „Über MQTT schaltbar“).')
        lines.append('')
    return '\n'.join(lines)
