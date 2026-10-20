from django.core.management.base import BaseCommand
from heating import mqtt

class Command(BaseCommand):
    help = 'Gibt Topic und Payload aller Schaltpunkte als Markdown aus (aus den ETA-Datenpunktinformationen).'

    def add_arguments(self, parser):
        parser.add_argument('--device', default='<geraet>', help='MQTT-Benutzer des Geräts für die Topics')

    def handle(self, **options):
        self.stdout.write(mqtt.markdown(options['device']))
