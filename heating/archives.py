"""Lossless five-minute storage blocks, with derived summaries for inspection.

Keep the exact samples inside each compressed block: averaging alone changes
trapezoidal energy, counter resets, runtime and arbitrary range boundaries.
All history consumers use iter_samples; archive movement is atomic and locked.
"""
import hashlib
import heapq
import json
import zlib
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Sample, SampleArchive
from .security import file_lock
from .services import get_state, set_state

FIELDS = ('id', 'at', 'value', 'raw', 'text', 'unit', 'quality')
MAX_BYTES = 4 * 1024 * 1024


def block_start(at):
    at = at.astimezone(dt_timezone.utc)
    return at.replace(minute=at.minute - at.minute % 5, second=0, microsecond=0)


def encode(rows):
    data = [[r.id, r.at.isoformat(), r.value, r.raw, r.text, r.unit, r.quality] for r in rows]
    raw = json.dumps({'version': 1, 'rows': data}, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError('Archivblock überschreitet die zulässige Größe')
    payload = zlib.compress(raw, level=9)
    return payload, hashlib.sha256(payload).hexdigest()


def decode(payload, checksum, count):
    payload = bytes(payload)
    if hashlib.sha256(payload).hexdigest() != checksum:
        raise ValueError('Prüfsummenfehler im Messwertarchiv')
    decoder = zlib.decompressobj()
    raw = decoder.decompress(payload, MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or not decoder.eof or decoder.unused_data:
        raise ValueError('Ungültiger komprimierter Archivblock')
    data = json.loads(raw)
    if data.get('version') != 1 or len(data['rows']) != count:
        raise ValueError('Archivversion oder Messwertanzahl ungültig')
    rows = []
    for values in data['rows']:
        if len(values) != len(FIELDS):
            raise ValueError('Ungültiges Messwertformat')
        row = SimpleNamespace(**dict(zip(FIELDS, values)))
        row.at = datetime.fromisoformat(row.at)
        if timezone.is_naive(row.at):
            raise ValueError('Archivzeit ohne Zeitzone')
        rows.append(row)
    if len({r.id for r in rows}) != count or rows != sorted(rows, key=lambda r: (r.at, r.id)):
        raise ValueError('Doppelte oder unsortierte Archivwerte')
    return rows


def summary(rows):
    units = {}
    for row in rows:
        item = units.setdefault(row.unit, {'valid': 0, 'invalid': 0, 'sum': 0., 'min': None, 'max': None})
        if row.quality != 'ok' or row.value is None:
            item['invalid'] += 1
        else:
            item['valid'] += 1
            item['sum'] += row.value
            item['min'] = row.value if item['min'] is None else min(item['min'], row.value)
            item['max'] = row.value if item['max'] is None else max(item['max'], row.value)
    for item in units.values():
        total = item.pop('sum')
        item['mean'] = total / item['valid'] if item['valid'] else None
    return {'units': units, 'first': rows[0].at.isoformat(), 'last': rows[-1].at.isoformat()}


def iter_samples(point, start, end):
    """Caller checks section permissions; shared lock avoids a mixed pre/post move."""
    def archived():
        for block in SampleArchive.objects.filter(point=point, start__gte=block_start(start), start__lte=end).order_by('start').iterator(chunk_size=128):
            for row in decode(block.payload, block.checksum, block.count):
                if start <= row.at <= end:
                    yield row

    with file_lock('archive', exclusive=False):
        live = Sample.objects.filter(point=point, at__gte=start, at__lte=end).order_by('at', 'id').iterator(chunk_size=1000)
        yield from heapq.merge(live, archived(), key=lambda r: (r.at, r.id))


def storage_stats():
    with file_lock('archive', exclusive=False):
        raw = Sample.objects.count()
        archived = SampleArchive.objects.aggregate(samples=Sum('count'))['samples'] or 0
        blocks = SampleArchive.objects.count()
    return {'raw': raw, 'archived': archived, 'blocks': blocks, 'total': raw + archived,
            'enabled': settings.ARCHIVE_ENABLED, 'days': settings.ARCHIVE_AFTER_DAYS,
            'status': get_state('archive-status')}


def compact_batch(limit=64, now=None):
    """One short batch between polls. Checked pre-archive backup once per UTC day."""
    from . import backups
    now = now or timezone.now()
    cutoff = block_start(now - timedelta(days=settings.ARCHIVE_AFTER_DAYS))
    with file_lock('maintenance', exclusive=False), file_lock('backup'), file_lock('archive'):
        if not Sample.objects.filter(at__lt=cutoff).exists():
            return 0
        safety = get_state('archive-safety')
        if safety.get('day') != now.astimezone(dt_timezone.utc).date().isoformat():
            meta = backups.snapshot('pre-archive')
            backups.restore_test(meta['id'])
            set_state('archive-safety', {'day': now.astimezone(dt_timezone.utc).date().isoformat(), 'backup': meta['id']})
        moved = 0
        for _ in range(limit):
            with transaction.atomic():
                first = Sample.objects.filter(at__lt=cutoff).order_by('at', 'id').first()
                if first is None:
                    break
                start = block_start(first.at)
                query = Sample.objects.filter(point_id=first.point_id, at__gte=start, at__lt=start + timedelta(minutes=5))
                fresh = list(query.order_by('at', 'id')[:4097])
                if len(fresh) > 4096:
                    raise ValueError('Zu viele Messwerte in einem Archivblock')
                old = SampleArchive.objects.filter(point_id=first.point_id, start=start).first()
                rows = (decode(old.payload, old.checksum, old.count) if old else []) + fresh
                rows.sort(key=lambda r: (r.at, r.id))
                payload, checksum = encode(rows)
                checked = decode(payload, checksum, len(rows))
                if [[getattr(r, f) for f in FIELDS] for r in checked] != [[getattr(r, f) for f in FIELDS] for r in rows]:
                    raise ValueError('Rückleseprüfung des Archivs fehlgeschlagen')
                SampleArchive.objects.update_or_create(point_id=first.point_id, start=start, defaults={
                    'count': len(rows), 'summary': summary(rows), 'payload': payload, 'checksum': checksum})
                # Same transaction: either both the archive and deletion commit, or neither.
                query.delete()
                moved += len(fresh)
        set_state('archive-status', {'at': now.isoformat(), 'ok': True, 'moved': moved, 'cutoff': cutoff.isoformat()})
        return moved


def scheduled():
    if not settings.ARCHIVE_ENABLED:
        return
    now = timezone.now()
    state = get_state('archive-status')
    if state.get('retry_after') and now < datetime.fromisoformat(state['retry_after']):
        return
    try:
        compact_batch(now=now)
    except Exception as exc:
        set_state('archive-status', {'at': now.isoformat(), 'ok': False, 'error': str(exc)[:200],
                                    'retry_after': (now + timedelta(hours=1)).isoformat()})
