import os.path
import json
import sys
import unittest
from swift.common import swob, utils
from swift.common.swob import Request
from oioswift.common.middleware import container_hierarchy

# Hack PYTHONPATH so "test" is swift's test directory
sys.path.insert(1, os.path.abspath(os.path.join(__file__, '../../../../..')))
from test.unit.common.middleware.helpers import FakeSwift  # noqa


class OioContainerHierarchy(unittest.TestCase):
    def setUp(self):
        conf = {'sds_default_account': 'OPENIO'}
        self.filter_conf = {
            'strip_v1': 'true',
            'swift3_compat': 'true',
            'account_first': 'true',
            'create_dir_placeholders': 'true',
            'recursive_placeholders': 'true',
            'sds_default_account': 'OPENIO'}
        self.app = FakeSwift()
        self.ch = container_hierarchy.filter_factory(
            conf,
            **self.filter_conf)(self.app)

    def call_app(self, req, app=None):
        if app is None:
            app = self.app

        self.authorized = []

        def authorize(req):
            self.authorized.append(req)

        if 'swift.authorize' not in req.environ:
            req.environ['swift.authorize'] = authorize

        req.headers.setdefault("User-Agent", "Melted Cheddar")

        status = [None]
        headers = [None]

        def start_response(s, h, ei=None):
            status[0] = s
            headers[0] = h

        body_iter = app(req.environ, start_response)
        with utils.closing_if_possible(body_iter):
            body = b''.join(body_iter)

        return status[0], headers[0], body

    def call_ch(self, req):
        return self.call_app(req, app=self.ch)

    def test_simple_put(self):
        """check number of request generated by Container Hierarchy"""
        self.app.register(
            'PUT', '/v1/a/c%2Fd1%2Fd2%2Fd3/o', swob.HTTPCreated, {})
        # placeholders
        self.app.register(
            'PUT', '/v1/a/c%2Fd1%2Fd2/d3/', swob.HTTPOk, {})
        self.app.register(
            'PUT', '/v1/a/c%2Fd1/d2/', swob.HTTPOk, {})
        self.app.register(
            'PUT', '/v1/a/c/d1/', swob.HTTPOk, {})

        req = Request.blank('/v1/a/c/d1/d2/d3/o', method='PUT')
        resp = self.call_ch(req)

        self.assertEqual(resp[0], '201 Created')
        self.assertEqual(
            [('PUT', '/v1/a/c%2Fd1%2Fd2/d3/'),
             ('PUT', '/v1/a/c%2Fd1/d2/'),
             ('PUT', '/v1/a/c/d1/'),
             ('PUT', '/v1/a/c%2Fd1%2Fd2%2Fd3/o')],
            self.app.calls)

    def test_placeholder_put(self):
        """stop at first placeholder already present"""
        self.app.register(
            'PUT', '/v1/a/c%2Fd1%2Fd2%2Fd3/o', swob.HTTPCreated, {})
        # placeholder already set
        self.app.register(
            'PUT', '/v1/a/c%2Fd1%2Fd2/d3/', swob.HTTPPreconditionFailed, {})
        req = Request.blank('/v1/a/c/d1/d2/d3/o', method='PUT')
        resp = self.call_ch(req)
        self.assertEqual(resp[0], '201 Created')
        self.assertEqual(len(self.app.calls), 2)

    def test_placeholder_failed_put(self):
        """simulate issue when creating placeholder"""
        self.app.register(
            'PUT', '/v1/a/c%2Fd1%2Fd2%2Fd3/o', swob.HTTPCreated, {})
        # issue on placeholder
        self.app.register(
            'PUT', '/v1/a/c%2Fd1%2Fd2/d3/', swob.HTTPRequestTimeout, {})
        # other placeholders are ok
        self.app.register(
            'PUT', '/v1/a/c%2Fd1/d2/', swob.HTTPOk, {})
        self.app.register(
            'PUT', '/v1/a/c/d1/', swob.HTTPOk, {})
        req = Request.blank('/v1/a/c/d1/d2/d3/o', method='PUT')
        resp = self.call_ch(req)

        self.assertEqual(resp[0], '201 Created')

    def test_get(self):
        self.app.register(
            'GET', '/v1/a/c%2Fd1%2Fd2%2Fd3/o', swob.HTTPOk, {})
        req = Request.blank('/v1/a/c/d1/d2/d3/o', method='GET')
        resp = self.call_ch(req)
        self.assertEqual(resp[0], '200 OK')

    def test_recursive_listing(self):
        self.app.register(
            'GET',
            '/v1/a/c%2Fd1%2Fd2?prefix=&limit=10000&delimiter=%2F&format=json',
            swob.HTTPOk, {}, json.dumps([{"subdir": "d3/"}]))

        self.app.register(
            'GET',
            '/v1/a/c%2Fd1%2Fd2%2Fd3?prefix=&limit=10000&delimiter=%2F&format=json', # noqa
            swob.HTTPOk, {},
            json.dumps([{"hash": "d41d8cd98f00b204e9800998ecf8427e",
                         "last_modified": "2018-04-20T09:40:59.000000",
                         "bytes": 0, "name": "o",
                         "content_type": "application/octet-stream"}]))

        req = Request.blank('/v1/a/c?prefix=d1%2Fd2%2F', method='GET')
        resp = self.call_ch(req)

        data = json.loads(resp[2])
        self.assertEqual(data[0]['name'], 'd1/d2/d3/o')

    def test_global_listing(self):
        self.app.register(
            'GET', '/v1/a', swob.HTTPOk, {})

        req = Request.blank('/v1/a', method='GET')
        resp = self.call_ch(req)
        self.assertEqual(resp[0], '200 OK')

    def test_delete_object(self):
        self.app._responses = {}
        self.app.register('DELETE', '/v1/a/d1%2Fd2%2Fd3/o', swob.HTTPOk, {})

        req = Request.blank('/v1/a/d1/d2/d3/o', method='DELETE')
        resp = self.call_ch(req)

        self.app.register(
            'GET', '/v1/a/d1%2Fd2%2Fd3?delimiter=%2F&limit=1&prefix=&format=json', # noqa
            swob.HTTPOk, {})
        self.app.register(
            'DELETE', '/v1/a/d1%2Fd2/d3/', swob.HTTPNoContent, {})

        # distcp job create and remove its own placeholder and we wan
        # to refuse them if any object are still available
        req = Request.blank('/v1/a/d1/d2/d3/', method='DELETE')
        resp = self.call_ch(req)
        self.assertEqual(resp[0], '204 No Content')
