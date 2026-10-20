import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.db import connections, transaction
from django.utils import timezone
from .models import ChangeEvent, State, Audit
from .services import get_state, set_state
from .security import file_lock

def sha(path):
    with open(path, 'rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()

def root():
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', settings.INSTANCE_NAME):
        raise ValueError('Gültigen INSTANCE_NAME konfigurieren')
    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return settings.BACKUP_DIR.resolve()

def filename(kind, ident):
    stamp = timezone.now().strftime('%Y%m%dT%H%M%S%fZ')
    return f'eta_{settings.INSTANCE_NAME}_{kind}_backup_{stamp}_{ident}.sqlite3'

def save_manifest(meta):
    dest = root() / (meta['filename'] + '.json')
    temp = dest.with_suffix('.json.partial')
    with open(temp, 'w') as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, dest)

def manifests():
    items = []
    for path in root().glob('*.sqlite3.json'):
        try:
            value = json.loads(path.read_text())
            if value.get('app') == 'eta' and value.get('instance') == settings.INSTANCE_NAME:
                items.append(value)
        except (ValueError, OSError):
            continue
    return sorted(items, key=lambda v: v['created'], reverse=True)

def artifact(ident):
    meta = next((m for m in manifests() if m['id'] == str(ident)), None)
    if not meta:
        raise ValueError('Sicherung nicht gefunden')
    path = (root() / meta['filename']).resolve()
    if path.parent != root() or path.is_symlink() or not path.is_file():
        raise ValueError('Sicherungsdatei fehlt oder ungültiger Pfad')
    if sha(path) != meta['sha256']:
        raise ValueError('Prüfsumme stimmt nicht überein')
    return meta, path

def inspect_db(path):
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite-Integritätsprüfung fehlgeschlagen')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Ungültige Datenbankbeziehungen')
        installation = json.loads(db.execute("SELECT value FROM heating_state WHERE key='installation'").fetchone()[0])
        if installation.get('schema') != 1 or installation.get('instance') != settings.INSTANCE_NAME:
            raise ValueError('Schema oder Quellinstanz passt nicht zur Zielinstanz')
        migrations = set(db.execute('SELECT app,name FROM django_migrations'))
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute('SELECT app,name FROM django_migrations')
            if migrations != set(cursor.fetchall()):
                raise ValueError('Migrationsstand passt nicht zum laufenden Programm')
        counts = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in ('auth_user', 'heating_point', 'heating_sample', 'heating_samplearchive')}
        counts['archived_samples'] = db.execute('SELECT COALESCE(SUM(count),0) FROM heating_samplearchive').fetchone()[0]
        if not db.execute("SELECT 1 FROM auth_user u JOIN auth_user_groups ug ON ug.user_id=u.id JOIN auth_group g ON g.id=ug.group_id WHERE u.is_active=1 AND g.name='admin' LIMIT 1").fetchone():
            raise ValueError('Sicherung enthält keinen aktiven Administrator')
        return counts

def snapshot(kind='manual'):
    ident = uuid.uuid4().hex
    name = filename(kind, ident)
    path = root() / name
    partial = path.with_suffix('.sqlite3.partial')
    ids = list(ChangeEvent.objects.values_list('id', flat=True))
    try:
        with closing(sqlite3.connect(settings.DATABASES['default']['NAME'])) as source, closing(sqlite3.connect(partial)) as target:
            source.backup(target)
            # Removed raw rows leave reusable pages; do not copy that free space
            # into every permanent backup. This only compacts the backup file.
            target.execute('VACUUM')
        counts = inspect_db(partial)
        with open(partial, 'r+b') as handle:
            os.fsync(handle.fileno())
        os.replace(partial, path)
        meta = {'app': 'eta', 'instance': settings.INSTANCE_NAME, 'source_instance': settings.INSTANCE_NAME, 'id': ident, 'filename': name, 'kind': kind, 'created': timezone.now().isoformat(), 'sha256': sha(path), 'size': path.stat().st_size, 'status': 'verified', 'counts': counts, 'copy': 'not_configured', 'copy_error': '', 'restore_test': None}
        target = get_state('backup-policy').get('target', '')
        if target:
            meta.update(copy='pending', target=target, target_marker=get_state('backup-policy').get('target_marker', ''))
        save_manifest(meta)
        # Explicit IDs: writes committed during the snapshot remain pending.
        with transaction.atomic():
            for offset in range(0, len(ids), 500):
                ChangeEvent.objects.filter(id__in=ids[offset:offset+500]).delete()
            set_state('backup-last', {'at': meta['created'], 'id': ident, 'error': ''})
        return meta
    finally:
        partial.unlink(missing_ok=True)

def restore_test(ident):
    meta, path = artifact(ident)
    with tempfile.TemporaryDirectory(prefix='eta-restore-') as directory:
        destination = Path(directory) / 'restored.sqlite3'
        with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
        counts = inspect_db(destination)
        if counts != meta['counts']:
            raise ValueError('Zeilenvergleich beim Restore-Test fehlgeschlagen')
        from .archives import decode
        with closing(sqlite3.connect(f'file:{destination}?mode=ro', uri=True)) as restored:
            for payload, checksum, count in restored.execute('SELECT payload,checksum,count FROM heating_samplearchive'):
                decode(payload, checksum, count)
    meta['restore_test'] = timezone.now().isoformat()
    save_manifest(meta)
    return meta

def restore(ident):
    # Caller owns exclusive maintenance and backup lock: requests and logger writes stop.
    meta, path = artifact(ident)
    restore_test(ident)
    before = snapshot('pre-restore')
    connections.close_all()
    try:
        with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as source, closing(sqlite3.connect(settings.DATABASES['default']['NAME'])) as destination:
            source.backup(destination)
        if inspect_db(settings.DATABASES['default']['NAME']) != meta['counts']:
            raise ValueError('Datenvergleich nach Restore fehlgeschlagen')
    except Exception:
        _, rollback_path = artifact(before['id'])
        with closing(sqlite3.connect(f'file:{rollback_path}?mode=ro', uri=True)) as source, closing(sqlite3.connect(settings.DATABASES['default']['NAME'])) as destination:
            source.backup(destination)
        raise
    connections.close_all()
    from django.contrib.sessions.models import Session
    from .models import Command, AccessToken, Job
    Session.objects.all().delete()
    AccessToken.objects.all().delete()
    Command.objects.filter(status__in=['preview', 'sending']).update(status='cancelled', result='Nach Wiederherstellung verworfen')
    Job.objects.filter(status__in=['pending', 'running']).update(status='cancelled', result='Nach Wiederherstellung verworfen')
    policy = get_state('backup-policy')
    policy['enabled'] = False
    policy['next'] = None
    set_state('backup-policy', policy)
    set_state('controls', {'enabled': False})
    set_state('restore-last', {'at': timezone.now().isoformat(), 'id': ident, 'before': before['id']})
    Audit.objects.create(actor='worker', role='service', action='restore', detail={'backup': ident, 'before': before['id']})
    return before

def import_backup(upload):
    ident = uuid.uuid4().hex
    name = filename('incoming', ident)
    path = root() / name
    partial = path.with_suffix('.sqlite3.partial')
    try:
        size = 0
        with open(partial, 'xb') as handle:
            for chunk in upload.chunks():
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise ValueError('Upload größer als 512 MB')
                handle.write(chunk)
        counts = inspect_db(partial)
        os.replace(partial, path)
        meta = {'app': 'eta', 'instance': settings.INSTANCE_NAME, 'source_instance': settings.INSTANCE_NAME, 'id': ident, 'filename': name, 'kind': 'incoming', 'created': timezone.now().isoformat(), 'sha256': sha(path), 'size': size, 'status': 'verified', 'counts': counts, 'copy': 'not_configured', 'copy_error': '', 'restore_test': None}
        save_manifest(meta)
        return meta
    finally:
        partial.unlink(missing_ok=True)

def deliver(meta):
    _, source = artifact(meta['id'])
    target = Path(meta['target'])
    resolved = target.resolve()
    allowed = Path('/targets').resolve()
    if not resolved.is_relative_to(allowed) or resolved == allowed or not target.is_dir():
        raise ValueError('Ziel muss ein vorhandener Mount unter /targets sein')
    if not os.path.ismount(resolved):
        raise ValueError('Ziel ist nicht als eigener Datenträger eingebunden')
    marker = resolved / '.eta-target-id'
    if not meta.get('target_marker') or not marker.is_file() or marker.read_text().strip() != meta['target_marker']:
        raise ValueError('Identität des Sicherungsziels stimmt nicht')
    dest = resolved / meta['filename']
    if dest.exists():
        if sha(dest) != meta['sha256']:
            raise ValueError('Andere Datei unter demselben Zielnamen')
    else:
        partial = dest.with_suffix('.sqlite3.partial')
        with open(source, 'rb') as src, open(partial, 'wb') as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        if sha(partial) != meta['sha256']:
            raise ValueError('Ziel-Prüfsumme falsch')
        # No overwrite of a racing external writer; a target without hardlinks fails visibly.
        os.link(partial, dest)
        partial.unlink()
    meta.update(copy='verified', copy_error='', copied_at=timezone.now().isoformat())
    save_manifest(meta)

def delete_backup(ident):
    meta, path = artifact(ident)
    if meta['copy'] in ('pending', 'failed'):
        raise ValueError('Ausstehende Zielkopie: Original bleibt erhalten')
    if meta['copy'] == 'verified':
        deliver(meta)  # Revalidate/recreate the target copy before removing the original.
    path.unlink()
    (root() / (meta['filename'] + '.json')).unlink()

def due(policy, last, now, first=None, latest=None):
    if not policy.get('enabled'):
        return False
    elapsed = (now - last).total_seconds() if last else float('inf')
    time_due = elapsed >= policy['interval']
    event_due = first is not None and elapsed >= policy['minimum'] and ((now-first).total_seconds() >= policy['maximum'] or (now-latest).total_seconds() >= policy['quiet'])
    return (policy['mode'] in ('time', 'hybrid') and time_due) or (policy['mode'] in ('event', 'hybrid') and event_due)

def scheduled():
    from django.utils.dateparse import parse_datetime
    from django.db.models import Min, Max
    now = timezone.now()
    policy = get_state('backup-policy')
    previous = get_state('backup-last')
    last = parse_datetime(previous['at']) if previous.get('at') else None
    bounds = ChangeEvent.objects.aggregate(first=Min('created'), latest=Max('created'))
    retry = get_state('backup-retry').get('at')
    if due(policy, last, now, **bounds) and (not retry or now >= parse_datetime(retry)):
        try:
            snapshot(policy['mode'])
            set_state('backup-retry', {})
        except Exception as exc:
            set_state('backup-retry', {'at': (now+timedelta(minutes=5)).isoformat(), 'error': str(exc)[:200]})
    if policy.get('enabled') and policy['mode'] in ('time', 'hybrid'):
        latest = get_state('backup-last').get('at')
        policy['next'] = ((parse_datetime(latest) if latest else now) + timedelta(seconds=policy['interval'])).isoformat()
    else:
        policy['next'] = None
    set_state('backup-policy', policy)
    for meta in manifests():
        if meta['copy'] in ('pending', 'failed') and (not meta.get('retry_at') or now >= parse_datetime(meta['retry_at'])):
            try:
                deliver(meta)
            except Exception as exc:
                meta.update(copy='failed', copy_error=str(exc)[:200], retry_at=(now+timedelta(minutes=5)).isoformat())
                save_manifest(meta)
    # Retention only for verified own originals, never for pending external copies or uploads.
    originals = [m for m in manifests() if m['kind'] in ('time', 'event', 'hybrid', 'manual', 'pre-archive')]
    daily = {}
    for meta in originals:
        if now-parse_datetime(meta['created']) < timedelta(days=30):
            daily.setdefault(meta['created'][:10], meta['id'])
    for meta in originals[max(2, int(policy.get('keep', 30))):]:
        if meta['copy'] not in ('pending', 'failed') and meta['id'] not in daily.values() and meta['id'] != get_state('archive-safety').get('backup'):
            delete_backup(meta['id'])
