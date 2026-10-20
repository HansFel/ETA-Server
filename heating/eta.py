from decimal import Decimal, InvalidOperation
import re
import requests
from defusedxml import ElementTree as ET
from django.conf import settings

NS = {'e': 'http://www.eta.co.at/rest/v1'}

class EtaError(Exception):
    pass

class EtaClient:
    def __init__(self, url=None):
        self.url = (url or settings.ETA_URL).rstrip('/')
        self.session = requests.Session()
        self.session.trust_env = False
    def request(self, resource, method='GET', data=None):
        try:
            response = self.session.request(method, self.url + resource, data=data, timeout=(3, 5), allow_redirects=False)
            if response.status_code not in (200, 201):
                raise EtaError('ETA antwortet mit HTTP ' + str(response.status_code))
            if len(response.content) > 2_000_000:
                raise EtaError('ETA-Antwort zu groß')
            return ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError) as exc:
            raise EtaError('ETA-Verbindung oder XML-Antwort fehlerhaft') from exc
    def uri(self, uri):
        if not re.fullmatch(r'/\d+/\d+/\d+/\d+/\d+', uri):
            raise EtaError('Ungültige Datenpunktadresse')
        return uri
    def read(self, uri):
        root = self.request('/user/var' + self.uri(uri))
        node = root.find('e:value', NS)
        if node is None:
            raise EtaError('Messwert fehlt')
        raw = (node.text or '').strip()
        try:
            factor = Decimal(node.get('scaleFactor', '1'))
            number = Decimal(raw) / factor
            if not number.is_finite():
                raise InvalidOperation
        except (InvalidOperation, ZeroDivisionError) as exc:
            raise EtaError('Ungültige Skalierung') from exc
        return {'raw': raw, 'value': float(number), 'text': node.get('strValue', raw), 'unit': node.get('unit', ''), 'scale': str(factor), 'offset': node.get('advTextOffset', '0'), 'quality': 'ok'}
    def info(self, uri):
        root = self.request('/user/varinfo' + self.uri(uri))
        node = root.find('.//e:variable', NS)
        if node is None:
            raise EtaError('Datenpunktinformationen fehlen')
        valid = node.find('e:validValues', NS)
        info = {'writable': node.get('isWritable') == '1', 'scale': node.get('scaleFactor', '1'), 'unit': node.get('unit', ''), 'type': node.findtext('e:type', '', NS), 'choices': []}
        if valid is not None:
            info['choices'] = [{'raw': n.text, 'label': n.get('strValue', n.text)} for n in valid.findall('e:value', NS)]
            for bound in ('min', 'max'):
                el = valid.find('e:' + bound, NS)
                if el is not None:
                    info[bound] = el.text
        return info
    def validate(self, info, value):
        if not info.get('writable'):
            raise EtaError('Dieser Wert ist nicht schreibbar')
        try:
            num = Decimal(str(value))
            if not num.is_finite():
                raise InvalidOperation
            if info['choices']:
                if str(value) not in [v['raw'] for v in info['choices']]:
                    raise EtaError('Nicht erlaubter Schaltwert')
                return str(value)
            if 'min' not in info or 'max' not in info:
                raise EtaError('ETA liefert keine sicheren Wertebereiche')
            raw = num * Decimal(info['scale'])
            if raw != raw.to_integral_value() or not Decimal(info['min']) <= raw <= Decimal(info['max']):
                raise EtaError('Wert außerhalb des Bereichs oder zu viele Nachkommastellen')
            return str(int(raw))
        except (InvalidOperation, ValueError, KeyError) as exc:
            raise EtaError('Ungültiger Wert') from exc
    def write(self, uri, raw):
        root = self.request('/user/var' + self.uri(uri), 'POST', {'value': raw})
        if root.find('e:success', NS) is None:
            raise EtaError('ETA bestätigt den Befehl nicht')
    def write_timeslot(self, uri, raw, begin, end):
        # ETA RESTful v1.2, 4.2: Zeitfenster brauchen value, begin und end (Viertelstunden seit Mitternacht, 0-96).
        if not (isinstance(begin, int) and isinstance(end, int) and 0 <= begin <= end <= 96):
            raise EtaError('Ungültiges Zeitfenster')
        root = self.request('/user/var' + self.uri(uri), 'POST', {'value': raw, 'begin': str(begin), 'end': str(end)})
        if root.find('e:success', NS) is None:
            raise EtaError('ETA bestätigt den Befehl nicht')
    def errors(self):
        root = self.request('/user/errors')
        return [{'section': f.get('name'), 'message': e.get('msg'), 'priority': e.get('priority'), 'time': e.get('time'), 'detail': e.text or ''} for f in root.findall('.//e:fub', NS) for e in f.findall('e:error', NS)]
