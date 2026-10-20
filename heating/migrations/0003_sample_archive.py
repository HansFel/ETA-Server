from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('heating', '0002_person_grants')]
    operations = [
        migrations.CreateModel(
            name='SampleArchive',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start', models.DateTimeField()),
                ('count', models.PositiveIntegerField()),
                ('summary', models.JSONField(default=dict)),
                ('payload', models.BinaryField()),
                ('checksum', models.CharField(max_length=64)),
                ('point', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='heating.point')),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('point', 'start'), name='unique_sample_archive')]},
        ),
        migrations.AddIndex(model_name='sample', index=models.Index(fields=['at'], name='sample_archive_age_idx')),
    ]
