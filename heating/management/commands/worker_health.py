from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from heating.services import get_state
class Command(BaseCommand):
    def handle(self, **options):
        state = get_state('worker')
        if not state.get('ok') or (timezone.now()-parse_datetime(state['at'])).total_seconds() > 120:
            raise CommandError('Worker-Heartbeat fehlt oder ist fehlerhaft')
