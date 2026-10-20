from django.db import migrations, models


def default_mqtt(apps, schema_editor):
    # Schaltpunkte ohne Rückfrage sind über MQTT freigegeben, wichtige Punkte (mit Rückfrage) zunächst nicht.
    Point = apps.get_model('heating', 'Point')
    Point.objects.filter(control=True, confirm=False).update(mqtt=True)


class Migration(migrations.Migration):
    dependencies = [('heating', '0004_control_detail')]

    operations = [
        migrations.AddField(model_name='point', name='mqtt', field=models.BooleanField(default=False)),
        migrations.RunPython(default_mqtt, migrations.RunPython.noop),
    ]
