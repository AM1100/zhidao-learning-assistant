import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import Mock, patch

import browser_runtime as runtime
from automation import AutomationController, AutomationError


class DefaultBrowserTests(unittest.TestCase):
    def test_supported_browsers_do_not_require_chrome(self):
        for name in ['msedge.exe', 'microsoft-edge-stable', 'chromium', 'brave-browser', 'opera']:
            with self.subTest(name=name):
                self.assertTrue(runtime.DefaultBrowser('default', name).controllable)
        for name in ['firefox', 'Safari', 'unknown', 'fake-chrome']:
            with self.subTest(name=name):
                self.assertFalse(runtime.DefaultBrowser('default', name).controllable)

    def test_linux_reads_default_desktop_and_discards_profile_flags(self):
        with tempfile.TemporaryDirectory(prefix='browser test ') as directory:
            root = Path(directory)
            (root/'applications').mkdir()
            (root/'applications/default.desktop').write_text(
                '[Desktop Entry]\nName=Edge\nExec="/test path/msedge" --profile-directory=Personal %U\n')
            with patch.dict(os.environ, {'XDG_DATA_HOME': directory, 'XDG_DATA_DIRS': directory}), \
                    patch.object(runtime, 'read_command', return_value='default.desktop'), \
                    patch.object(runtime.shutil, 'which', return_value='/test path/msedge') as which:
                browser = runtime.linux_browser()
            self.assertEqual(browser, runtime.DefaultBrowser('Edge', '/test path/msedge'))
            which.assert_called_once_with('/test path/msedge')

    def test_linux_falls_back_to_xdg_settings_when_mime_unavailable(self):
        with patch.object(runtime, 'read_command', side_effect=[OSError(), 'missing.desktop']) as command, \
                patch.object(Path, 'is_file', return_value=False):
            self.assertFalse(runtime.linux_browser().controllable)
        self.assertEqual(command.call_args.args[0], ['xdg-settings', 'get', 'default-web-browser'])

    def test_linux_rejects_desktop_path(self):
        with patch.object(runtime, 'read_command', return_value='../outside.desktop'):
            self.assertFalse(runtime.linux_browser().controllable)

    def test_windows_reads_https_progid_and_quoted_executable(self):
        registry = Mock()
        registry.HKEY_CURRENT_USER = 'HKCU'
        registry.HKEY_CLASSES_ROOT = 'HKCR'
        registry.OpenKey = Mock(return_value=unittest.mock.MagicMock())
        registry.QueryValueEx.side_effect = [('MSEdgeHTM', 1), ('"C:\\Program Files\\Edge\\msedge.exe" --single-argument %1', 1)]
        with patch.dict('sys.modules', {'winreg': registry}), patch.object(Path, 'is_file', return_value=True):
            browser = runtime.windows_browser()
        self.assertEqual(browser.executable, r'C:\Program Files\Edge\msedge.exe')
        self.assertIn('https', registry.OpenKey.call_args_list[0].args[1])

    def test_macos_uses_https_default_bundle_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)/'Edge.app'
            (app/'Contents/MacOS').mkdir(parents=True)
            executable = app/'Contents/MacOS/Microsoft Edge'
            executable.touch()
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps(
                {'CFBundleName': 'Edge', 'CFBundleExecutable': 'Microsoft Edge'}))
            handlers = {'LSHandlers': [{'LSHandlerURLScheme': 'https', 'LSHandlerRoleAll': 'com.microsoft.edgemac'}]}
            with patch.object(runtime.subprocess, 'check_output', return_value=plistlib.dumps(handlers)), \
                    patch.object(runtime, 'read_command', return_value=str(app)) as command:
                browser = runtime.mac_browser()
            self.assertEqual(browser.executable, str(executable))
            self.assertTrue(browser.controllable)
            self.assertEqual(command.call_args.args[0][-1], 'com.microsoft.edgemac')

    def test_detection_failure_does_not_choose_chrome(self):
        with patch.object(runtime.sys, 'platform', 'linux'), patch.object(runtime, 'linux_browser', side_effect=OSError()):
            self.assertFalse(runtime.detect_default_browser().controllable)

    def test_launch_uses_only_selected_executable(self):
        playwright = Mock()
        runtime.launch_default(playwright, runtime.DefaultBrowser('Edge', '/test/msedge'))
        playwright.chromium.launch.assert_called_once_with(executable_path='/test/msedge', headless=False)

    def test_open_default_delegates_url_without_shell_or_credentials(self):
        url = 'https://studyvideoh5.zhihuishu.com/videoStudy.html'
        with patch.object(runtime.sys, 'platform', 'linux'), patch.object(runtime.subprocess, 'Popen') as launch:
            runtime.open_default(url)
        self.assertEqual(launch.call_args.args, (['xdg-open', url],))
        self.assertNotIn('shell', launch.call_args.kwargs)


class BrowserControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = AutomationController(Mock())
        self.course = {'name': 'test', 'url': 'https://studyvideoh5.zhihuishu.com/videoStudy.html'}

    def test_unsupported_default_opens_manual_without_playwright_or_ai(self):
        with patch('automation.detect_default_browser', return_value=runtime.DefaultBrowser('Firefox', '/usr/bin/firefox')), \
                patch('automation.open_default') as open_url, patch('automation.importlib.util.find_spec') as spec:
            result = self.controller.start(self.course, 'private-cookie', {}, True)
        self.assertEqual(result['state'], 'manual')
        self.assertIsNone(self.controller.thread)
        spec.assert_not_called()
        open_url.assert_called_once_with(self.course['url'])
        self.assertNotIn('private-cookie', str(result))
        with self.assertRaises(AutomationError):
            self.controller.command('resume')

    def test_supported_default_is_passed_to_worker(self):
        browser = runtime.DefaultBrowser('Edge', '/usr/bin/msedge')
        with patch('automation.detect_default_browser', return_value=browser), \
                patch('automation.importlib.util.find_spec', return_value=object()), \
                patch('automation.threading.Thread') as thread:
            result = self.controller.start(self.course, 'private-cookie', {}, False)
        self.assertEqual(result['state'], 'starting')
        self.assertEqual(thread.call_args.kwargs['args'][-1], browser)
        thread.return_value.start.assert_called_once()

    def test_manual_open_error_does_not_report_success(self):
        with patch('automation.detect_default_browser', return_value=runtime.DefaultBrowser('Safari')), \
                patch('automation.open_default', side_effect=OSError()):
            with self.assertRaises(AutomationError):
                self.controller.start(self.course, 'cookie', {}, False)

    def test_untrusted_url_never_reaches_os_browser(self):
        with patch('automation.open_default') as open_url:
            with self.assertRaises(AutomationError):
                self.controller.start({'name': 'bad', 'url': 'file:///etc/passwd'}, 'cookie', {}, False)
        open_url.assert_not_called()

    def test_launch_failure_reports_selected_browser_without_fallback_or_raw_error(self):
        browser = runtime.DefaultBrowser('Edge', '/test/msedge')
        with patch('automation.load_sessions', return_value=Mock()) as sessions, \
                patch('playwright.sync_api.sync_playwright'), \
                patch('automation.launch_default', side_effect=RuntimeError('private-cookie')) as launch:
            sessions.return_value.StudySession.return_value.jar = []
            self.controller.worker(self.course, 'private-cookie', {}, False, False, browser)
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[1], browser)
        result = self.controller.snapshot()
        self.assertEqual(result['state'], 'error')
        self.assertIn('Edge', result['message'])
        self.assertNotIn('private-cookie', result['message'])


if __name__ == '__main__':
    unittest.main()
