"""Synthetic storage/restore check. Always uses a fresh temporary database.

Run inside the Linux application image: python scripts/verify_archive_storage.py
No live ETA reads, real users, volumes or external backup targets are used.
"""
import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

with tempfile.TemporaryDirectory(prefix='eta-archive-check-') as directory:
    os.environ.pop('SECRET_KEY_FILE', None)
    os.environ.update(DJANGO_SETTINGS_MODULE='config.settings', SECRET_KEY='isolated-storage-check-only',
        DATA_DIR=directory, BACKUP_DIR=directory+'/backups', INSTANCE_NAME='archive-check',
        BOOTSTRAP_USER='archive-test', ARCHIVE_ENABLED='0')
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth.models import User, Group
    from django.db import connection
    from django.utils import timezone
    from heating import archives, backups
    from heating.models import Point, Sample, SampleArchive
    call_command('migrate', verbosity=0)
    user = User.objects.create_user('archive-test', password=None)
    call_command('bootstrap', verbosity=0)
    user.groups.set(Group.objects.filter(name='admin'))
    now = timezone.now()
    start = archives.block_start(now-timedelta(days=40))
    points = list(Point.objects.all())
    rows = [Sample(point=point, at=start+timedelta(seconds=30*i), value=float(i % 51),
                   unit='test', raw=str(i % 51), text='Synthetischer Testwert')
            for point in points for i in range(360)]
    Sample.objects.bulk_create(rows, batch_size=500)

    def storage_bytes():
        with connection.cursor() as cursor:
            cursor.execute("SELECT COALESCE(SUM(pgsize),0) FROM dbstat WHERE name IN "
                           "(SELECT name FROM sqlite_master WHERE tbl_name IN ('heating_sample','heating_samplearchive'))")
            return cursor.fetchone()[0]

    before = storage_bytes()
    while archives.compact_batch(limit=1024, now=now):
        pass
    # SQLite normally retains free pages for reuse. A compact backup measures
    # occupied storage separately from reusable free space in the live DB.
    meta = backups.snapshot('manual')
    backups.restore_test(meta['id'])
    after = storage_bytes()
    count = sum(len(list(archives.iter_samples(point, start, start+timedelta(hours=3)))) for point in points)
    assert count == len(rows)
    print(json.dumps({'synthetic_samples': count, 'blocks': SampleArchive.objects.count(),
        'raw_rows_remaining': Sample.objects.count(), 'measurement_tables_bytes_before': before,
        'measurement_tables_bytes_after': after, 'reduction_percent': round((1-after/before)*100, 1),
        'isolated_restore': 'passed'}, indent=2))
