import os
import unittest

from fastapi.testclient import TestClient
from voice_agent.admin import app


class TestAdmin(unittest.TestCase):
    def setUp(self):
        self.before_user = os.environ.get('ADMIN_USERNAME')
        self.before_pass = os.environ.get('ADMIN_PASSWORD')
        os.environ['ADMIN_USERNAME'] = 'admin-test'
        os.environ['ADMIN_PASSWORD'] = 'a-very-long-test-password'
        self.client = TestClient(app)

    def tearDown(self):
        for key, val in [('ADMIN_USERNAME', self.before_user), ('ADMIN_PASSWORD', self.before_pass)]:
            if val is None: os.environ.pop(key, None)
            else: os.environ[key] = val

    def test_swagger_requires_password(self):
        self.assertEqual(self.client.get('/docs').status_code, 401)
        self.assertEqual(self.client.get('/openapi.json').status_code, 401)
        self.assertEqual(self.client.get('/admin/config').status_code, 401)

    def test_correct_password_allows_read(self):
        r = self.client.get('/docs', auth=('admin-test','a-very-long-test-password'))
        self.assertEqual(r.status_code, 200)
        r = self.client.get('/admin/config', auth=('admin-test','a-very-long-test-password'))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('hf_', str(r.json()))


if __name__ == '__main__': unittest.main()
