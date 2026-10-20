from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class Point(models.Model):
    key = models.SlugField(unique=True)
    label = models.CharField(max_length=120)
    uri = models.CharField(max_length=120, unique=True)
    section = models.CharField(max_length=80)
    enabled = models.BooleanField(default=True)
    control = models.BooleanField(default=False)
    confirm = models.BooleanField(default=False)
    mqtt = models.BooleanField(default=False)  # über MQTT schaltbar (Administrator legt fest)
    metadata = models.JSONField(default=dict)
    latest = models.JSONField(default=dict)
    updated = models.DateTimeField(null=True)
    class Meta:
        ordering = ['id']

class Sample(models.Model):
    point = models.ForeignKey(Point, on_delete=models.CASCADE)
    at = models.DateTimeField(default=timezone.now)
    value = models.FloatField(null=True)
    raw = models.CharField(max_length=120, blank=True)
    text = models.CharField(max_length=200, blank=True)
    unit = models.CharField(max_length=30, blank=True)
    quality = models.CharField(max_length=30, default='ok')
    class Meta:
        indexes = [models.Index(fields=['point', 'at']), models.Index(fields=['at'], name='sample_archive_age_idx')]

class State(models.Model):
    key = models.CharField(max_length=80, primary_key=True)
    value = models.JSONField(default=dict)

class SampleArchive(models.Model):
    """Five-minute UTC blocks; compressed originals retain exact historical queries."""
    point = models.ForeignKey(Point, on_delete=models.CASCADE)
    start = models.DateTimeField()
    count = models.PositiveIntegerField()
    summary = models.JSONField(default=dict)
    payload = models.BinaryField()
    checksum = models.CharField(max_length=64)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['point', 'start'], name='unique_sample_archive')]

class ChangeEvent(models.Model):
    source = models.CharField(max_length=100)
    created = models.DateTimeField(default=timezone.now)

class Audit(models.Model):
    at = models.DateTimeField(default=timezone.now)
    actor = models.CharField(max_length=150)
    role = models.CharField(max_length=30)
    action = models.CharField(max_length=100)
    detail = models.JSONField(default=dict)
    class Meta:
        ordering = ['-id']

class Command(models.Model):
    id = models.UUIDField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    role = models.CharField(max_length=30)
    point = models.ForeignKey(Point, on_delete=models.PROTECT)
    raw = models.CharField(max_length=80)
    before = models.CharField(max_length=80)
    created = models.DateTimeField(default=timezone.now)
    expires = models.DateTimeField()
    status = models.CharField(max_length=30, default='preview')
    result = models.CharField(max_length=250, blank=True)

class AccessToken(models.Model):
    digest = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    purpose = models.CharField(max_length=10)
    expires = models.DateTimeField()
    used = models.BooleanField(default=False)

class RateLimit(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    since = models.DateTimeField(default=timezone.now)

class Job(models.Model):
    kind = models.CharField(max_length=20)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='pending')
    created = models.DateTimeField(default=timezone.now)
    finished = models.DateTimeField(null=True)
    result = models.CharField(max_length=300, blank=True)

class Grant(models.Model):
    """Recht einer Person auf einen Bereich: 'view' = sehen, 'control' = sehen und schalten."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='grants')
    section = models.CharField(max_length=80)
    level = models.CharField(max_length=10, choices=[('view', 'Sehen'), ('control', 'Schalten')])
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'section'], name='one_grant_per_section')]

class ControlDetail(models.Model):
    """Detailprofil für Schaltrechte innerhalb eines Bereichs."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='control_details')
    section = models.CharField(max_length=80)
    profile = models.CharField(max_length=40)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'section'], name='one_control_detail_per_section')]
