from django.core.management.base import BaseCommand
from heating.archives import compact_batch


class Command(BaseCommand):
    help = 'Verdichtet einen begrenzten Stapel alter Messwerte nach geprüfter Sicherung.'

    def add_arguments(self, parser):
        parser.add_argument('--blocks', type=int, default=64)

    def handle(self, **options):
        count = compact_batch(limit=max(1, min(options['blocks'], 1024)))
        self.stdout.write(f'{count} Messwerte verlustfrei archiviert.')
