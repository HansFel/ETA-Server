"""Calculate differences while reading graph data; never persist results."""
import heapq
from itertools import groupby
from datetime import timedelta
from types import SimpleNamespace
from django.conf import settings
from .archives import iter_samples
from .models import Point


def inputs(point):
    topics = point.metadata.get('inputs', [])
    if len(topics) != 2:
        return []
    candidates = list(Point.objects.filter(section=point.section, enabled=True,
        uri__startswith='mqtt:' + point.metadata.get('device', '') + ':'))
    result = []
    for topic in topics:
        matching = [p for p in candidates if p.metadata.get('topic') == topic and not p.metadata.get('derived')]
        if len(matching) != 1:
            return []
        result.append(matching[0])
    return result


def graph_samples(point, start, end):
    if not point.metadata.get('derived'):
        yield from iter_samples(point, start, end)
        return
    sources = inputs(point)
    if len(sources) != 2:
        return
    tolerance = timedelta(seconds=settings.POLL_SECONDS * 1.5)
    def stream(index):
        for row in iter_samples(sources[index], start-tolerance, end):
            yield (row.at, index, row)
    latest = {}
    for at, group in groupby(heapq.merge(stream(0), stream(1), key=lambda item: (item[0], item[1])), key=lambda item: item[0]):
        for _, index, row in group:
            latest[index] = row
        if at < start:
            continue
        valid = len(latest) == 2 and all(r.quality == 'ok' and r.value is not None and at-r.at <= tolerance for r in latest.values())
        value = round(latest[0].value-latest[1].value, 3) if valid else None
        yield SimpleNamespace(at=at, value=value, quality='ok' if valid else 'offline', unit='°C')
