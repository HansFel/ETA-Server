import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import Client, TransactionTestCase, override_settings
from django.db.models.query import QuerySet

from . import archives, backups
from .models import Grant, Point, Sample, SampleArchive
from .services import get_state
from .views import power_stats


class ArchiveTests(TransactionTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.override = override_settings(DATA_DIR=Path(self.temp.name), BACKUP_DIR=Path(self.temp.name)/'backups',
            INSTANCE_NAME='archive-test', ARCHIVE_AFTER_DAYS=30, ARCHIVE_ENABLED=True,
            ALLOWED_HOSTS=['testserver'], SCRIPT_NAME='')
        self.override.enable()
        self.user = User.objects.create_user('admin', password='test-archive-password-817')
        call_command('bootstrap', verbosity=0)
        self.user.groups.set(Group.objects.filter(name='admin'))
        self.client.force_login(self.user)
        # Around Vienna's repeated autumn hour; storage always uses UTC.
        self.start = datetime(2025, 10, 26, 0, 59, tzinfo=dt_timezone.utc)
        self.now = self.start + timedelta(days=31)
        self.power = Point.objects.get(key='power')
        self.counter = Point.objects.get(key='starts')

    def tearDown(self):
        self.override.disable()
        self.temp.cleanup()

    def samples(self):
        for i, value in enumerate([0, 10, 20, None, 30, 0, 15, 25, 5, 0, 10, 12]):
            Sample.objects.create(point=self.power, at=self.start+timedelta(seconds=30*i), value=value,
                quality='offline' if value is None else 'ok', unit='kW', raw='' if value is None else str(value), text='Messung')
            Sample.objects.create(point=self.counter, at=self.start+timedelta(seconds=30*i), value=100+i if i<6 else i)

    def test_exact_stats_history_export_and_repeat(self):
        self.samples()
        end = self.start + timedelta(minutes=7)
        args = {'start': self.start.isoformat(), 'end': end.isoformat(), 'point': 'power'}
        stats = power_stats(self.start, end)
        history = self.client.get('/api/history/', args).json()
        export = self.client.get('/export/', args).content
        expected = [(r.at, r.value, r.quality, r.raw, r.text, r.unit) for r in archives.iter_samples(self.power, self.start, end)]
        self.assertEqual(archives.compact_batch(now=self.now), 24)
        self.assertEqual(Sample.objects.count(), 0)
        self.assertEqual(archives.compact_batch(now=self.now), 0)
        self.assertEqual(power_stats(self.start, end), stats)
        self.assertEqual(self.client.get('/api/history/', args).json(), history)
        self.assertEqual(self.client.get('/export/', args).content, export)
        actual = [(r.at, r.value, r.quality, r.raw, r.text, r.unit) for r in archives.iter_samples(self.power, self.start, end)]
        self.assertEqual(actual, expected)
        storage = archives.storage_stats()
        self.assertEqual(storage['total'], 24)
        self.assertEqual(storage['archived'], 24)
        self.assertContains(self.client.get('/admin/'), '5-Minuten')

    def test_boundary_late_insert_and_live_merge(self):
        self.samples()
        left, right = self.start+timedelta(seconds=17), self.start+timedelta(minutes=4, seconds=43)
        expected = power_stats(left, right)
        archives.compact_batch(now=self.now, limit=1)
        self.assertEqual(power_stats(left, right), expected)
        Sample.objects.create(point=self.power, at=self.start+timedelta(seconds=11), value=7, unit='kW')
        archives.compact_batch(now=self.now)
        self.assertEqual(len(list(archives.iter_samples(self.power, self.start, right))), 11)
        # Never archive the partially expired five-minute block.
        at = archives.block_start(self.now-timedelta(days=30))+timedelta(seconds=1)
        recent = Sample.objects.create(point=self.power, at=at, value=3)
        archives.compact_batch(now=self.now)
        self.assertTrue(Sample.objects.filter(pk=recent.pk).exists())

    def test_backup_failure_and_corrupt_encoding_leave_raw_rows(self):
        self.samples()
        with patch('heating.backups.restore_test', side_effect=ValueError('restore failed')):
            with self.assertRaises(ValueError):
                archives.compact_batch(now=self.now)
        self.assertEqual(Sample.objects.count(), 24)
        self.assertEqual(SampleArchive.objects.count(), 0)
        with patch('heating.archives.encode', return_value=(b'corrupt', 'wrong')):
            with self.assertRaises(ValueError):
                archives.compact_batch(now=self.now)
        self.assertEqual(Sample.objects.count(), 24)
        self.assertEqual(SampleArchive.objects.count(), 0)

    def test_isolated_restore_checks_archived_payloads(self):
        self.samples()
        archives.compact_batch(now=self.now)
        meta = backups.snapshot('manual')
        self.assertEqual(meta['counts']['archived_samples'], 24)
        self.assertTrue(backups.restore_test(meta['id'])['restore_test'])
        block = SampleArchive.objects.first()
        block.payload = b'bad'
        block.save()
        broken = backups.snapshot('manual')
        with self.assertRaises(ValueError):
            backups.restore_test(broken['id'])

    def test_archive_permissions_and_revocation(self):
        self.samples()
        archives.compact_batch(now=self.now)
        user = User.objects.create_user('reader', password='test-reader-password-817')
        grant = Grant.objects.create(user=user, section='Kessel', level='view')
        client = Client()
        client.force_login(user)
        args = {'start': self.start.isoformat(), 'end': (self.start+timedelta(minutes=8)).isoformat(), 'point': 'power'}
        self.assertEqual(client.get('/api/history/', args).status_code, 200)
        self.assertEqual(client.get('/admin/').status_code, 403)
        grant.delete()
        self.assertNotEqual(client.get('/api/history/', args).status_code, 200)
        self.assertNotEqual(client.get('/export/', args).status_code, 200)
        self.assertNotEqual(Client().get('/api/history/', args).status_code, 200)

    def test_worker_failure_status_and_backoff(self):
        with patch('heating.archives.compact_batch', side_effect=ValueError('test failure')) as compact:
            archives.scheduled()
            archives.scheduled()
        self.assertEqual(compact.call_count, 1)
        self.assertFalse(get_state('archive-status')['ok'])

    def test_transaction_failure_restores_raw_and_archive(self):
        self.samples()
        original = QuerySet.delete
        def fail_after_delete(query):
            result = original(query)
            if query.model is Sample:
                raise RuntimeError('simulated interruption after delete')
            return result
        with patch.object(QuerySet, 'delete', fail_after_delete):
            with self.assertRaises(RuntimeError):
                archives.compact_batch(now=self.now)
        self.assertEqual(Sample.objects.count(), 24)
        self.assertEqual(SampleArchive.objects.count(), 0)
        self.assertEqual(archives.compact_batch(now=self.now), 24)

    def test_summary_keeps_units_invalids_and_extrema(self):
        for i, (value, unit, quality) in enumerate([(2, 'kW', 'ok'), (8, 'kW', 'ok'),
                (None, 'kW', 'offline'), (5000, 'W', 'ok')]):
            Sample.objects.create(point=self.power, at=self.start+timedelta(seconds=i), value=value, unit=unit, quality=quality)
        archives.compact_batch(now=self.now)
        units = SampleArchive.objects.get().summary['units']
        self.assertEqual(units['kW'], {'valid': 2, 'invalid': 1, 'min': 2, 'max': 8, 'mean': 5})
        self.assertEqual(units['W']['mean'], 5000)

    def test_disabled_worker_does_not_archive(self):
        self.samples()
        with override_settings(ARCHIVE_ENABLED=False), patch('heating.archives.compact_batch') as compact:
            archives.scheduled()
        compact.assert_not_called()
        self.assertEqual(Sample.objects.count(), 24)
