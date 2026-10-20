"""Run via stdin in the OLD worker, before replacing its image or migrating.

docker compose exec -T worker python - < scripts/pre_archive_update_backup.py
"""
import os
import shutil

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.conf import settings
from heating import backups
from heating.security import file_lock

with file_lock('maintenance'), file_lock('backup'):
    meta = backups.snapshot('pre-update')
    backups.restore_test(meta['id'])
    _, source = backups.artifact(meta['id'])
    destination = settings.BACKUP_DIR / ('rollback-archive-' + meta['id'])
    destination.mkdir(exist_ok=False)
    copied = destination / source.name
    shutil.copy2(source, copied)
    shutil.copy2(source.with_name(source.name + '.json'), destination)
    if backups.sha(copied) != meta['sha256']:
        raise RuntimeError('Rollback-Kopie stimmt nicht mit der Sicherung überein')
    print('Geprüfte Vor-Update-Sicherung und isolierter Restore-Test erfolgreich.')
    print('Geschützte Rollback-Kopie:', destination)
