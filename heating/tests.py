import hashlib
import json
import tempfile
import uuid
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
from django.test import TransactionTestCase, Client, override_settings
from django.conf import settings
from django.contrib.auth.models import User, Group
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone
from .models import Point, Sample, ChangeEvent, Command, AccessToken, Grant, ControlDetail, Audit
from .services import set_state, get_state
from .eta import EtaClient, EtaError
from . import backups

PASSWORD = 'local-test-passphrase-4826'

class AppTestBase(TransactionTestCase):
    """Gemeinsame Testumgebung und Hilfen."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.override = override_settings(BACKUP_DIR=Path(self.temp.name)/'backups', DATA_DIR=Path(self.temp.name), INSTANCE_NAME='eta-docker-test', EMAIL_HOST='', ALLOWED_HOSTS=['testserver','localhost'])
        self.override.enable()
        self.user = User.objects.create_user('admin',email='admin@example.invalid',password=PASSWORD)
        call_command('bootstrap',verbosity=0)
        self.user.groups.set(Group.objects.filter(name='admin'))
        self.client.force_login(self.user)
        self.point=Point.objects.get(key='boiler-switch')
    def tearDown(self):
        self.override.disable()
        self.temp.cleanup()
    def person(self, name, wiese_profile='switch_schedule', **grants):
        """Legt eine Person mit Rechten ({'Bereich': 'view'|'control'}) an und gibt einen angemeldeten Client zurück."""
        user=User.objects.create_user(name,password=PASSWORD)
        for section,level in grants.items():
            Grant.objects.create(user=user,section=section,level=level)
        if grants.get('Wiesenegger') == 'control':
            ControlDetail.objects.create(user=user, section='Wiesenegger', profile=wiese_profile)
        client=Client()
        client.force_login(user)
        return user,client
    def eta_mock(self, mock, raw_before='1802', raw_after='1803'):
        set_state('controls',{'enabled':True})
        eta=mock.return_value
        eta.info.return_value={'writable':True,'scale':'1','unit':'','choices':[{'raw':'1802','label':'Aus'},{'raw':'1803','label':'Ein'}]}
        eta.validate.return_value=raw_after
        eta.read.side_effect=[{'raw':raw_before,'text':'Aus','unit':''},{'raw':raw_after,'text':'Ein','unit':''}]
        return eta

class AppTests(AppTestBase):
    def test_basepath_proxy_under_subpath(self):
        # Simuliert den NAS-Betrieb hinter Traefik: App läuft unter /eta (kein stripPrefix).
        # FORCE_SCRIPT_NAME macht alle internen Links + Statics unter /eta/... erreichbar,
        # Login-/Logout-Umleitungen zeigen auf /eta/..., und https-Origin gilt als vertraut.
        with override_settings(
            SCRIPT_NAME='/eta', FORCE_SCRIPT_NAME='/eta',
            LOGIN_URL='/eta/login/', LOGIN_REDIRECT_URL='/eta/',
            LOGOUT_REDIRECT_URL='/eta/login/',
            STATIC_URL='/eta/static/',
            ALLOWED_HOSTS=['testserver','localhost','mqtt.example.invalid'],
            CSRF_TRUSTED_ORIGINS=['https://mqtt.example.invalid'],
        ):
            anon=Client(HTTP_HOST='mqtt.example.invalid')
            # geschützte Seite wird zur Anmeldung umgeleitet (FORCE_SCRIPT_NAME setzt der
            # echte WSGI-Server; der Test-Client nicht, daher akzeptieren wir /login/ oder /eta/login/)
            r_anon=anon.get('/dashboard/')
            self.assertEqual(r_anon.status_code,302,)
            self.assertTrue(('/login/' in r_anon.get('Location','')) or ('/eta/login/' in r_anon.get('Location','')))
            # eingeloggt (self.client hat Rolle admin aus setUp): erreichbar, und alle internen
            # Links + Statics werden unter /eta/... ausgegeben.
            dashboard=self.client.get('/dashboard/')
            self.assertEqual(dashboard.status_code,200)
            self.assertContains(dashboard, 'data-live-url="/eta/api/live/"')
            self.assertContains(dashboard, 'data-history-url="/eta/api/history/"')
            self.assertContains(dashboard, 'href="/eta/export/"')
            html=self.client.get('/control/').content.decode()
            self.assertIn('/eta/static/app.css',html)
            self.assertIn('src="/eta/static/app.js"',html)
            self.assertIn('href="/eta/password/"',html)
            # https-Origin beim CSRF wird toleriert (Header-Simulation)
            self.assertIn('https://mqtt.example.invalid',settings.CSRF_TRUSTED_ORIGINS)
    def test_protected_routes_anonymous_and_reader(self):
        anon=Client()
        for path in ['/dashboard/','/control/','/backups/','/people/','/api/live/','/api/history/','/export/']:
            self.assertEqual(anon.get(path).status_code,302,path)
        _,reader=self.person('leser',Kessel='view')
        for path in ['/control/','/backups/','/people/','/admin/','/backups/fake/download/']:
            self.assertEqual(reader.get(path).status_code,403,path)
        self.assertEqual(reader.get('/api/live/').status_code,200)
        self.assertEqual(reader.post('/backups/action/',{'action':'backup'}).status_code,403)
        self.assertEqual(reader.post('/people/',{'action':'create','username':'x'}).status_code,403)
        self.assertFalse(User.objects.filter(username='x').exists())
    def test_role_revocation_immediate(self):
        self.user.groups.remove(Group.objects.get(name='admin'))
        self.assertEqual(self.client.get('/backups/').status_code,403)
    def test_grant_revocation_immediate(self):
        user,client=self.person('wiese',Wiesenegger='control')
        self.assertEqual(client.get('/control/').status_code,200)
        Grant.objects.filter(user=user).delete()
        self.assertEqual(client.get('/control/').status_code,403)
        self.assertEqual(client.get('/api/live/').status_code,403)
    def test_blocked_user_session_revoked(self):
        self.user.is_active=False;self.user.save()
        self.assertEqual(self.client.get('/api/live/').status_code,302)
    def test_csrf_required(self):
        client=Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post('/logout/').status_code,403)
    def test_csrf_accepts_null_origin_but_still_requires_token(self):
        # Browsers like Edge may send Origin: null (opaque origin) on same-origin
        # POSTs. TrustingCsrfViewMiddleware must accept that while still enforcing
        # the CSRF token itself.
        client=Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        # Without a valid token the POST is still rejected, even with Origin: null.
        self.assertEqual(client.post('/logout/',{},HTTP_ORIGIN='null').status_code,403)
        # With a valid token the opaque origin is accepted.
        client.get('/dashboard/')
        token=client.cookies['eta_csrf'].value
        response=client.post('/logout/',{'csrfmiddlewaretoken':token},HTTP_ORIGIN='null')
        self.assertEqual(response.status_code,302)
    def test_last_administrator_retained(self):
        self.client.post('/people/',{'action':'save','user':self.user.id,'active':'on'})
        self.assertTrue(self.user.groups.filter(name='admin').exists())
    def test_admin_pages_render(self):
        for path in ['/admin/','/dashboard/','/people/','/control/','/backups/','/password/']:
            self.assertEqual(self.client.get(path).status_code,200,path)
    def test_control_page_groups_all_blocks(self):
        response=self.client.get('/control/')
        html=response.content.decode()
        for section in ['Kessel','Warmwasser','HK Haus','Wiesenegger','Werkstatt','Puffer']:
            self.assertIn(section,html,section)
        self.assertIn('Warmwasser sofort laden',html)
        self.assertIn('Heizkurve bei −10 °C',html)
        # Wiesenegger "Betrieb" is read-only on the real plant and must not be offered.
        self.assertNotIn('Betrieb',html)
    def test_control_page_ignores_invalid_numeric_metadata(self):
        point=Point.objects.get(key='wiese-switch')
        point.metadata={'min':'\n     ','max':'\n     ','scale':'10','choices':[{'raw':'1802','label':'Aus'},{'raw':'1803','label':'Ein'}],'writable':True}
        point.save(update_fields=['metadata'])
        _,client=self.person('wiese-meta',Wiesenegger='control')
        self.assertEqual(client.get('/control/').status_code,200)
    @patch('heating.views.EtaClient')
    def test_wiesenegger_esp_mode_blocks_without_fresh_release(self,mock):
        self.eta_mock(mock)
        set_state('wiesenegger-control',{'mode':'esp_release'})
        set_state('wiesenegger-esp-release',{'enabled':False,'at':timezone.now().isoformat()})
        _,client=self.person('wiese-esp',Wiesenegger='control')
        point=Point.objects.get(key='wiese-switch')
        html=client.get('/control/').content.decode()
        self.assertIn('Wiesenegger-Steuerung ist derzeit gesperrt',html)
        self.assertEqual(client.post(f'/control/{point.pk}/set/',{'value':'1803'}).status_code,403)
        mock.assert_not_called()
    @patch('heating.views.EtaClient')
    def test_wiesenegger_esp_mode_allows_with_fresh_release(self,mock):
        eta=self.eta_mock(mock)
        set_state('wiesenegger-control',{'mode':'esp_release'})
        set_state('wiesenegger-esp-release',{'enabled':True,'at':timezone.now().isoformat()})
        _,client=self.person('wiese-esp-ok',Wiesenegger='control')
        point=Point.objects.get(key='wiese-switch')
        self.assertEqual(client.post(f'/control/{point.pk}/set/',{'value':'1803'}).status_code,302)
        eta.write.assert_called_once_with(point.uri,'1803')
    def test_admin_sets_wiesenegger_mode(self):
        self.assertEqual(self.client.post('/control/wiesenegger-mode/',{'mode':'esp_release','password':PASSWORD}).status_code,302)
        self.assertEqual(get_state('wiesenegger-control').get('mode'),'esp_release')
    def test_no_control_when_locked(self):
        with patch('heating.views.EtaClient') as eta:
            self.assertEqual(self.client.post(f'/control/{self.point.pk}/set/',{'value':'1803'}).status_code,403)
            eta.assert_not_called()
    def test_control_validate_boundaries(self):
        info={'writable':True,'choices':[],'min':'0','max':'1000','scale':'10'}
        client=EtaClient()
        self.assertEqual(client.validate(info,'55.5'),'555')
        for value in ['101','NaN','Infinity','55.55','-1']:
            with self.assertRaises(EtaError):client.validate(info,value)
    @patch('heating.views.EtaClient')
    def test_critical_point_asks_once_without_password(self,mock):
        eta=self.eta_mock(mock)
        self.assertTrue(self.point.confirm)
        r=self.client.post(f'/control/{self.point.pk}/set/',{'value':'1803'})
        self.assertEqual(r.status_code,200)
        self.assertContains(r,'Ja, jetzt senden')
        self.assertNotContains(r,'type="password"')
        eta.write.assert_not_called()
        self.assertEqual(Command.objects.count(),0)
        eta.read.side_effect=[{'raw':'1802','text':'Aus','unit':''},{'raw':'1803','text':'Ein','unit':''}]
        self.assertEqual(self.client.post(f'/control/{self.point.pk}/set/',{'value':'1803','confirmed':'1'}).status_code,302)
        eta.write.assert_called_once_with(self.point.uri,'1803')
        cmd=Command.objects.get();self.assertEqual(cmd.status,'confirmed');self.assertEqual(cmd.before,'1802')
    @patch('heating.views.EtaClient')
    def test_normal_point_switches_directly_with_readback_and_audit(self,mock):
        from .models import Audit
        eta=self.eta_mock(mock)
        point=Point.objects.get(key='house-auto');self.assertFalse(point.confirm)
        self.assertEqual(self.client.post(f'/control/{point.pk}/set/',{'value':'1803'}).status_code,302)
        eta.write.assert_called_once_with(point.uri,'1803')
        cmd=Command.objects.get();self.assertEqual(cmd.status,'confirmed')
        self.assertEqual(list(Audit.objects.filter(action__startswith='control-').values_list('action',flat=True).order_by('id')),['control-request','control-result'])
    @patch('heating.views.EtaClient')
    def test_control_timeout_no_retry(self,mock):
        eta=self.eta_mock(mock)
        eta.read.side_effect=[{'raw':'1802','text':'Aus','unit':''},EtaError('timeout')]
        eta.write.side_effect=EtaError('timeout')
        point=Point.objects.get(key='house-auto')
        self.client.post(f'/control/{point.pk}/set/',{'value':'1803'})
        cmd=Command.objects.get();self.assertEqual(cmd.status,'uncertain');self.assertEqual(eta.write.call_count,1)
    @patch('heating.views.EtaClient')
    def test_wiesenegger_person_only_own_circuit(self,mock):
        eta=self.eta_mock(mock)
        _,client=self.person('wiese',Wiesenegger='control')
        own=Point.objects.get(key='wiese-switch');restricted=Point.objects.get(key='wiese-auto');other=Point.objects.get(key='boiler-switch');ww=Point.objects.get(key='ww-switch')
        # fremde Bereiche: auch per direktem POST kein Zugriff, ETA wird nicht angefasst
        for p in (other,ww,restricted):
            self.assertEqual(client.post(f'/control/{p.pk}/set/',{'value':'1803','confirmed':'1'}).status_code,403)
        mock.assert_not_called()
        self.assertEqual(client.post(f'/control/{own.pk}/set/',{'value':'1803'}).status_code,302)
        eta.write.assert_called_once_with(own.uri,'1803')
        html=client.get('/control/').content.decode()
        self.assertIn('Wiesenegger ein / aus',html)
        self.assertIn('Wiesenegger Zeitschaltuhr',html)
        for hidden in ('Kessel ein / aus','WW Ein / Aus','Werkstatt ein / aus','Pufferladung'):
            self.assertNotIn(hidden,html)
        self.assertNotIn('Wiesenegger Automatik',html)
        for path in ['/backups/','/people/','/admin/']:
            self.assertEqual(client.get(path).status_code,403,path)
    @patch('heating.views.EtaClient')
    def test_view_only_cannot_switch(self,mock):
        self.eta_mock(mock)
        _,client=self.person('mitleser',Wiesenegger='view',Kessel='view')
        own=Point.objects.get(key='wiese-switch')
        self.assertEqual(client.post(f'/control/{own.pk}/set/',{'value':'1803'}).status_code,403)
        self.assertEqual(client.get('/control/').status_code,403)
        mock.assert_not_called()
    def test_data_apis_limited_to_visible_sections(self):
        _,client=self.person('wiese',Wiesenegger='view')
        data=client.get('/api/live/').json()
        self.assertTrue(data['points'])
        self.assertEqual({p['section'] for p in data['points']},{'Wiesenegger'})
        self.assertEqual(data['errors'],{})
        self.assertEqual(client.get('/api/history/?point=power').status_code,403)
        self.assertEqual(client.get('/export/?point=power').status_code,403)
        self.assertEqual(client.get('/export/?point=power,flow').status_code,403)
        self.assertEqual(client.get('/api/history/?point=gibtesnicht').status_code,400)
        html=client.get('/dashboard/').content.decode()
        self.assertNotIn('value="power"',html)
        self.assertEqual(client.get('/dashboard/').status_code,200)
        # Leser mit Kessel-Recht sieht Kesselwerte, aber keine fremden Bereiche
        _,kessel=self.person('kessel',Kessel='view')
        self.assertEqual(kessel.get('/api/history/?point=power').status_code,200)
        self.assertEqual(kessel.get('/api/history/?point=flow').status_code,403)
    def test_person_without_grants_has_no_access(self):
        _,client=self.person('leer')
        self.assertEqual(client.get('/').status_code,200)
        for path in ['/dashboard/','/control/','/api/live/','/api/history/','/export/']:
            self.assertEqual(client.get(path).status_code,403,path)
    def test_home_redirects_by_rights(self):
        self.assertTrue(self.client.get('/').url.endswith('/admin/'))
        _,a=self.person('a',Kessel='view');self.assertTrue(a.get('/').url.endswith('/dashboard/'))
        _,b=self.person('b',Wiesenegger='control');self.assertTrue(b.get('/').url.endswith('/control/'))
    def test_blocked_person_loses_access(self):
        user,client=self.person('wiese',Wiesenegger='control')
        self.client.post('/people/',{'action':'save','user':user.id,'grant-4':'control'})  # ohne "active" = gesperrt
        user.refresh_from_db();self.assertFalse(user.is_active)
        self.assertEqual(client.get('/control/').status_code,302)
    def test_admin_manages_rights_and_presets(self):
        r=self.client.post('/people/',{'action':'create','username':'Wiese','preset':'wiesenegger','active':'on'})
        self.assertEqual(r.status_code,200)
        user=User.objects.get(username='wiese')
        self.assertEqual(dict(user.grants.values_list('section','level')),{'Wiesenegger':'control'})
        self.assertEqual(ControlDetail.objects.get(user=user, section='Wiesenegger').profile,'switch_schedule')
        self.assertFalse(user.groups.filter(name='admin').exists())
        # einzeln ändern: Wiesenegger nur sehen, HK Haus schalten (Reihenfolge der Bereiche: Kessel, HK Haus, Puffer, WW, Wiesenegger, Werkstatt)
        self.client.post('/people/',{'action':'save','user':user.id,'active':'on','grant-1':'control','grant-4':'view'})
        self.assertEqual(dict(user.grants.values_list('section','level')),{'HK Haus':'control','Wiesenegger':'view'})
        self.assertFalse(ControlDetail.objects.filter(user=user, section='Wiesenegger').exists())
        # ungültige Stufe / Vorlage werden abgelehnt, nichts wird geändert
        self.client.post('/people/',{'action':'save','user':user.id,'active':'on','grant-0':'root'})
        self.client.post('/people/',{'action':'create','username':'zweite','preset':'gibtesnicht','active':'on'})
        self.assertEqual(user.grants.count(),2)
        self.assertFalse(User.objects.filter(username='zweite').exists())
    def test_confirm_setting_admin_only(self):
        point=Point.objects.get(key='house-auto')
        _,client=self.person('wiese',Wiesenegger='control')
        self.assertEqual(client.post(f'/control/{point.pk}/confirm-setting/',{'confirm':'on'}).status_code,403)
        point.refresh_from_db();self.assertFalse(point.confirm)
        self.client.post(f'/control/{point.pk}/confirm-setting/',{'confirm':'on'})
        point.refresh_from_db();self.assertTrue(point.confirm)
        self.client.post(f'/control/{point.pk}/confirm-setting/',{})
        point.refresh_from_db();self.assertFalse(point.confirm)
    @patch('heating.views.EtaClient')
    def test_wiesenegger_full_profile_allows_auto(self,mock):
        eta=self.eta_mock(mock)
        _,client=self.person('wiese-full',Wiesenegger='control',wiese_profile='full')
        point=Point.objects.get(key='wiese-auto')
        self.assertEqual(client.post(f'/control/{point.pk}/set/',{'value':'1803'}).status_code,302)
        eta.write.assert_called_once_with(point.uri,'1803')
    @patch('heating.views.EtaClient')
    def test_timeslot_written_as_quarter_hours(self,mock):
        # ETA RESTful v1.2: Zeitfenster werden mit value, begin und end (Viertelstunden) geschrieben.
        set_state('controls',{'enabled':True})
        eta=mock.return_value
        point=Point.objects.get(key='wiese-time-mon-1')
        eta.info.return_value={'writable':True,'scale':'10','unit':'','type':'TIMESLOT','choices':[]}
        eta.read.side_effect=[{'raw':'0','text':'00:00 - 24:00','unit':''},{'raw':'0','text':'06:00 - 09:30','unit':''}]
        _,client=self.person('wiese-slot',Wiesenegger='control')
        self.assertEqual(client.post(f'/control/{point.pk}/set/',{'value':'06:00 - 09:30'}).status_code,302)
        eta.write.assert_not_called()
        eta.write_timeslot.assert_called_once_with(point.uri,'0',24,38)
        self.assertEqual(Command.objects.get(point=point).status,'confirmed')
        eta.read.side_effect=[{'raw':'0','text':'06:00 - 09:30','unit':''}]
        client.post(f'/control/{point.pk}/set/',{'value':'06:10 - 09:00'})
        eta.write_timeslot.assert_called_once()
    def test_eta_timeslot_request_parameters(self):
        client=EtaClient('http://eta.invalid')
        with patch.object(EtaClient,'request') as request:
            request.return_value.find.return_value=object()
            client.write_timeslot('/121/10103/12113/0/1082','0',24,96)
            request.assert_called_once_with('/user/var/121/10103/12113/0/1082','POST',{'value':'0','begin':'24','end':'96'})
            with self.assertRaises(EtaError):
                client.write_timeslot('/121/10103/12113/0/1082','0',40,24)
    def schedule_setup(self, mon=('00:00 - 24:00','00:00 - 00:00','00:00 - 00:00')):
        set_state('controls',{'enabled':True})
        now=timezone.now()
        slot_meta={'writable':True,'scale':'10','unit':'','type':'TIMESLOT','choices':[]}
        for key in ['wed','thu','fri','sat','sun','mon','tue']:
            for n in (1,2,3):
                text=mon[n-1] if key=='mon' else '00:00 - 00:00'
                Point.objects.filter(key=f'wiese-time-{key}-{n}').update(metadata=slot_meta,latest={'raw':'0','value':0.0,'text':text,'unit':'','quality':'ok'},updated=now)
            Point.objects.filter(key=f'wiese-time-{key}').update(metadata={'writable':True,'scale':'10','unit':'','type':'DEFAULT','choices':[],'min':'0.0','max':'800.0'},latest={'raw':'160','value':16.0,'text':'xxx','unit':'','quality':'ok'},updated=now)
        Point.objects.filter(key='wiese-time-state').update(latest={'raw':'1803','value':1803.0,'text':'Ein','unit':'','quality':'ok'},updated=now)
    def schedule_form(self, **changes):
        data={}
        for key in ['mon','tue','wed','thu','fri','sat','sun']:
            for n in (1,2,3):
                point=Point.objects.get(key=f'wiese-time-{key}-{n}')
                text=point.latest['text']
                begin,end=text.split(' - ')
                active=text not in ('00:00 - 00:00','24:00 - 24:00')
                data[f'{point.key}-orig']={'00:00 - 24:00':'0-96','00:00 - 00:00':'0-0'}.get(text,'')
                if active:
                    data[f'{point.key}-on']='on'
                    data[f'{point.key}-begin']=begin
                    data[f'{point.key}-end']='00:00' if end=='24:00' else end
            data[f'wiese-time-{key}-orig']='16.0'
            data[f'wiese-time-{key}-value']='16.0'
        data.update(changes)
        return {k:v for k,v in data.items() if v is not None}
    def test_schedule_page_shows_week_timeline(self):
        self.schedule_setup()
        _,client=self.person('wiese-view-schedule',Wiesenegger='control')
        html=client.get('/control/').content.decode()
        self.assertIn('Wiesenegger Zeitschaltuhr',html)
        self.assertIn('ETA meldet: Heizzeit Ein',html)
        self.assertIn('name="wiese-time-mon-1-begin"',html)
        self.assertIn('data-orig="0-96"',html)
        self.assertIn('00:00–24:00',html)
        self.assertNotIn('HH:MM - HH:MM',html)
    @patch('heating.views.EtaClient')
    def test_schedule_saves_only_changed_slots(self,mock):
        self.schedule_setup()
        eta=mock.return_value
        eta.info.return_value={'writable':True,'scale':'10','unit':'','type':'TIMESLOT','choices':[]}
        eta.read.side_effect=[{'raw':'0','text':'00:00 - 24:00','unit':''},{'raw':'0','text':'06:00 - 08:00','unit':''},
                              {'raw':'0','text':'00:00 - 00:00','unit':''},{'raw':'0','text':'16:00 - 24:00','unit':''}]
        _,client=self.person('wiese-schedule',Wiesenegger='control')
        response=client.post('/control/schedule/',self.schedule_form(**{'wiese-time-mon-1-begin':'06:00','wiese-time-mon-1-end':'08:00','wiese-time-mon-2-on':'on','wiese-time-mon-2-begin':'16:00','wiese-time-mon-2-end':'00:00'}))
        self.assertEqual(response.status_code,302)
        mon1,mon2=Point.objects.get(key='wiese-time-mon-1'),Point.objects.get(key='wiese-time-mon-2')
        self.assertEqual([c.args for c in eta.write_timeslot.call_args_list],[(mon1.uri,'0',24,32),(mon2.uri,'0',64,96)])
        eta.write.assert_not_called()
        self.assertEqual(sorted(Command.objects.values_list('status',flat=True)),['confirmed','confirmed'])
        mon1.refresh_from_db()
        self.assertEqual(mon1.latest['text'],'06:00 - 08:00')
    @patch('heating.views.EtaClient')
    def test_schedule_outside_temperature_uses_validated_write(self,mock):
        self.schedule_setup()
        eta=mock.return_value
        eta.info.return_value={'writable':True,'scale':'10','unit':'','type':'DEFAULT','choices':[],'min':'0','max':'800'}
        eta.validate.return_value='175'
        eta.read.side_effect=[{'raw':'160','value':16.0,'text':'xxx','unit':''},{'raw':'175','value':17.5,'text':'xxx','unit':''}]
        response=self.client.post('/control/schedule/',self.schedule_form(**{'wiese-time-tue-value':'17,5'}))
        self.assertEqual(response.status_code,302)
        eta.write.assert_called_once_with(Point.objects.get(key='wiese-time-tue').uri,'175')
        eta.write_timeslot.assert_not_called()
    @patch('heating.views.EtaClient')
    def test_schedule_rejects_invalid_input_without_eta_call(self,mock):
        self.schedule_setup()
        overlap={'wiese-time-mon-1-begin':'06:00','wiese-time-mon-1-end':'10:00','wiese-time-mon-2-on':'on','wiese-time-mon-2-begin':'09:00','wiese-time-mon-2-end':'12:00'}
        for form in [overlap,{'wiese-time-mon-1-begin':'06:10'},{'wiese-time-mon-1-begin':'10:00','wiese-time-mon-1-end':'09:00'},{'wiese-time-mon-1-orig':'x'}]:
            self.assertEqual(self.client.post('/control/schedule/',self.schedule_form(**form)).status_code,302)
            self.assertIn('notice error',self.client.get('/control/').content.decode(),form)
        mock.assert_not_called()
        self.assertFalse(Command.objects.exists())
    @patch('heating.views.EtaClient')
    def test_schedule_does_not_overwrite_change_made_at_plant(self,mock):
        self.schedule_setup()
        eta=mock.return_value
        eta.info.return_value={'writable':True,'scale':'10','unit':'','type':'TIMESLOT','choices':[]}
        eta.read.side_effect=[{'raw':'0','text':'05:00 - 22:00','unit':''}]
        self.client.post('/control/schedule/',self.schedule_form(**{'wiese-time-mon-1-begin':'06:00'}))
        eta.write_timeslot.assert_not_called()
        self.assertIn('inzwischen an der Anlage geändert',self.client.get('/control/').content.decode())
    @patch('heating.views.EtaClient')
    def test_schedule_stops_after_uncertain_write(self,mock):
        self.schedule_setup()
        eta=mock.return_value
        eta.info.return_value={'writable':True,'scale':'10','unit':'','type':'TIMESLOT','choices':[]}
        eta.read.side_effect=[{'raw':'0','text':'00:00 - 24:00','unit':''}]
        eta.write_timeslot.side_effect=EtaError('timeout')
        self.client.post('/control/schedule/',self.schedule_form(**{'wiese-time-mon-1-begin':'06:00','wiese-time-tue-1-on':'on','wiese-time-tue-1-begin':'06:00','wiese-time-tue-1-end':'08:00'}))
        self.assertEqual(eta.write_timeslot.call_count,1)
        self.assertEqual(Command.objects.get().status,'uncertain')
        self.assertIn('1 weitere Änderung(en) wurden deshalb nicht gesendet',self.client.get('/control/').content.decode())
    @patch('heating.views.EtaClient')
    def test_schedule_rights_lock_and_confirmation(self,mock):
        self.schedule_setup()
        form=self.schedule_form(**{'wiese-time-mon-1-begin':'06:00'})
        _,other=self.person('hk-only',**{'HK Haus':'control','Wiesenegger':'view'})
        self.assertEqual(other.post('/control/schedule/',form).status_code,403)
        set_state('controls',{'enabled':False})
        self.assertEqual(self.client.post('/control/schedule/',form).status_code,403)
        set_state('controls',{'enabled':True})
        set_state('wiesenegger-control',{'mode':'esp_release'})
        _,limited=self.person('wiese-locked',Wiesenegger='control')
        self.assertEqual(limited.post('/control/schedule/',form).status_code,403)
        mock.assert_not_called()
        Point.objects.filter(key='wiese-time-mon-1').update(confirm=True)
        response=self.client.post('/control/schedule/',form)
        self.assertContains(response,'Zeitschaltuhr ändern')
        self.assertContains(response,'06:00 - 24:00')
        mock.assert_not_called()
    def test_migration_converts_old_roles(self):
        from importlib import import_module
        from django.apps import apps
        convert=import_module('heating.migrations.0002_person_grants').convert_roles
        op=User.objects.create_user('alt-bediener');rd=User.objects.create_user('alt-leser')
        op.groups.add(Group.objects.create(name='operator'));rd.groups.add(Group.objects.create(name='reader'))
        convert(apps,None)
        self.assertEqual(set(op.grants.values_list('level',flat=True)),{'control'})
        self.assertEqual(set(rd.grants.values_list('level',flat=True)),{'view'})
        self.assertEqual(op.grants.count(),6)
        self.assertFalse(Group.objects.filter(name__in=['operator','reader']).exists())
    def test_invitation_single_use(self):
        from .views import issue_token
        user=User.objects.create_user('newperson')
        link=issue_token(user,'invite');path=link.split('http://localhost:8098')[-1]
        if not path.startswith('/'):path='/access/'+link.split('/access/')[1]
        response=Client().post(path,{'new_password1':PASSWORD,'new_password2':PASSWORD})
        self.assertEqual(response.status_code,302)
        self.assertEqual(Client().get(path).status_code,400)
        user.refresh_from_db();self.assertTrue(user.check_password(PASSWORD))
    def test_history_gap_not_zero_or_integrated(self):
        p=Point.objects.get(key='power');end=timezone.now();start=end-timedelta(minutes=10)
        Sample.objects.create(point=p,at=start,value=25)
        Sample.objects.create(point=p,at=start+timedelta(seconds=30),value=25)
        Sample.objects.create(point=p,at=end,value=25)
        from .views import power_stats
        stats=power_stats(start,end)
        self.assertEqual(stats['coverage'],5)
        self.assertEqual(stats['utilization'],50)
        self.assertAlmostEqual(stats['energy'],.21,places=2)
    def test_history_multi_point_and_dashboard_series_ui(self):
        from django.utils import timezone
        end=timezone.now();start=end-timedelta(hours=2)
        for key in ('power','boiler','outside'):
            p=Point.objects.get(key=key)
            Sample.objects.create(point=p,at=start,value=20 if key=='power' else 50)
            Sample.objects.create(point=p,at=end,value=25 if key=='power' else 52)
        response=self.client.get('/api/history/?point=power,boiler,outside&hours=168')
        self.assertEqual(response.status_code,200)
        data=response.json()
        self.assertEqual([s['key'] for s in data['series']],['power','boiler','outside'])
        self.assertTrue(all(len(s['values'])==600 for s in data['series']))
        # unknown point is rejected
        self.assertEqual(self.client.get('/api/history/?point=power,gibtesnicht').status_code,400)
        # dashboard offers a multi-select with all points and power preselected
        html=self.client.get('/dashboard/').content.decode()
        self.assertIn('id="series-menu"',html)
        self.assertIn('value="power"',html)
        self.assertIn('checked',html)
        self.assertIn('id="chart-legend"',html)
    def test_events_commit_rollback_and_no_backup_loop(self):
        ChangeEvent.objects.all().delete()
        try:
            with transaction.atomic():
                Sample.objects.create(point=self.point,value=1)
                raise ValueError
        except ValueError:pass
        self.assertEqual(ChangeEvent.objects.count(),0)
        Sample.objects.create(point=self.point,value=1)
        self.assertEqual(ChangeEvent.objects.count(),1)
        backups.snapshot()
        self.assertEqual(ChangeEvent.objects.count(),0)
    def test_backup_failure_keeps_events(self):
        Sample.objects.create(point=self.point,value=2)
        before=ChangeEvent.objects.count()
        with patch('heating.backups.inspect_db',side_effect=ValueError('disk')):
            with self.assertRaises(ValueError):backups.snapshot()
        self.assertEqual(ChangeEvent.objects.count(),before)
        self.assertEqual(backups.manifests(),[])
    def test_concurrent_event_not_acknowledged(self):
        real=backups.inspect_db
        def insert(path):
            counts=real(path)
            Sample.objects.create(point=self.point,value=99)
            return counts
        with patch('heating.backups.inspect_db',side_effect=insert):backups.snapshot()
        self.assertGreater(ChangeEvent.objects.count(),0)
    def test_restore_real_isolated_db_and_session_invalidation(self):
        Sample.objects.create(point=self.point,value=7)
        meta=backups.snapshot()
        self.assertIn('eta-docker-test',meta['filename'])
        self.assertTrue(backups.restore_test(meta['id'])['restore_test'])
        Sample.objects.create(point=self.point,value=8)
        backups.restore(meta['id'])
        self.assertEqual(Sample.objects.count(),1)
        self.assertEqual(Sample.objects.first().value,7)
        self.assertFalse(get_state('backup-policy')['enabled'])
        self.assertFalse(get_state('controls')['enabled'])
        self.assertEqual(self.client.get('/api/live/').status_code,302)
        self.assertTrue(any(m['kind']=='pre-restore' for m in backups.manifests()))
    def test_checksum_tampering_rejected(self):
        meta=backups.snapshot()
        with open(backups.root()/meta['filename'],'ab') as handle:handle.write(b'bad')
        with self.assertRaises(ValueError):backups.artifact(meta['id'])
    def test_pending_copy_protects_original(self):
        set_state('backup-policy',{'target':'/targets/absent','target_marker':'test'})
        meta=backups.snapshot()
        with self.assertRaises(ValueError):backups.delete_backup(meta['id'])
        with self.assertRaises(ValueError):backups.deliver(meta)
        self.assertTrue((backups.root()/meta['filename']).exists())
    def test_scheduler_modes_and_max_wait(self):
        now=timezone.now();policy={'enabled':True,'mode':'event','interval':86400,'quiet':120,'minimum':900,'maximum':900}
        self.assertTrue(backups.due(policy,now-timedelta(hours=1),now,now-timedelta(minutes=20),now))
        self.assertFalse(backups.due(policy,now-timedelta(seconds=30),now,now-timedelta(minutes=20),now))
        policy['enabled']=False
        self.assertFalse(backups.due(policy,None,now,now-timedelta(days=1),now))

class MqttTests(AppTestBase):
    """MQTT-Brücke ohne Broker: Befehlsauswertung wie im Web."""
    def device(self, name='wg-esp32-01', **grants):
        user,_=self.person(name,**grants)
        user.groups.add(Group.objects.get_or_create(name='mqtt')[0])
        return user
    def send(self, key, value, device='wg-esp32-01', retained=False, **extra):
        from . import mqtt
        payload={'command_id':str(uuid.uuid4()),'value':value,**extra}
        return mqtt.handle_command(f'eta/dev/{device}/cmd/{key}/set',json.dumps(payload).encode(),retained)
    @patch('heating.views.EtaClient')
    def test_device_switches_with_readback_and_audit(self,mock):
        eta=self.eta_mock(mock)
        self.device(Wiesenegger='control')
        with patch('heating.mqtt.EtaClient',mock):
            topic,result=self.send('wiese-switch','ON')
        self.assertEqual(topic,'eta/dev/wg-esp32-01/cmd/wiese-switch/result')
        self.assertEqual(result['status'],'confirmed')
        point=Point.objects.get(key='wiese-switch')
        eta.write.assert_called_once_with(point.uri,'1803')
        cmd=Command.objects.get()
        self.assertEqual((str(cmd.id),cmd.role,cmd.user.username),(result['command_id'],'mqtt','wg-esp32-01'))
        self.assertTrue(Audit.objects.filter(actor='wg-esp32-01',action='control-result').exists())
    @patch('heating.mqtt.EtaClient')
    def test_duplicate_delivery_does_not_switch_twice(self,mock):
        eta=self.eta_mock(mock)
        self.device(Wiesenegger='control')
        from . import mqtt
        body=json.dumps({'command_id':str(uuid.uuid4()),'value':'1803'}).encode()
        first=mqtt.handle_command('eta/dev/wg-esp32-01/cmd/wiese-switch/set',body)[1]
        second=mqtt.handle_command('eta/dev/wg-esp32-01/cmd/wiese-switch/set',body)[1]
        self.assertEqual(eta.write.call_count,1)
        self.assertEqual((first['status'],second['status'],second['reason']),('confirmed','confirmed','Bereits verarbeitet'))
    @patch('heating.mqtt.EtaClient')
    def test_rejections_never_reach_eta(self,mock):
        set_state('controls',{'enabled':True})
        self.device(Wiesenegger='control')
        old=(timezone.now()-timedelta(minutes=10)).isoformat()
        cases=[
            dict(key='wiese-switch',value='ON',retained=True),
            dict(key='boiler-switch',value='ON'),
            dict(key='wiese-auto',value='ON'),                       # eingeschränktes Profil
            dict(key='wiese-curve-cold',value='60'),                  # nicht im Profil
            dict(key='wiese-switch',value='ON',ts_utc=old),
            dict(key='wiese-switch',value='ON',device='unbekannt'),
            dict(key='gibt-es-nicht',value='ON'),
        ]
        for case in cases:
            result=self.send(**case)[1]
            self.assertEqual(result['status'],'rejected',case)
        # Gerätekonto ohne Gruppe mqtt, normales Personenkonto und Admin-Konto sind keine Geräte
        self.person('person-ohne-mqtt',Wiesenegger='control')
        self.assertEqual(self.send('wiese-switch','ON',device='person-ohne-mqtt')[1]['status'],'rejected')
        admin=self.device('esp-admin',Wiesenegger='control');admin.groups.add(Group.objects.get(name='admin'))
        self.assertEqual(self.send('wiese-switch','ON',device='esp-admin')[1]['status'],'rejected')
        # Kaputtes JSON
        from . import mqtt
        self.assertEqual(mqtt.handle_command('eta/dev/wg-esp32-01/cmd/wiese-switch/set',b'{kaputt')[1]['status'],'rejected')
        self.assertIsNone(mqtt.handle_command('eta/dev/../cmd/x/set',b'{}'))
        mock.assert_not_called()
        self.assertFalse(Command.objects.exists())
    @patch('heating.mqtt.EtaClient')
    def test_lock_esp_mode_and_confirm_points(self,mock):
        self.device('esp-full',Wiesenegger='control',Kessel='control',wiese_profile='full')
        set_state('controls',{'enabled':False})
        self.assertIn('nicht freigegeben',self.send('wiese-switch','ON',device='esp-full')[1]['reason'])
        set_state('controls',{'enabled':True})
        self.assertIn('für MQTT nicht freigegeben',self.send('boiler-switch','ON',device='esp-full')[1]['reason'])
        self.device('esp-limited',Wiesenegger='control')
        set_state('wiesenegger-control',{'mode':'esp_release'})
        self.assertIn('ESP32-Freigabe',self.send('wiese-switch','ON',device='esp-limited')[1]['reason'])
        mock.assert_not_called()
    def test_states_only_for_visible_sections(self):
        from . import mqtt
        self.device(Wiesenegger='view')
        Point.objects.filter(key='wiese-outside').update(latest={'raw':'52','value':5.2,'text':'5,2','unit':'°C','quality':'ok'},updated=timezone.now())
        states=mqtt.visible_states('wg-esp32-01')
        self.assertIn('eta/dev/wg-esp32-01/state/wiese-outside',states)
        self.assertEqual(states['eta/dev/wg-esp32-01/state/wiese-outside']['value'],5.2)
        self.assertFalse(any('/state/boiler' in t or '/state/power' in t for t in states))
        Point.objects.filter(key='wiese-outside').update(updated=timezone.now()-timedelta(hours=1))
        stale=mqtt.visible_states('wg-esp32-01')['eta/dev/wg-esp32-01/state/wiese-outside']
        self.assertEqual((stale['quality'],stale['value']),('stale',None))
        self.assertEqual(mqtt.visible_states('unbekannt'),{})
    def test_people_page_marks_mqtt_device(self):
        user=self.device('esp-flag',Wiesenegger='control')
        response=self.client.post('/people/',{'action':'save','user':user.pk,'grant-4':'control','wiese-profile':'switch_schedule','active':'on','mqtt':'on'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(user.groups.filter(name='mqtt').exists())
        self.assertContains(response,'MQTT-Gerät · ')
        self.client.post('/people/',{'action':'save','user':user.pk,'grant-4':'control','active':'on','mqtt':'on','admin':'on'})
        self.assertFalse(user.groups.filter(name='admin').exists())
    @patch('heating.mqtt.EtaClient')
    def test_plain_text_payload_and_choice_labels(self,mock):
        set_state('controls',{'enabled':True})
        eta=mock.return_value
        self.device('esp-ww',WW='control')
        point=Point.objects.get(key='ww-priority')
        point.mqtt=True;point.save(update_fields=['mqtt'])
        choices=[{'raw':'1880','label':'Aus'},{'raw':'1881','label':'Nur WW'},{'raw':'1882','label':'Vorrang'}]
        eta.info.return_value={'writable':True,'scale':'1','unit':'','choices':choices}
        eta.validate.side_effect=lambda info,value: value if value in ('1880','1881','1882') else (_ for _ in ()).throw(EtaError('Nicht erlaubter Schaltwert'))
        eta.read.side_effect=[{'raw':'1880','text':'Aus','unit':''},{'raw':'1881','text':'Nur WW','unit':''},{'raw':'1881','text':'Nur WW','unit':''}]
        from . import mqtt
        topic,result=mqtt.handle_command('eta/dev/esp-ww/cmd/ww-priority/set',b'nur ww')
        self.assertEqual(result['status'],'confirmed')
        eta.write.assert_called_once_with(point.uri,'1881')
        uuid.UUID(result['command_id'])
        self.assertEqual(mqtt.handle_command('eta/dev/esp-ww/cmd/ww-priority/set',b'gibt es nicht')[1]['status'],'rejected')
    def test_point_mqtt_defaults_follow_confirm(self):
        self.assertFalse(Point.objects.get(key='boiler-switch').mqtt)
        self.assertTrue(Point.objects.get(key='wiese-switch').mqtt)
        self.assertFalse(Point.objects.get(key='power').mqtt)
    def test_payload_docs_from_metadata(self):
        from . import mqtt
        point=Point.objects.get(key='wiese-time-mon')
        point.metadata={'writable':True,'scale':'10','unit':'°C','type':'DEFAULT','choices':[],'min':'0.0','max':'800.0'}
        self.assertEqual(mqtt.payload_doc(point)['values'],['40','0','80'])
        self.assertIn('Schrittweite 0.1',mqtt.payload_doc(point)['hint'])
        point.metadata={'writable':True,'scale':'1','choices':[{'raw':'1802','label':'Aus'},{'raw':'1803','label':'Ein'}]}
        self.assertEqual(mqtt.payload_doc(point)['values'],['Aus','Ein','ON','OFF'])
        point.metadata={'writable':True,'scale':'10','type':'TIMESLOT','choices':[]}
        self.assertEqual(mqtt.payload_doc(point)['kind'],'Zeitfenster')
        point.metadata={'writable':False}
        self.assertEqual(mqtt.payload_doc(point)['values'],[])
        point.metadata={}
        self.assertEqual(mqtt.payload_doc(point)['kind'],'unbekannt')
    def test_mqtt_page_documents_every_point_and_saves_flags(self):
        self.device('esp-doc',Wiesenegger='control')
        Point.objects.filter(key='wiese-switch').update(metadata={'writable':True,'scale':'1','choices':[{'raw':'1802','label':'Aus'},{'raw':'1803','label':'Ein'}]})
        html=self.client.get('/mqtt/').content.decode()
        for p in Point.objects.filter(control=True):
            self.assertIn(f'eta/dev/esp-doc/cmd/{p.key}/set',html,p.key)
        self.assertIn('{&quot;command_id&quot;: &quot;&lt;neue UUID&gt;&quot;, &quot;value&quot;: &quot;Aus&quot;}',html)
        md=self.client.get('/mqtt/punkte.md?device=esp-doc')
        self.assertIn('eta/dev/esp-doc/cmd/wiese-switch/set',md.content.decode())
        self.assertIn('attachment',md['Content-Disposition'])
        self.client.post('/mqtt/',{'mqtt':['boiler-switch']})
        self.assertEqual(list(Point.objects.filter(mqtt=True).values_list('key',flat=True)),['boiler-switch'])
        _,person=self.person('nur-person',Wiesenegger='control')
        self.assertEqual(person.get('/mqtt/').status_code,403)
        self.assertEqual(person.post('/mqtt/',{'mqtt':['wiese-switch']}).status_code,403)
        self.assertEqual(person.get('/mqtt/punkte.md').status_code,403)
    def test_public_states_cover_all_points_status_and_errors(self):
        from . import mqtt
        now=timezone.now()
        Point.objects.filter(key='power').update(latest={'raw':'123','value':12.3,'text':'12,3','unit':'kW','quality':'ok'},updated=now)
        Point.objects.filter(key='boiler').update(latest={'raw':'','value':None,'text':'Keine Verbindung','unit':'°C','quality':'offline'},updated=now)
        set_state('logger',{'at':now.isoformat(),'ok':True,'points':2,'failed':1})
        set_state('worker',{'at':now.isoformat(),'ok':True})
        set_state('errors',{'ok':False,'items':[],'at':now.isoformat()})
        states=mqtt.public_states()
        for p in Point.objects.filter(enabled=True):
            self.assertIn(f'eta/state/{p.key}',states,p.key)
        self.assertEqual((states['eta/state/power']['value'],states['eta/state/power']['unit']),(12.3,'kW'))
        self.assertEqual((states['eta/state/boiler']['value'],states['eta/state/boiler']['quality']),(None,'offline'))
        self.assertTrue(states['eta/status']['worker_ok'])
        self.assertEqual((states['eta/errors']['known'],states['eta/errors']['items']),(False,None))
        Point.objects.filter(key='power').update(enabled=False)
        self.assertNotIn('eta/state/power',mqtt.public_states())
        md=mqtt.markdown()
        self.assertIn('| Kessel | Kesseltemperatur | `eta/state/boiler` | Messwert | °C |',md)
        self.assertNotIn('`eta/state/power`',md)
        html=self.client.get('/mqtt/').content.decode()
        self.assertIn('eta/state/boiler',html)
