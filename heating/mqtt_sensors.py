"""ESP32 sensor snapshots, with explicit administrator-assigned visibility."""
import hashlib
import json
import math
import re
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import Point, Sample
from .mqtt import device_actor
from .rights import SECTIONS
from .security import file_lock
from .services import get_state, set_state

SENSOR_TOPIC = 'etaio/+/sensors'
TOPIC_RE = re.compile(r'^etaio/([A-Za-z0-9_-]{1,64})/sensors$')
MAPPING_KEY = 'mqtt-sensor-sections'
DIFFERENCES = {
    'fw': ('Temperaturdifferenz FW (berechnet)', 'vorlauf-fw', 'retour-fw'),
    'haus': ('Temperaturdifferenz Haus (berechnet)', 'haus-vorlauf', 'haus-retour'),
}


def store(point, name, section, latest, metadata, now):
    previous = Sample.objects.filter(point=point).order_by('-at').first()
    if previous is None or (now - previous.at).total_seconds() >= settings.POLL_SECONDS or previous.quality != latest['quality']:
        Sample.objects.create(point=point, at=now, **latest)
    Point.objects.filter(pk=point.pk).update(label=name, section=section, latest=latest, updated=now, metadata=metadata)


def differences(device, section, readings, now):
    for pair, (name, supply, return_) in DIFFERENCES.items():
        supply_topic = 'etaio/' + device + '/' + supply + '/temperature'
        return_topic = 'etaio/' + device + '/' + return_ + '/temperature'
        uri = 'mqtt:' + device + ':delta-' + pair
        point = Point.objects.filter(uri=uri).first()
        # Discover a pair only when both topics occur in the same snapshot.
        if point is None:
            if supply_topic not in readings or return_topic not in readings:
                continue
            point = Point.objects.create(uri=uri, key='esp-' + hashlib.sha256(uri.encode()).hexdigest()[:24], label=name, section=section)
        if not point.enabled:
            continue
        metadata = {'source': 'mqtt', 'device': device, 'derived': True,
            'formula': 'Vorlauf − Retour', 'inputs': [supply_topic, return_topic], 'unit': '°C'}
        # This is a selectable graph definition, never a stored calculated reading.
        if point.metadata != metadata or point.latest != {'unit': '°C'} or point.section != section:
            Point.objects.filter(pk=point.pk).update(label=name, section=section,
                latest={'unit': '°C'}, updated=None, metadata=metadata)


def configure(device, section):
    if not device_actor(device) or section not in ['', *SECTIONS]:
        raise ValueError('Ungültiges Gerät oder ungültiger Sensorbereich')
    with transaction.atomic():
        mapping = get_state(MAPPING_KEY)
        mapping[device] = section
        set_state(MAPPING_KEY, mapping)
        # An unassigned sensor is only visible to administrators.
        Point.objects.filter(uri__startswith='mqtt:' + device + ':').update(section=section or 'MQTT nicht zugeordnet')


def ingest(topic, payload, retained=False):
    match = TOPIC_RE.fullmatch(topic)
    if not match or retained or len(payload) > 16384:
        return 0
    device = match[1]
    if not device_actor(device):
        return 0
    section = get_state(MAPPING_KEY).get(device)
    if section not in SECTIONS:
        return 0
    try:
        rows = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return 0
    if not isinstance(rows, list) or len(rows) > 32:
        return 0
    now = timezone.now()
    count = 0
    seen = set()
    readings = {}
    with file_lock('maintenance', exclusive=False), transaction.atomic():
        for row in rows:
            if not isinstance(row, dict):
                continue
            sensor_id = row.get('id')
            source_topic = row.get('topic')
            if not isinstance(sensor_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', sensor_id) or sensor_id in seen:
                continue
            if not isinstance(source_topic, str) or not re.fullmatch(r'etaio/' + re.escape(device) + r'/[A-Za-z0-9_-]{1,64}/temperature', source_topic):
                continue
            seen.add(sensor_id)
            name = row.get('name')
            name = name.strip()[:100] if isinstance(name, str) and name.strip() else sensor_id
            value = row.get('temperature')
            valid = type(value) in (int, float) and -55 <= value <= 125 and math.isfinite(value)
            value = float(value) if valid else None
            uri = 'mqtt:' + device + ':' + hashlib.sha256(sensor_id.encode()).hexdigest()[:24]
            key = 'esp-' + hashlib.sha256(uri.encode()).hexdigest()[:24]
            point, _ = Point.objects.get_or_create(uri=uri, defaults={'key': key, 'label': name, 'section': section})
            if not point.enabled:
                continue
            latest = {'value': value, 'raw': str(value) if valid else '', 'text': str(value) if valid else 'Sensor nicht verfügbar', 'unit': '°C', 'quality': 'ok' if valid else 'offline'}
            store(point, name, section, latest, {'source': 'mqtt', 'device': device, 'sensor_id': sensor_id, 'topic': source_topic, 'unit': '°C'}, now)
            readings[source_topic] = value
            count += 1
        differences(device, section, readings, now)
    return count
