from django.db import transaction
from heating.models import Point
POINTS = [
 ('wiese-outside','Au?entemperatur Wiesenegger','/121/10103/0/0/12197','Wiesenegger',False),
 ('wiese-flow-target','Vorlauf Soll Wiesenegger','/121/10103/0/0/12304','Wiesenegger',False),
 ('wiese-state','Heizkreiszustand Wiesenegger','/121/10103/0/0/12090','Wiesenegger',False),
 ('wiese-operation','Betriebsart Wiesenegger','/121/10103/0/0/12092','Wiesenegger',False),
 ('wiese-pump','Pumpenanforderung Wiesenegger','/121/10103/0/11124/2001','Wiesenegger',False),
 ]
with transaction.atomic():
    for key,label,uri,section,control in POINTS:
        p,created=Point.objects.get_or_create(key=key,defaults=dict(label=label,uri=uri,section=section,control=False))
        assert p.uri==uri and p.section==section and not p.control
        print(key, 'angelegt' if created else 'vorhanden')
