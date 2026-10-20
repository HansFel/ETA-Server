import time
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections
from django.utils import timezone
from heating.models import Job, Command as ControlCommand
from heating.services import collect, set_state
from heating.security import file_lock
from heating import backups, archives

class Command(BaseCommand):
    def handle(self, **options):
        with file_lock('worker', blocking=False):
            Job.objects.filter(status='running').update(status='failed', result='Worker wurde unterbrochen; erneut starten')
            ControlCommand.objects.filter(status='sending').update(status='uncertain', result='Unterbrochen: tatsächlichen Anlagenzustand prüfen; kein automatischer Neuversuch')
            next_poll = 0
            while True:
                try:
                    if time.monotonic() >= next_poll:
                        collect()
                        next_poll = time.monotonic() + settings.POLL_SECONDS
                    job = Job.objects.filter(status='pending').order_by('id').first()
                    if job:
                        # Global lock order: maintenance first, then backup, including requests.
                        with file_lock('maintenance', exclusive=job.kind == 'restore'), file_lock('backup'):
                            job.status = 'running'
                            job.save()
                            try:
                                if job.kind == 'backup':
                                    result = backups.snapshot('manual')['filename']
                                elif job.kind == 'test':
                                    result = 'Isolierter Restore-Test erfolgreich: ' + backups.restore_test(job.payload['id'])['filename']
                                elif job.kind == 'restore':
                                    result = 'Wiederhergestellt. Vor-Restore-Sicherung: ' + backups.restore(job.payload['id'])['filename']
                                else:
                                    raise ValueError('Unbekannter Auftrag')
                                Job.objects.filter(pk=job.pk).update(status='done', finished=timezone.now(), result=result[:300])
                            except Exception as exc:
                                Job.objects.filter(pk=job.pk).update(status='failed', finished=timezone.now(), result=str(exc)[:300])
                    with file_lock('maintenance', exclusive=False), file_lock('backup'):
                        backups.scheduled()
                    archives.scheduled()
                    set_state('worker', {'at': timezone.now().isoformat(), 'ok': True})
                except Exception as exc:
                    self.stderr.write('Worker: ' + str(exc)[:200])
                    set_state('worker', {'at': timezone.now().isoformat(), 'ok': False, 'error': str(exc)[:200]})
                finally:
                    connections.close_all()
                time.sleep(5)
