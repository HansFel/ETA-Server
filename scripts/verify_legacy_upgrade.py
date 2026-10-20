"""Isolated schema 0001 -> 0003 test with synthetic accounts and measurements."""
import os
import sys
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
with tempfile.TemporaryDirectory(prefix='eta-legacy-upgrade-') as directory:
    os.environ.pop('SECRET_KEY_FILE', None)
    os.environ.update(DJANGO_SETTINGS_MODULE='config.settings', SECRET_KEY='isolated-upgrade-test',
                      DATA_DIR=directory, BACKUP_DIR=directory+'/backups', INSTANCE_NAME='legacy-test')
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.contrib.auth.models import User, Group
    from django.utils import timezone
    call_command('migrate', verbosity=0)
    executor = MigrationExecutor(connection)
    executor.migrate([('heating', '0001_initial')])
    old_apps = executor.loader.project_state([('heating', '0001_initial')]).apps
    OldPoint = old_apps.get_model('heating', 'Point')
    OldSample = old_apps.get_model('heating', 'Sample')
    groups = {name: Group.objects.create(name=name) for name in ('admin', 'operator', 'reader')}
    expected = {}
    for name, memberships in [('admin', ['admin']), ('operator', ['operator']),
                              ('reader', ['reader']), ('both', ['reader', 'operator']), ('none', [])]:
        user = User.objects.create_user(name, password='synthetic-upgrade-password-817')
        user.groups.set([groups[g] for g in memberships])
        expected[name] = (user.pk, user.password)
    point = OldPoint.objects.create(key='boiler-switch', uri='/test/1', label='Test', section='Kessel', control=True)
    sample = OldSample.objects.create(point=point, at=timezone.now(), value=12.5, raw='125', unit='test', quality='ok')
    # Checked pre-migration copy of the isolated fixture, never a live database.
    with sqlite3.connect(str(Path(directory)/'eta.sqlite3')) as source, sqlite3.connect(str(Path(directory)/'before.sqlite3')) as target:
        source.backup(target)
        assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert target.execute('SELECT COUNT(*) FROM heating_sample').fetchone()[0] == 1
    call_command('migrate', verbosity=0)
    from heating.models import Grant, Point, Sample
    from heating.rights import SECTIONS
    for user in User.objects.all():
        assert (user.pk, user.password) == expected[user.username]
        grants = dict(Grant.objects.filter(user=user).values_list('section', 'level'))
        level = {'operator': 'control', 'reader': 'view', 'both': 'control'}.get(user.username)
        assert grants == ({section: level for section in SECTIONS} if level else {})
    assert User.objects.get(username='admin').groups.filter(name='admin').exists()
    assert not Group.objects.filter(name__in=['operator', 'reader']).exists()
    assert Point.objects.get(pk=point.pk).confirm
    retained = Sample.objects.get(pk=sample.pk)
    assert (retained.value, retained.raw, retained.unit, retained.quality) == (12.5, '125', 'test', 'ok')
    call_command('bootstrap', verbosity=0)
    from heating import backups
    meta = backups.snapshot('manual')
    assert backups.restore_test(meta['id'])['restore_test']
    print('PASS: 0001 -> 0003; user IDs/password hashes, admin, 6 area grants, measurements, confirmations and isolated restore.')
