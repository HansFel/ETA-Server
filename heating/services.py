from django.db import transaction
from django.utils import timezone
from .eta import EtaClient, EtaError
from .models import Point, Sample, State, Audit
from .security import file_lock

def get_state(key, default=None):
    row = State.objects.filter(pk=key).first()
    return row.value if row else (default if default is not None else {})

def set_state(key, value):
    State.objects.update_or_create(key=key, defaults={'value': value})

def audit(request, action, detail):
    Audit.objects.create(actor=request.user.username, role=('admin' if getattr(request, 'is_admin', False) else 'person'), action=action, detail=detail)

def collect():
    client = EtaClient()
    points = list(Point.objects.filter(enabled=True).exclude(uri__startswith='mqtt:'))
    readings = []
    failures = 0
    for point in points:
        at = timezone.now()
        try:
            value = client.read(point.uri)
            meta = point.metadata or client.info(point.uri)
        except EtaError:
            failures += 1
            value, meta = {'quality': 'offline', 'text': 'Keine Verbindung', 'value': None, 'raw': '', 'unit': point.latest.get('unit', '')}, point.metadata
        readings.append((point, at, value, meta))
        if failures >= 2:
            # Avoid a long series of timeouts when the appliance is disconnected.
            for remaining in points[len(readings):]:
                readings.append((remaining, timezone.now(), {'quality': 'offline', 'text': 'Nicht abgefragt', 'value': None, 'raw': '', 'unit': remaining.latest.get('unit', '')}, remaining.metadata))
            break
    try:
        errors = {'ok': True, 'items': client.errors(), 'at': timezone.now().isoformat()} if not failures else {'ok': False, 'items': [], 'at': timezone.now().isoformat()}
    except EtaError:
        errors = {'ok': False, 'items': [], 'at': timezone.now().isoformat()}
    with file_lock('maintenance', exclusive=False), transaction.atomic():
        for point, at, value, meta in readings:
            Sample.objects.create(point=point, at=at, value=value['value'], raw=value['raw'], text=value['text'], unit=value['unit'], quality=value['quality'])
            Point.objects.filter(pk=point.pk).update(latest=value, metadata=meta, updated=at)
        set_state('logger', {'at': timezone.now().isoformat(), 'ok': failures == 0, 'points': len(readings), 'failed': sum(v['quality'] != 'ok' for _, _, v, _ in readings)})
        set_state('errors', errors)
