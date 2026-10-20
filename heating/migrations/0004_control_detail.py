from django.db import migrations, models
import django.db.models.deletion


def seed_wiesenegger_details(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Grant = apps.get_model('heating', 'Grant')
    ControlDetail = apps.get_model('heating', 'ControlDetail')
    Group = apps.get_model('auth', 'Group')

    admin_group = Group.objects.filter(name='admin').first()
    admin_user_ids = set()
    if admin_group:
        admin_user_ids = set(admin_group.user_set.values_list('id', flat=True))

    user_ids = Grant.objects.filter(section='Wiesenegger', level='control').values_list('user_id', flat=True).distinct()
    for user in User.objects.filter(id__in=user_ids):
        if user.id in admin_user_ids:
            continue
        control_sections = list(Grant.objects.filter(user_id=user.id, level='control').values_list('section', flat=True))
        profile = 'switch_schedule' if control_sections == ['Wiesenegger'] else 'full'
        ControlDetail.objects.update_or_create(user_id=user.id, section='Wiesenegger', defaults={'profile': profile})


class Migration(migrations.Migration):
    dependencies = [('heating', '0003_sample_archive')]

    operations = [
        migrations.CreateModel(
            name='ControlDetail',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('section', models.CharField(max_length=80)),
                ('profile', models.CharField(max_length=40)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='control_details', to='auth.user')),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('user', 'section'), name='one_control_detail_per_section')]},
        ),
        migrations.RunPython(seed_wiesenegger_details, migrations.RunPython.noop),
    ]
