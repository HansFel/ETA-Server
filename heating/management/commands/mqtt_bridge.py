import json
import queue
import time
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections
from django.utils import timezone
from heating import mqtt as bridge
from heating import mqtt_sensors
from heating.services import get_state, set_state

class Command(BaseCommand):
    help = 'MQTT-Brücke: Schaltbefehle von Geräten annehmen, Ergebnisse und Messwerte veröffentlichen.'

    def handle(self, **options):
        if not settings.MQTT_HOST:
            # Ohne Broker ruhig warten statt ständig neu zu starten.
            set_state('mqtt', {'at': timezone.now().isoformat(), 'enabled': False})
            self.stdout.write('MQTT_HOST nicht gesetzt: Brücke ist ausgeschaltet.')
            while True:
                time.sleep(3600)
        import paho.mqtt.client as paho
        inbox = queue.Queue(maxsize=200)
        client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id='eta-app-' + (settings.INSTANCE_NAME or 'eta'), protocol=paho.MQTTv311)
        client.username_pw_set(settings.MQTT_USER, settings.MQTT_PASSWORD)
        client.will_set('eta/app/availability', 'offline', qos=1, retain=True)
        client.reconnect_delay_set(2, 60)

        def on_connect(c, userdata, flags, reason, properties):
            if reason.is_failure:
                self.stderr.write('MQTT-Verbindung abgelehnt: ' + str(reason))
                return
            c.subscribe(bridge.COMMAND_TOPIC, qos=1)
            c.subscribe(mqtt_sensors.SENSOR_TOPIC, qos=1)
            c.publish('eta/app/availability', 'online', qos=1, retain=True)
            published.clear()  # nach Neuverbindung alle Zustände erneut senden

        def on_message(c, userdata, message):
            try:
                inbox.put_nowait((message.topic, message.payload, message.retain))
            except queue.Full:
                pass  # Überlauf: lieber verwerfen als veraltete Befehle später ausführen

        published = {}
        client.on_connect, client.on_message = on_connect, on_message
        client.connect_async(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=30)
        client.loop_start()
        next_states = 0
        try:
            while True:
                try:
                    topic, payload, retained = inbox.get(timeout=1)
                    try:
                        if topic.startswith('etaio/'):
                            mqtt_sensors.ingest(topic, payload, retained)
                            continue
                        answer = bridge.handle_command(topic, payload, retained)
                        if answer:
                            client.publish(answer[0], json.dumps(answer[1], ensure_ascii=False), qos=1, retain=False)
                        set_state('mqtt', {**get_state('mqtt'), 'last_command': timezone.now().isoformat()})
                    except Exception as exc:
                        self.stderr.write('MQTT-Befehl: ' + str(exc)[:200])
                    finally:
                        connections.close_all()
                except queue.Empty:
                    pass
                if time.monotonic() >= next_states:
                    next_states = time.monotonic() + 10
                    try:
                        if client.is_connected():
                            current = bridge.public_states() if settings.MQTT_PUBLISH_ALL else {}
                            for device in bridge.devices():
                                current.update(bridge.visible_states(device))
                            for topic, payload in current.items():
                                body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                                if published.get(topic) != body:
                                    client.publish(topic, body, qos=1, retain=True)
                                    published[topic] = body
                            for topic in set(published) - set(current):
                                # Recht entzogen: gespeicherten Zustand löschen
                                client.publish(topic, b'', qos=1, retain=True)
                                published.pop(topic)
                        set_state('mqtt', {**get_state('mqtt'), 'at': timezone.now().isoformat(), 'enabled': True, 'connected': client.is_connected()})
                    except Exception as exc:
                        self.stderr.write('MQTT-Zustände: ' + str(exc)[:200])
                    finally:
                        connections.close_all()
        finally:
            client.publish('eta/app/availability', 'offline', qos=1, retain=True)
            client.loop_stop()
            client.disconnect()
