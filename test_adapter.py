import base64
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import server


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / 'plugins' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapter = load('test_platform_adapter', 'platform_adapter.py')
sessions = load('test_study_session', 'study_session.py')


class AdapterTests(unittest.TestCase):
    def test_encrypt_matches_independent_node_crypto_vector(self):
        actual = adapter.encode_payload({'x': '中文', 'n': 1}, b'0123456789abcdef')
        self.assertEqual(actual, 'mGKlltuTEu98/UQyQNvf4b9LiA3gA5ye8b0dROrPF9I=')

    def test_public_rsa_recovery_and_padding_validation(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        pub = base64.b64encode(private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
        payload = json.dumps({'cKey': '0123456789abcdef'}).encode()
        block = b'\0\x01' + b'\xff' * (128-len(payload)-3) + b'\0' + payload
        numbers = private.private_numbers()
        signed = pow(int.from_bytes(block, 'big'), numbers.d, numbers.public_numbers.n).to_bytes(128, 'big')
        with patch.object(adapter, 'PUBLIC_KEY', pub):
            self.assertEqual(adapter.recover_key(base64.b64encode(signed).decode()), b'0123456789abcdef')
            with self.assertRaises(adapter.AdapterError):
                adapter.recover_key(base64.b64encode(b'\0'*128).decode())

    def test_pagination_reads_current_and_completed(self):
        client = Mock()
        client.call.side_effect = [
            {'result': {'courseOpenDtos': [{'courseId': 1}, {'courseId': 2}]}},
            {'result': {'courseOpenDtos': [{'courseId': 3}]}},
            {'result': {'courseOpenDtos': [{'courseId': 1}, {'courseId': 4}]}},
            {'result': {'courseOpenDtos': []}}]
        with patch.object(adapter, 'PAGE_SIZE', 2):
            courses = adapter.read_shared(client)
        self.assertEqual(len(courses), 4)
        self.assertEqual([c.args[1]['pageNo'] for c in client.call.call_args_list], [1,2,1,2])

    def test_unrecognized_response_is_not_an_empty_course_list(self):
        client = Mock()
        client.call.return_value = {'result': None}
        with self.assertRaises(adapter.AdapterError):
            adapter.read_shared(client)

    def test_upstream_business_failure_is_not_success(self):
        for data in ({'code': 401}, {'status': 422}, {'code': 500}, {'result': []}):
            with self.assertRaises(adapter.AdapterError):
                adapter.check_response(data, '测试接口')

    def test_old_session_is_not_reused_for_video_service(self):
        session = sessions.StudySession('CASLOGC=real-common-cookie; CASTGC=real-sso-cookie; SESSION=wrong-home-session; SERVERID=wrong-home-server; acw_tc=home-firewall')
        cookies = {c.name: c.value for c in session.jar}
        self.assertEqual(cookies, {'CASLOGC': 'real-common-cookie', 'CASTGC': 'real-sso-cookie'})
        self.assertNotIn('SESSION', cookies)

    def test_sso_redirect_cannot_leave_trusted_domains(self):
        for url in ('http://zhihuishu.com/', 'https://zhihuishu.com.evil.test/', 'https://evil.test/'):
            with self.assertRaises(sessions.SessionError):
                sessions.SafeRedirect().redirect_request(None,None,302,'',{},url)

    def test_tree_normalization_has_three_levels(self):
        tree = adapter.chapter_nodes([{'name':'第一章','videoLessons':[{'name':'第一节','videoSmallLessons':[{'name':'视频一'}]}]}])
        self.assertEqual(tree, [{'name':'第一章','children':[{'name':'第一节','children':[{'name':'视频一'}]}]}])

    def test_url_encoded_identifier_decoded_exactly_once(self):
        item = adapter.course_record({'courseId': 1, 'secret': 'abc%2Bdef%3D', 'recruitId': 2}, 'share')
        self.assertEqual(item['platform']['recruit_and_course_id'], 'abc+def=')
        self.assertIn('abc%2Bdef%3D', item['url'])
        self.assertNotIn('%252B', item['url'])

    def test_backend_keeps_chapter_binding_out_of_browser_response(self):
        raw = {'id':'share:1:2','name':'课程','chapters':[], 'platform': {'recruit_and_course_id': 'private-course-key'}}
        self.assertNotIn('platform', server.normalize_courses([raw])[0])


if __name__ == '__main__':
    unittest.main()
