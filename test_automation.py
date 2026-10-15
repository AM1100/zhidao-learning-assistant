import threading
import unittest
from unittest.mock import Mock, patch

from automation import AutomationController, AutomationError, PlaybackRunner, decode_answer, solve_popup


class FakeVideo:
    def __init__(self):
        self.state = {'src':'video-one','time':20,'duration':30,'paused':False,'ended':False,'ready':4,'rate':1}
        self.actions = []

    def evaluate(self, script):
        if script == 'v => v.pause()':
            self.actions.append('pause'); self.state['paused'] = True
        elif script == 'v => v.play()':
            self.actions.append('play'); self.state['paused'] = False
        elif script == 'v => v.currentSrc || v.src':
            return self.state['src']
        else:
            return dict(self.state)


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.page = Mock()
        self.page.url = 'https://studyvideoh5.zhihuishu.com/stuStudy'
        self.page.is_closed.return_value = False
        self.video = FakeVideo()
        self.report = Mock()
        self.stop = threading.Event()
        self.pause = threading.Event()
        self.runner = PlaybackRunner(self.page,self.stop,self.pause,self.report)
        self.runner.video = Mock(return_value=self.video)
        self.runner.blockers = Mock(return_value=None)
        self.runner.process_popup = Mock(return_value=False)
        self.now = 100
        self.clock = patch('automation.time.monotonic', side_effect=lambda:self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_near_end_does_not_advance_until_real_ended(self):
        self.video.state['time'] = 29.99
        self.runner.tick()
        self.assertEqual(self.runner.completed,0)
        self.page.locator.assert_not_called()

    def test_ended_advances_once_after_platform_wait(self):
        self.video.state['ended'] = True
        next_button = Mock()
        next_button.count.return_value = 1
        self.page.locator.return_value = next_button
        self.runner.tick()
        self.assertEqual(self.runner.completed,1)
        next_button.click.assert_not_called()
        self.now += 5
        self.runner.tick()
        self.runner.tick()
        next_button.click.assert_called_once()
        self.assertEqual(self.runner.completed,1)

    def test_platform_auto_advance_prevents_duplicate_next(self):
        self.video.state['ended'] = True
        self.runner.tick()
        self.now += 5
        self.video.state.update(src='video-two',ended=False,time=0)
        self.runner.tick()
        self.page.locator.assert_not_called()

    def test_pause_and_resume_control_actual_media(self):
        self.runner.tick()
        self.now += 30
        self.pause.set()
        self.runner.tick()
        self.pause.clear()
        self.runner.tick()
        self.assertEqual(self.video.actions,['pause','play'])

    def test_popup_blocks_playback_advance(self):
        self.runner.process_popup.return_value = True
        self.video.state['ended'] = True
        self.runner.tick()
        self.assertEqual(self.runner.completed,0)

    def test_platform_verification_pauses_automation(self):
        self.runner.blockers.return_value = '需要人脸验证'
        self.runner.tick()
        self.assertFalse(self.pause.is_set())
        self.assertEqual(self.video.actions, ['pause'])
        self.assertEqual(self.report.call_args.args[0], 'waiting_user')

    def test_blocker_does_not_latch_pause_or_hide_reason_after_continue(self):
        self.runner.blockers.return_value = '需要关闭平台播放提示'
        self.runner.tick()
        self.video.state['paused'] = False  # user clicks play in the platform
        self.runner.resume_requested.set()
        self.runner.tick()
        self.runner.tick()
        self.assertFalse(self.pause.is_set())
        self.assertFalse(self.video.state['paused'])
        self.assertEqual(self.video.actions, ['pause'])
        self.assertEqual(self.report.call_args.args, ('waiting_user', '需要关闭平台播放提示'))
        self.runner.blockers.return_value = None
        self.runner.tick()
        self.assertEqual(self.report.call_args.args[0], 'starting')
        self.video.state['time'] += 1
        self.runner.tick()
        self.assertEqual(self.report.call_args.args[0], 'running')

    def test_explicit_continue_recovers_before_pause_tick_and_after_initial_play_window(self):
        self.runner.tick()
        self.now += 30
        self.video.state['paused'] = True
        self.runner.resume_requested.set()
        self.runner.tick()
        self.assertEqual(self.video.actions, ['play'])

    def test_explicit_continue_uses_manual_start_after_selector_timeout(self):
        from automation import UnlearnedStart
        selector = UnlearnedStart(self.report)
        selector.started -= 61
        self.runner.start_selector = selector
        self.runner.tick()
        self.assertTrue(self.pause.is_set())
        self.runner.tick()
        self.video.state['paused'] = False
        self.pause.clear()
        self.runner.resume_requested.set()
        self.runner.tick()
        self.assertTrue(selector.done)
        self.assertFalse(self.video.state['paused'])
        self.video.state['time'] += 1
        self.runner.tick()
        self.assertFalse(self.pause.is_set())
        self.assertEqual(self.report.call_args.args[0], 'running')

    def test_continue_is_retained_until_video_exists_and_progress_advances(self):
        self.runner.video.return_value = None
        self.runner.resume_requested.set()
        self.runner.tick()
        self.assertTrue(self.runner.resume_pending)
        self.runner.video.return_value = self.video
        self.video.state['paused'] = True
        self.runner.tick()
        self.assertEqual(self.video.actions, ['play'])
        self.runner.tick()
        self.assertEqual(self.report.call_args.args[0], 'starting')
        self.video.state['time'] += 1
        self.runner.tick()
        self.assertFalse(self.runner.resume_pending)
        self.assertEqual(self.report.call_args.args[0], 'running')

    def test_rejected_play_is_retried_without_losing_continue(self):
        self.runner.tick()
        self.video.state['paused'] = True
        self.now += 30
        action = Mock(side_effect=[RuntimeError('not ready'), None])
        self.runner.media_action = action
        self.runner.resume_requested.set()
        self.runner.tick()
        self.assertTrue(self.runner.resume_pending)
        self.now += 4
        self.runner.tick()
        self.assertEqual(action.call_count, 2)
        self.video.state.update(paused=False, time=21)
        self.runner.tick()
        self.assertEqual(self.report.call_args.args[0], 'running')

    def test_manual_play_resumes_a_manually_paused_task(self):
        self.runner.tick()
        self.pause.set()
        self.runner.tick()
        self.video.state.update(paused=False, time=21)
        self.runner.tick()
        self.assertFalse(self.pause.is_set())
        self.assertEqual(self.report.call_args.args[0], 'running')
        self.assertEqual(self.video.actions, ['pause'])

    def test_login_page_requires_user(self):
        self.page.url = 'https://passport.zhihuishu.com/login'
        self.runner.tick()
        self.runner.video.assert_not_called()
        self.assertEqual(self.report.call_args.args[0],'waiting_user')


class AnswerTests(unittest.TestCase):
    def test_single_and_multi_answer_validation(self):
        self.assertEqual(decode_answer('{"choices":[1],"confidence":0.96}',3,False),[1])
        self.assertEqual(decode_answer('```json\n{"choices":[2,0],"confidence":0.95}\n```',3,True),[0,2])

    def test_uncertain_malformed_and_out_of_range_answers_are_not_clicked(self):
        for text in ['oops','[]','{"choices":[3],"confidence":1}',
                     '{"choices":[true],"confidence":1}', '{"choices":[0,0],"confidence":1}',
                     '{"choices":[{}],"confidence":1}', '{"choices":[0],"confidence":0.5}']:
            with self.assertRaises((AutomationError,ValueError)):
                decode_answer(text,2,False)

    def test_ai_request_contains_no_platform_cookie(self):
        requester = Mock(return_value={'choices':[{'message':{'content':'{"choices":[0],"confidence":0.95}'}}]})
        ai = {'url':'https://ai.example.test/v1/chat/completions','model':'model','key':'ai-key'}
        solve_popup(ai,'练习题',['甲','乙'],False,requester)
        args = requester.call_args.args
        self.assertEqual(args[2], {'Authorization':'Bearer ai-key'})
        self.assertNotIn('Cookie',repr(args))

    def test_stop_is_idempotent_without_worker(self):
        controller = AutomationController(Mock())
        self.assertEqual(controller.command('stop')['state'],'stopped')
        with self.assertRaises(AutomationError):
            controller.command('resume')

    def test_untrusted_course_url_rejected_before_launch(self):
        controller = AutomationController(Mock())
        with self.assertRaises(AutomationError):
            controller.start({'name':'bad','url':'https://evil.test/'},'a=b',{},False)


class UnlearnedStartTests(unittest.TestCase):
    def setUp(self):
        from automation import UnlearnedStart
        self.selector = UnlearnedStart(Mock())
        self.chapters = [{'videoLessons': [
            {'id': 1, 'name': '同名课时'},
            {'id': 2, 'name': '父课时', 'videoSmallLessons': [
                {'id': 21, 'name': '进行中'}, {'id': 22, 'name': '同名课时'}]}]},
            {'videoLessons': [{'id': 3, 'name': '后续未学'}]}]
        self.progress = {'lesson': {'1': {'watchState': 1}},
                         'lv': {'21': {'watchState': 2}, '22': {'watchState': 0}}}

    def test_order_nested_and_started_are_distinguished(self):
        from automation import first_unlearned
        self.assertEqual(first_unlearned(self.chapters, self.progress),
                         {'chapter': 0, 'leaf': 2, 'name': '同名课时'})

    def test_missing_id_uses_official_default_only_after_valid_response(self):
        from automation import first_unlearned
        self.assertEqual(first_unlearned(self.chapters, {'lesson': {}, 'lv': {}})['leaf'], 0)
        for progress in [None, {}, {'lesson': {}}, {'lesson': [], 'lv': {}}]:
            with self.assertRaises(AutomationError):
                first_unlearned(self.chapters, progress)

    def test_all_started_and_unknown_status(self):
        from automation import first_unlearned
        chapters = [{'videoLessons': [{'id':1, 'name':'完成'}, {'id':2, 'name':'进行中'}]}]
        progress = {'lesson': {'1': {'watchState':1}, '2': {'watchState':2}}, 'lv': {}}
        self.assertIsNone(first_unlearned(chapters, progress))
        progress['lesson']['1']['watchState'] = 7
        with self.assertRaises(AutomationError):
            first_unlearned(chapters, progress)

    def test_bad_response_does_not_default_everything_to_unlearned(self):
        response = Mock(url='https://studyservice-api.zhihuishu.com/gateway/t/v1/learning/queryStuyInfo', status=500)
        response.json.return_value = {'code': -1, 'data': {}}
        self.selector.response(response)
        self.assertIsNone(self.selector.progress)
        response.status = 200
        response.json.return_value = {'code':0, 'data':self.progress}
        self.selector.response(response)
        self.assertEqual(self.selector.progress, self.progress)

    def test_wait_pauses_video_without_selecting_and_timeout_requires_manual(self):
        page, video, pause = Mock(), FakeVideo(), threading.Event()
        self.assertTrue(self.selector.tick(page,video,pause))
        self.assertTrue(self.selector.tick(page,video,pause))
        self.assertEqual(video.actions,['pause'])
        page.locator.assert_not_called()
        self.selector.started -= 61
        self.selector.tick(page,video,pause)
        self.assertTrue(pause.is_set())

    def test_duplicate_title_selection_uses_chapter_and_row_and_clicks_once(self):
        self.selector.chapters = self.chapters
        self.selector.progress = self.progress
        page, row = Mock(), Mock()
        chapters = page.locator.return_value
        chapters.count.return_value = 2
        rows = chapters.nth.return_value.locator.return_value
        rows.count.return_value = 3
        rows.nth.return_value = row
        row.locator.return_value.count.return_value = 1
        row.locator.return_value.get_attribute.return_value = '同名课时'
        row.get_attribute.return_value = 'clearfix video'
        pause = threading.Event()
        self.selector.tick(page,None,pause)
        self.selector.tick(page,None,pause)
        chapters.nth.assert_called_with(0)
        rows.nth.assert_called_with(2)
        row.click.assert_called_once()
        self.assertFalse(self.selector.done)
        row.get_attribute.return_value = 'clearfix video current_play'
        self.assertFalse(self.selector.tick(page,None,pause))
        self.assertTrue(self.selector.done)

    def test_already_selected_unlearned_video_is_not_paused(self):
        self.selector.chapters = self.chapters
        self.selector.progress = self.progress
        page, row, video = Mock(), Mock(), FakeVideo()
        chapters = page.locator.return_value
        chapters.count.return_value = 2
        rows = chapters.nth.return_value.locator.return_value
        rows.count.return_value = 3
        rows.nth.return_value = row
        row.locator.return_value.count.return_value = 1
        row.locator.return_value.get_attribute.return_value = '同名课时'
        row.get_attribute.return_value = 'clearfix video current_play'
        pause = threading.Event()
        self.assertFalse(self.selector.tick(page,video,pause))
        self.assertFalse(self.selector.tick(page,video,pause))
        self.assertEqual(video.actions, [])
        row.click.assert_not_called()

    def test_target_is_frozen_and_new_video_is_not_paused_during_confirmation(self):
        self.selector.chapters = self.chapters
        self.selector.progress = self.progress
        page, row, video = Mock(), Mock(), FakeVideo()
        chapters = page.locator.return_value
        chapters.count.return_value = 2
        rows = chapters.nth.return_value.locator.return_value
        rows.count.return_value = 3
        rows.nth.return_value = row
        row.locator.return_value.count.return_value = 1
        row.locator.return_value.get_attribute.return_value = '同名课时'
        row.get_attribute.return_value = 'clearfix video'
        pause = threading.Event()
        self.selector.tick(page,video,pause)
        self.assertEqual(video.actions, ['pause'])
        self.progress['lv']['22']['watchState'] = 2
        row.get_attribute.return_value = 'clearfix video current_play'
        video.state.update(src='new-video', paused=False)
        self.assertFalse(self.selector.tick(page,video,pause))
        rows.nth.assert_called_with(2)
        self.assertEqual(video.actions, ['pause'])
        self.assertFalse(video.state['paused'])

    def test_no_unstarted_does_not_click_first_lesson(self):
        self.selector.chapters = [{'videoLessons':[{'id':1,'name':'完成'}]}]
        self.selector.progress = {'lesson':{'1':{'watchState':1}},'lv':{}}
        page, pause = Mock(), threading.Event()
        self.selector.tick(page,None,pause)
        self.assertTrue(pause.is_set())
        page.locator.assert_not_called()


if __name__ == '__main__':
    unittest.main()
