import json
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth.models import Group
from django.utils import timezone
from .tests import AppTestBase
from .models import Point, Sample
from . import mqtt_sensors, mqtt


class MqttSensorTests(AppTestBase):
    def pair_snapshot(self, fw=(60, 40), house=(35, 38)):
        rows = []
        for index, (topic, value) in enumerate(zip(['vorlauf-fw', 'retour-fw', 'haus-vorlauf', 'haus-retour'], [*fw, *house])):
            rows.append({'id': 'ds_pair_%d' % index, 'name': topic, 'topic': 'etaio/esp-test/'+topic+'/temperature', 'temperature': value})
        return rows

    def test_differences_calculated_from_raw_history_without_storage(self):
        mqtt_sensors.ingest(self.topic, json.dumps(self.pair_snapshot()).encode())
        fw = Point.objects.get(uri='mqtt:esp-test:delta-fw')
        house = Point.objects.get(uri='mqtt:esp-test:delta-haus')
        self.assertFalse(Sample.objects.filter(point__in=[fw, house]).exists())
        self.assertNotIn('value', fw.latest)
        from .graph_samples import graph_samples
        start, end = timezone.now()-timedelta(hours=1), timezone.now()
        self.assertEqual(list(graph_samples(fw, start, end))[-1].value, 20)
        self.assertEqual(list(graph_samples(house, start, end))[-1].value, -3)
        mqtt_sensors.ingest(self.topic, json.dumps(self.pair_snapshot((61, 40))).encode())
        self.assertFalse(Sample.objects.filter(point__in=[fw, house]).exists())
        self.assertEqual(fw.metadata['unit'], '°C')

    def test_graph_difference_invalid_stale_and_archived_inputs(self):
        mqtt_sensors.ingest(self.topic, json.dumps(self.pair_snapshot()).encode())
        fw = Point.objects.get(uri='mqtt:esp-test:delta-fw')
        from .graph_samples import graph_samples, inputs
        sources = inputs(fw)
        start, end = timezone.now()-timedelta(hours=1), timezone.now()
        Sample.objects.filter(point=sources[1]).update(quality='offline', value=None)
        self.assertIsNone(list(graph_samples(fw, start, end))[-1].value)
        Sample.objects.filter(point=sources[1]).update(quality='ok', value=40, at=start+timedelta(seconds=1))
        self.assertIsNone(list(graph_samples(fw, start, end))[-1].value)
        # Shared archive reader is used for BOTH raw input series.
        from types import SimpleNamespace
        at=timezone.now()-timedelta(seconds=1)
        with patch('heating.graph_samples.iter_samples', side_effect=[[SimpleNamespace(at=at, value=65, quality='ok')], [SimpleNamespace(at=at, value=45, quality='ok')]]):
            self.assertEqual(list(graph_samples(fw, start, end))[-1].value, 20)

    def test_graph_only_differences_follow_area_permissions(self):
        mqtt_sensors.ingest(self.topic, json.dumps(self.pair_snapshot()).encode())
        fw = Point.objects.get(uri='mqtt:esp-test:delta-fw')
        _, allowed = self.person('difference-reader', Wiesenegger='view')
        _, denied = self.person('difference-denied', Kessel='view')
        from django.urls import reverse
        response = allowed.get(reverse('history'), {'point': fw.key})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(v['value'] == 20 for v in response.json()['series'][0]['values']))
        self.assertEqual(denied.get(reverse('history'), {'point': fw.key}).status_code, 403)
        self.assertEqual(allowed.get(reverse('export'), {'point': fw.key}).status_code, 400)
        self.assertFalse(any(p['key'] == fw.key for p in allowed.get(reverse('live')).json()['points']))
        self.assertNotIn('eta/state/'+fw.key, mqtt.public_states())

    def setUp(self):
        super().setUp()
        self.device, _ = self.person('esp-test', Wiesenegger='view')
        self.device.groups.add(Group.objects.get_or_create(name='mqtt')[0])
        mqtt_sensors.configure(self.device.username, 'Wiesenegger')
        self.topic = 'etaio/esp-test/sensors'

    def ingest(self, value=21.5, name='ESP32-Testfühler', retained=False, sensor_id='ds_123', topic=None):
        return mqtt_sensors.ingest(self.topic, json.dumps([{'id': sensor_id, 'name': name,
            'topic': topic or 'etaio/esp-test/vorlauf/temperature', 'temperature': value}]).encode(), retained)

    def test_logging_throttle_and_sensor_identity(self):
        self.assertEqual(self.ingest(), 1)
        point = Point.objects.get(uri__startswith='mqtt:')
        self.assertEqual(point.latest['value'], 21.5)
        self.assertEqual(point.section, 'Wiesenegger')
        self.ingest(22, name='Neuer Name')
        point.refresh_from_db()
        self.assertEqual(point.label, 'Neuer Name')
        self.assertEqual(point.latest['value'], 22)
        self.assertEqual(Sample.objects.filter(point=point).count(), 1)
        Sample.objects.filter(point=point).update(at=timezone.now()-timedelta(seconds=31))
        self.ingest(23)
        self.assertEqual(Sample.objects.filter(point=point).count(), 2)
        self.assertEqual(Point.objects.filter(uri__startswith='mqtt:').count(), 1)

    def test_retained_unknown_device_and_foreign_topics_ignored(self):
        self.assertEqual(self.ingest(retained=True), 0)
        self.assertEqual(self.ingest(topic='etaio/other/vorlauf/temperature'), 0)
        self.assertEqual(mqtt_sensors.ingest('etaio/unknown/sensors', b'[]'), 0)
        self.assertEqual(mqtt_sensors.ingest(self.topic, b'not-json'), 0)
        self.assertFalse(Point.objects.filter(uri__startswith='mqtt:').exists())
        self.device.is_active = False
        self.device.save()
        self.assertEqual(self.ingest(), 0)

    def test_invalid_readings_are_gaps_and_recovery_is_logged(self):
        self.ingest()
        self.ingest(None)
        point = Point.objects.get(uri__startswith='mqtt:')
        self.assertIsNone(point.latest['value'])
        self.assertEqual(point.latest['quality'], 'offline')
        self.ingest(0)
        point.refresh_from_db()
        self.assertEqual(point.latest['value'], 0)
        self.assertEqual(Sample.objects.filter(point=point).count(), 3)
        self.ingest(10**300)
        point.refresh_from_db()
        self.assertIsNone(point.latest['value'])

    def test_permissions_cover_dashboard_live_history_export_and_mqtt(self):
        self.ingest()
        point = Point.objects.get(uri__startswith='mqtt:')
        _, allowed = self.person('sensor-reader', Wiesenegger='view')
        _, denied = self.person('other-reader', Kessel='view')
        self.assertContains(allowed.get('/dashboard/'), point.label)
        self.assertNotContains(denied.get('/dashboard/'), point.label)
        self.assertTrue(any(p['key'] == point.key for p in allowed.get('/api/live/').json()['points']))
        self.assertFalse(any(p['key'] == point.key for p in denied.get('/api/live/').json()['points']))
        from django.urls import reverse
        for name in ['history', 'export']:
            self.assertEqual(allowed.get(reverse(name), {'point': point.key}).status_code, 200)
            self.assertEqual(denied.get(reverse(name), {'point': point.key}).status_code, 403)
        self.assertNotIn('eta/state/'+point.key, mqtt.public_states())
        self.assertNotIn(point.key, str(mqtt.visible_states('unknown')))
        self.assertIn(point.key, str(mqtt.visible_states('esp-test')))

    def test_admin_mapping_and_unassign(self):
        self.ingest()
        response = self.client.post('/mqtt/', {'action': 'sensor-section', 'sensor-device': 'esp-test', 'section': 'Werkstatt'})
        self.assertEqual(response.status_code, 302)
        point = Point.objects.get(uri__startswith='mqtt:')
        self.assertEqual(point.section, 'Werkstatt')
        mqtt_sensors.configure('esp-test', '')
        self.assertEqual(self.ingest(), 0)
        point.refresh_from_db()
        self.assertNotIn(point.section, mqtt_sensors.SECTIONS)

    @patch('heating.services.EtaClient')
    def test_eta_collector_does_not_read_mqtt_sensor_uris(self, mock):
        self.ingest()
        Point.objects.exclude(uri__startswith='mqtt:').update(enabled=False)
        mock.return_value.errors.return_value = []
        from .services import collect
        collect()
        mock.return_value.read.assert_not_called()
        self.assertEqual(Sample.objects.filter(point__uri__startswith='mqtt:').count(), 1)
