import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import server as app


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        app.STATE.update(cookie='', authorization='', ai={})
        app.COURSES.clear()

    def post(self, path, data, token=app.TOKEN, origin=None):
        headers = {'Content-Type': 'application/json', 'X-Local-Token': token}
        if origin:
            headers['Origin'] = origin
        request = urllib.request.Request(self.base + '/api/' + path, json.dumps(data).encode(), headers)
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as e:
            response = e
        return response.status, json.load(response)

    def test_course_normalization_and_links(self):
        items = app.normalize_courses({'result': {'courseOpenDtos': [{'courseId': 123, 'courseName': '<script>bad</script>', 'url': 'javascript:alert(1)'}]}})
        self.assertEqual(items[0]['id'], '123')
        self.assertEqual(items[0]['url'], '')
        self.assertEqual(app.safe_link('https://zhihuishu.com.evil.test/'), '')

    def test_invalid_chapters_and_duplicates(self):
        with self.assertRaises(app.AppError):
            app.validate_nodes([{'name': 'x', 'children': 'bad'}])
        with self.assertRaises(app.AppError):
            app.normalize_courses([{'id': '1'}, {'id': '1'}])

    def test_session_does_not_echo_credentials(self):
        status, data = self.post('session', {'cookie': 'session=secret', 'authorization': 'private-token'})
        self.assertEqual(status, 200)
        self.assertNotIn('secret', json.dumps(data))
        self.assertEqual(app.STATE['cookie'], 'session=secret')

    def test_local_csrf_boundaries(self):
        self.assertEqual(self.post('clear', {}, token='wrong')[0], 403)
        self.assertEqual(self.post('clear', {}, origin='https://example.com')[0], 403)
        request = urllib.request.Request(self.base + '/', headers={'Host': 'evil.test'})
        with self.assertRaises(urllib.error.HTTPError) as result:
            urllib.request.urlopen(request)
        self.assertEqual(result.exception.code, 403)

    def test_cookie_newline_rejected(self):
        self.assertEqual(self.post('session', {'cookie': 'a=b\r\nInjected: yes'})[0], 400)

    def test_platform_domain_and_credential_boundary(self):
        app.STATE.update(cookie='session=local', authorization='platform-token')
        with patch.object(app, 'request_json', return_value={'ok': True}) as request:
            for url in ['https://evil.test/api', 'http://zhihuishu.com/api', 'https://zhihuishu.com.evil.test/']:
                with self.assertRaises(app.AppError):
                    app.platform_request(url)
            request.assert_not_called()
            app.platform_request('https://onlineservice-api.zhihuishu.com/api', {'id': '1'})
            self.assertEqual(request.call_args.args[2]['Cookie'], 'session=local')

    def test_manual_tutor_endpoint_removed(self):
        self.assertEqual(self.post('ask', {'question': 'q'})[0], 400)

    def test_clear_and_unknown_path(self):
        app.STATE.update(cookie='a', authorization='b', ai={'key': 'c'})
        self.assertEqual(self.post('clear', {})[0], 200)
        self.assertEqual(app.STATE, {'cookie': '', 'authorization': '', 'ai': {}})
        self.assertEqual(self.post('unknown', {})[0], 400)

    def test_page_has_no_embedded_credentials(self):
        app.STATE['cookie'] = 'cookie-never-in-html'
        with urllib.request.urlopen(self.base) as response:
            html = response.read().decode()
            self.assertIn(app.TOKEN, html)
            self.assertNotIn('cookie-never-in-html', html)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_redirect_is_never_followed(self):
        with self.assertRaises(app.AppError):
            app.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test/')

    def test_live_course_route_binds_chapters_to_server_metadata(self):
        course = {'id': 'share:1:2', 'name': '课程', 'chapters': [],
                  'platform': {'kind': 'share', 'recruit_and_course_id': 'internal-course-id'}}
        with patch.object(app, 'adapter_call', return_value={'courses': [course], 'warnings': ['其他类型暂不可用'], 'scope': '共享课程'}) as call:
            status, data = self.post('courses', {})
            self.assertEqual(status, 200)
            self.assertNotIn('platform', data['courses'][0])
            self.assertEqual(data['warnings'], ['其他类型暂不可用'])
            self.assertEqual(app.COURSES[course['id']], course)
        with patch.object(app, 'adapter_call', return_value=[{'name': '第一章'}]) as call:
            status, data = self.post('chapters', {'course_id': course['id']})
            self.assertEqual(status, 200)
            call.assert_called_once_with('list_chapters', course)

    def test_cleared_session_cannot_reuse_cached_course(self):
        app.COURSES['share:1:2'] = {'id':'share:1:2'}
        self.post('clear', {})
        with patch.object(app, 'adapter_call') as call:
            status, data = self.post('chapters', {'course_id':'share:1:2'})
            self.assertEqual(status, 400)
            call.assert_not_called()

    def test_changing_session_during_read_discards_results(self):
        def changed(*args):
            app.STATE['cookie'] = 'different-account'
            return {'courses': [{'id':'one','name':'课程','chapters':[]}]}
        with patch.object(app, 'adapter_call', side_effect=changed):
            self.assertEqual(self.post('courses', {})[0], 400)
            self.assertFalse(app.COURSES)


if __name__ == '__main__':
    unittest.main()
