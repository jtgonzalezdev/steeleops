import io
import os
import tempfile
import unittest

import app


class BrandingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = app.DB_PATH
        app.DB_PATH = os.path.join(self.temp_dir.name, 'test.db')
        app.init_db()

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def request(self, path, cookie=''):
        response = {}

        def start_response(status, headers):
            response['status'] = status
            response['headers'] = dict(headers)

        environ = {
            'PATH_INFO': path,
            'REQUEST_METHOD': 'GET',
            'QUERY_STRING': '',
            'HTTP_COOKIE': cookie,
            'wsgi.input': io.BytesIO(),
        }
        response['body'] = b''.join(app.application(environ, start_response))
        return response

    def test_login_uses_only_official_provider_logo(self):
        conn = app.db()
        conn.execute(
            "UPDATE companies SET logo_path='/static/steele-security-shield.svg' WHERE name=?",
            (app.PROVIDER_BRAND_NAME,),
        )
        conn.commit()
        conn.close()

        response = self.request('/login')

        self.assertEqual('200 OK', response['status'])
        self.assertEqual(2, response['body'].count(b'/static/steele-security-logo.png'))
        self.assertNotIn(b'steele-security-shield.svg', response['body'])

    def test_authenticated_shell_ignores_legacy_provider_upload(self):
        conn = app.db()
        company = conn.execute('SELECT id FROM companies WHERE name=?', (app.PROVIDER_BRAND_NAME,)).fetchone()
        conn.execute(
            "UPDATE companies SET logo_path='/static/steele-security-shield.svg' WHERE id=?",
            (company['id'],),
        )
        user = conn.execute(
            "SELECT id FROM users WHERE company_id=? AND role IN ('company_admin', 'admin') ORDER BY id LIMIT 1",
            (company['id'],),
        ).fetchone()
        conn.commit()
        conn.close()
        session_id = app.create_session(user['id'])

        response = self.request('/dashboard', f'{app.SESSION_COOKIE_NAME}={session_id}')

        self.assertEqual('200 OK', response['status'])
        self.assertGreaterEqual(response['body'].count(b'/static/steele-security-logo.png'), 2)
        self.assertNotIn(b'steele-security-shield.svg', response['body'])

    def test_authenticated_shell_separates_fixed_branding_from_scroll_regions(self):
        conn = app.db()
        user = conn.execute(
            "SELECT id FROM users WHERE role IN ('company_admin', 'admin') ORDER BY id LIMIT 1"
        ).fetchone()
        conn.close()
        session_id = app.create_session(user['id'])

        response = self.request('/dashboard', f'{app.SESSION_COOKIE_NAME}={session_id}')
        body = response['body'].decode('utf-8')

        self.assertEqual('200 OK', response['status'])
        self.assertIn('class="sidebar-brand"', body)
        self.assertIn('class="nav-links"', body)
        self.assertIn('class="topbar card"', body)
        self.assertIn('class="page-scroll"', body)
        self.assertIn('.brand-shield-sidebar { width: 100%; height: 160px; }', app.STYLES_CSS)
        self.assertIn('.brand-shield-topbar { width: 184px; height: 127px; }', app.STYLES_CSS)
        self.assertIn('object-fit: contain', app.STYLES_CSS)

    def test_official_logo_is_served_unchanged(self):
        expected_path = os.path.join(app.STATIC_DIR, app.PROVIDER_LOGO_FILENAME)
        with open(expected_path, 'rb') as image_file:
            expected = image_file.read()

        response = self.request(app.PROVIDER_LOGO_URL)

        self.assertEqual('200 OK', response['status'])
        self.assertEqual(expected, response['body'])
        self.assertEqual('image/png', response['headers']['Content-Type'])


if __name__ == '__main__':
    unittest.main()
