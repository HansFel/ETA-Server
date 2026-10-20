import os
from pathlib import Path
from django.conf import settings
from django.contrib.auth.models import User, Group
from django.core.management.base import BaseCommand
from django.db import transaction, connection
from heating.models import Point
from heating.services import set_state
from heating.rights import CRITICAL_KEYS

POINTS = [
 ('power','Kesselleistung','/24/10561/0/0/12320','Kessel',False),
 ('boiler','Kesseltemperatur','/24/10561/0/0/12161','Kessel',False),
 ('return','Rücklauf','/24/10561/0/0/12220','Kessel',False),
 ('flue','Abgastemperatur','/24/10561/0/0/12162','Kessel',False),
 ('status','Kesselzustand','/24/10561/0/0/12000','Kessel',False),
 ('hours','Volllaststunden','/24/10561/0/0/12153','Kessel',False),
 ('starts','Zündungen','/24/10561/0/0/12018','Kessel',False),
 ('outside','Außentemperatur','/121/10101/0/0/12197','HK Haus',False),
 ('flow','Vorlauf Haus','/121/10101/0/0/12241','HK Haus',False),
 ('buffer','Pufferladung','/120/10601/0/0/12528','Puffer',False),
 ('buffer-top','Puffer oben','/120/10601/0/0/13191','Puffer',False),
 ('buffer-bottom','Puffer unten','/120/10601/0/0/13192','Puffer',False),
 ('boiler-switch','Kessel ein / aus','/24/10561/0/0/12080','Kessel',True),
 ('house-auto','HK Haus Automatik','/121/10101/0/0/12126','HK Haus',True),
 ('curve-cold','Heizkurve bei −10 °C','/121/10101/0/0/12104','HK Haus',True),
 ('curve-warm','Heizkurve bei +10 °C','/121/10101/0/0/12103','HK Haus',True),
 ('ww-switch','WW Ein / Aus','/121/10111/0/0/12080','WW',True),
 ('ww-soll','WW Soll-Temperatur','/121/10111/0/0/12132','WW',True),
 ('ww-boost','Warmwasser sofort laden','/121/10111/0/0/12134','WW',True),
 ('ww-priority','WW Priorität','/121/10111/0/0/12770','WW',True),
 ('wiese-outside','Au?entemperatur Wiesenegger','/121/10103/0/0/12197','Wiesenegger',False),
 ('wiese-flow-target','Vorlauf Soll Wiesenegger','/121/10103/0/0/12304','Wiesenegger',False),
 ('wiese-state','Heizkreiszustand Wiesenegger','/121/10103/0/0/12090','Wiesenegger',False),
 ('wiese-operation','Betriebsart Wiesenegger','/121/10103/0/0/12092','Wiesenegger',False),
 ('wiese-pump','Pumpenanforderung Wiesenegger','/121/10103/0/11124/2001','Wiesenegger',False),
 ('wiese-switch','Wiesenegger ein / aus','/121/10103/0/0/12080','Wiesenegger',True),
 ('wiese-time-state','Wiesenegger Schaltzustand Heizzeiten','/121/10103/12113/0/1109','Wiesenegger',True),
 ('wiese-time-temp','Wiesenegger Temperatur Heizzeiten','/121/10103/12113/0/1110','Wiesenegger',True),
 ('wiese-time-mon','Wiesenegger Montag außerhalb Zeitfenster','/121/10103/12113/0/1111','Wiesenegger',True),
 ('wiese-time-mon-1','Wiesenegger Montag Zeitfenster 1','/121/10103/12113/0/1082','Wiesenegger',True),
 ('wiese-time-mon-2','Wiesenegger Montag Zeitfenster 2','/121/10103/12113/0/1083','Wiesenegger',True),
 ('wiese-time-mon-3','Wiesenegger Montag Zeitfenster 3','/121/10103/12113/0/1084','Wiesenegger',True),
 ('wiese-time-tue','Wiesenegger Dienstag außerhalb Zeitfenster','/121/10103/12113/0/1112','Wiesenegger',True),
 ('wiese-time-tue-1','Wiesenegger Dienstag Zeitfenster 1','/121/10103/12113/0/1086','Wiesenegger',True),
 ('wiese-time-tue-2','Wiesenegger Dienstag Zeitfenster 2','/121/10103/12113/0/1087','Wiesenegger',True),
 ('wiese-time-tue-3','Wiesenegger Dienstag Zeitfenster 3','/121/10103/12113/0/1088','Wiesenegger',True),
 ('wiese-time-wed','Wiesenegger Mittwoch außerhalb Zeitfenster','/121/10103/12113/0/1113','Wiesenegger',True),
 ('wiese-time-wed-1','Wiesenegger Mittwoch Zeitfenster 1','/121/10103/12113/0/1090','Wiesenegger',True),
 ('wiese-time-wed-2','Wiesenegger Mittwoch Zeitfenster 2','/121/10103/12113/0/1091','Wiesenegger',True),
 ('wiese-time-wed-3','Wiesenegger Mittwoch Zeitfenster 3','/121/10103/12113/0/1092','Wiesenegger',True),
 ('wiese-time-thu','Wiesenegger Donnerstag außerhalb Zeitfenster','/121/10103/12113/0/1114','Wiesenegger',True),
 ('wiese-time-thu-1','Wiesenegger Donnerstag Zeitfenster 1','/121/10103/12113/0/1094','Wiesenegger',True),
 ('wiese-time-thu-2','Wiesenegger Donnerstag Zeitfenster 2','/121/10103/12113/0/1095','Wiesenegger',True),
 ('wiese-time-thu-3','Wiesenegger Donnerstag Zeitfenster 3','/121/10103/12113/0/1096','Wiesenegger',True),
 ('wiese-time-fri','Wiesenegger Freitag außerhalb Zeitfenster','/121/10103/12113/0/1115','Wiesenegger',True),
 ('wiese-time-fri-1','Wiesenegger Freitag Zeitfenster 1','/121/10103/12113/0/1098','Wiesenegger',True),
 ('wiese-time-fri-2','Wiesenegger Freitag Zeitfenster 2','/121/10103/12113/0/1099','Wiesenegger',True),
 ('wiese-time-fri-3','Wiesenegger Freitag Zeitfenster 3','/121/10103/12113/0/1100','Wiesenegger',True),
 ('wiese-time-sat','Wiesenegger Samstag außerhalb Zeitfenster','/121/10103/12113/0/1116','Wiesenegger',True),
 ('wiese-time-sat-1','Wiesenegger Samstag Zeitfenster 1','/121/10103/12113/0/1102','Wiesenegger',True),
 ('wiese-time-sat-2','Wiesenegger Samstag Zeitfenster 2','/121/10103/12113/0/1103','Wiesenegger',True),
 ('wiese-time-sat-3','Wiesenegger Samstag Zeitfenster 3','/121/10103/12113/0/1104','Wiesenegger',True),
 ('wiese-time-sun','Wiesenegger Sonntag außerhalb Zeitfenster','/121/10103/12113/0/1117','Wiesenegger',True),
 ('wiese-time-sun-1','Wiesenegger Sonntag Zeitfenster 1','/121/10103/12113/0/1106','Wiesenegger',True),
 ('wiese-time-sun-2','Wiesenegger Sonntag Zeitfenster 2','/121/10103/12113/0/1107','Wiesenegger',True),
 ('wiese-time-sun-3','Wiesenegger Sonntag Zeitfenster 3','/121/10103/12113/0/1108','Wiesenegger',True),
 ('wiese-auto','Wiesenegger Automatik','/121/10103/0/0/12126','Wiesenegger',True),
 ('wiese-curve-cold','Heizkurve bei −10 °C','/121/10103/0/0/12104','Wiesenegger',True),
 ('wiese-curve-warm','Heizkurve bei +10 °C','/121/10103/0/0/12103','Wiesenegger',True),
 ('werkstatt-switch','Werkstatt ein / aus','/121/10102/0/0/12080','Werkstatt',True),
 ('werkstatt-auto','Werkstatt Automatik','/121/10102/0/0/12126','Werkstatt',True),
 ('puffer-min','Puffer min.','/120/10601/0/0/13195','Puffer',True),
]

class Command(BaseCommand):
    def handle(self, **options):
        with transaction.atomic():
            admin_group = Group.objects.get_or_create(name='admin')[0]
            if not User.objects.exists():
                password = Path(os.environ['BOOTSTRAP_PASSWORD_FILE']).read_text().strip()
                user = User.objects.create_user(os.getenv('BOOTSTRAP_USER', 'admin'), password=password)
                user.groups.set([admin_group])
            for key, label, uri, section, control in POINTS:
                point, created = Point.objects.update_or_create(key=key, defaults={'label': label, 'uri': uri, 'section': section, 'control': control})
                if created:
                    # nur beim Anlegen; spätere Einstellungen des Administrators bleiben
                    point.confirm = key in CRITICAL_KEYS
                    point.mqtt = control and not point.confirm
                    point.save(update_fields=['confirm', 'mqtt'])
            from heating.models import State
            if not State.objects.filter(pk='backup-policy').exists():
                set_state('backup-policy', {'enabled': True, 'mode': 'time', 'interval': 86400, 'quiet': 120, 'minimum': 3600, 'maximum': 3600, 'keep': 30, 'target': '', 'target_marker': '', 'next': None})
            State.objects.get_or_create(key='installation', defaults={'value': {'instance': settings.INSTANCE_NAME, 'schema': 1}})
        # SQLite triggers also cover direct SQL and bulk operations, within the same commit.
        tables = ['heating_sample', 'heating_samplearchive', 'heating_point', 'auth_user', 'auth_user_groups', 'heating_grant', 'heating_command']
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode=WAL')
            for table in tables:
                for operation in ('INSERT', 'UPDATE', 'DELETE'):
                    cursor.execute(f"CREATE TRIGGER IF NOT EXISTS evt_{table}_{operation} AFTER {operation} ON {table} BEGIN INSERT INTO heating_changeevent(source,created) VALUES ('{table}',strftime('%Y-%m-%d %H:%M:%f','now')); END")
        self.stdout.write('Instanz und Datenpunkte bereit. Vorhandene Konten bleiben unverändert.')
