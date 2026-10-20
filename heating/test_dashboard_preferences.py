import json
from django.test import Client
from django.urls import reverse
from .tests import AppTestBase
from .services import get_state
from .models import Grant


class DashboardPreferenceTests(AppTestBase):
    def save(self, client, points, hours=24, **extra):
        return client.post(reverse('dashboard_preferences'), json.dumps({'points': points, 'hours': hours, **extra}), content_type='application/json')

    def test_selection_restored_for_user_across_new_sessions(self):
        self.assertEqual(self.save(self.client, ['boiler', 'power'], 168).status_code, 200)
        other_session = Client()
        other_session.force_login(self.user)
        page = other_session.get('/dashboard/')
        self.assertEqual(page.context['chart_hours'], 168)
        self.assertEqual({p.key for p in page.context['points'] if p.chart_selected}, {'boiler', 'power'})
        self.assertContains(page, 'data-default-hours="168"')

    def test_separate_users_and_no_foreign_or_control_points(self):
        a, client_a = self.person('preference-a', Kessel='view')
        b, client_b = self.person('preference-b', Kessel='view')
        self.assertEqual(self.save(client_a, ['boiler'], 1, user=b.pk).status_code, 200)
        self.assertEqual(self.save(client_b, ['power'], 720).status_code, 200)
        self.assertEqual(get_state('dashboard-user-%s' % a.pk)['points'], ['boiler'])
        self.assertEqual(get_state('dashboard-user-%s' % b.pk)['points'], ['power'])
        self.assertEqual(self.save(client_a, ['wiese-outside']).status_code, 403)
        self.assertEqual(self.save(client_a, ['boiler-switch']).status_code, 403)
        self.assertEqual(get_state('dashboard-user-%s' % a.pk)['points'], ['boiler'])

    def test_revoked_rights_filter_saved_selection_and_empty_selection_persists(self):
        user, client = self.person('preference-revocation', Kessel='view', Wiesenegger='view')
        self.assertEqual(self.save(client, ['boiler', 'wiese-outside']).status_code, 200)
        Grant.objects.filter(user=user, section='Kessel').delete()
        page=client.get('/dashboard/')
        self.assertEqual([p.key for p in page.context['points'] if p.chart_selected], ['wiese-outside'])
        self.assertEqual(self.save(client, [], 1).status_code, 200)
        self.assertFalse(any(p.chart_selected for p in client.get('/dashboard/').context['points']))

    def test_endpoint_requires_login_post_csrf_and_valid_data(self):
        self.assertEqual(self.save(Client(), ['power']).status_code, 302)
        self.assertEqual(self.client.get(reverse('dashboard_preferences')).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(self.save(csrf_client, ['power']).status_code, 403)
        self.assertEqual(self.save(self.client, ['power'], 999).status_code, 400)
        self.assertEqual(self.save(self.client, ['power'], True).status_code, 400)
        self.assertEqual(self.client.post(reverse('dashboard_preferences'), 'null', content_type='application/json').status_code, 400)
