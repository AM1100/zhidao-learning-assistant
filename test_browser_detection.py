"""Exercise challenge detection against real CSS/layout in an isolated page."""
import unittest
import base64
import io
import wave
from unittest.mock import Mock, patch

from automation import AutomationController, PlaybackRunner, verification_blocker

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'requires playwright and installed Chrome')
class VerificationVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = sync_playwright().start()
        try:
            cls.browser = cls.runtime.chromium.launch(channel='chrome', headless=True)
        except Exception:
            cls.runtime.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width':1000, 'height':700})
        self.addCleanup(self.page.close)

    def html(self, body):
        self.page.set_content('<style>.control {width:280px;height:44px;background:#ddd}</style>' + body)

    def test_loaded_empty_sdk_shell_is_not_a_challenge(self):
        self.html('<div class="yidun_modal control"></div><div class="yidun_popup control"></div>')
        self.assertIsNone(verification_blocker(self.page))

    def test_transparent_ancestor_not_visible_even_if_playwright_says_visible(self):
        self.html('<div style="opacity:0"><div class="yidun_popup"><div class="yidun_control control">拖动验证</div></div></div>')
        self.assertTrue(self.page.locator('.yidun_popup').is_visible())
        self.assertIsNone(verification_blocker(self.page))

    def test_hidden_inert_or_aria_hidden_ancestor_is_not_a_challenge(self):
        for attribute in ['hidden', 'inert', 'aria-hidden="true"', 'style="visibility:hidden"']:
            with self.subTest(attribute=attribute):
                self.html('<div ' + attribute + '><div class="yidun_popup"><div class="yidun_control control"></div></div></div>')
                self.assertIsNone(verification_blocker(self.page))

    def test_offscreen_captcha_not_a_challenge(self):
        self.html('<div class="yidun_popup" style="position:fixed;left:-1000px"><div class="yidun_control control"></div></div>')
        self.assertIsNone(verification_blocker(self.page))

    def test_actual_slider_is_detected_and_clears_after_close(self):
        self.html('<div class="yidun_popup"><div class="yidun_control control">拖动验证</div></div>')
        self.assertIn('可见的滑块', verification_blocker(self.page))
        self.page.locator('.yidun_popup').evaluate("e => e.style.opacity = '0'")
        self.assertIsNone(verification_blocker(self.page))

    def test_persistent_sdk_button_and_iframe_without_dialog_are_not_challenges(self):
        self.html('<div class="nc-container"><div class="nc_scale control">已通过</div></div><iframe src="about:blank#captcha" title="验证码组件"></iframe>')
        self.assertIsNone(verification_blocker(self.page))

    def test_visible_captcha_iframe_in_dialog_is_detected(self):
        self.html('<div role="dialog"><iframe src="about:blank#captcha" title="验证码窗口"></iframe></div>')
        self.assertIn('验证码窗口', verification_blocker(self.page))

    def test_visible_face_check_is_detected(self):
        self.html('<iframe src="about:blank#faceIdentity" title="人脸验证"></iframe>')
        self.assertIn('人脸验证窗口', verification_blocker(self.page))

    def playback_fixture(self):
        data = io.BytesIO()
        with wave.open(data, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b'\x00\x00' * 8000 * 20)
        source = 'data:audio/wav;base64,' + base64.b64encode(data.getvalue()).decode()
        html = ("<div id='container'><video muted playsinline width='480' height='270' src='" + source + "'></video>"
                "<button id='playButton' class='playButton'>播放/暂停</button></div>"
                "<script>(()=>{const v=document.querySelector('video'), b=document.querySelector('#playButton');"
                "b.onclick=()=>{b.dataset.clicks=Number(b.dataset.clicks||0)+1;v.paused?v.play():v.pause()};"
                "v.onplay=()=>b.className='pauseButton';v.onpause=()=>b.className='playButton';})();</script>")
        self.page.route('https://studyvideoh5.zhihuishu.com/**', lambda route: route.fulfill(body=html,content_type='text/html'))
        self.page.goto('https://studyvideoh5.zhihuishu.com/stuStudy')
        self.page.wait_for_function("document.querySelector('video').readyState >= 2")
        controller = AutomationController(Mock())
        controller.thread = Mock()
        controller.thread.is_alive.return_value = True
        runner = PlaybackRunner(self.page,controller.stop,controller.pause,controller.report,
                                resume_event=controller.resume_requested)
        return controller, runner, html

    def assert_playback_advances(self, runner):
        before = self.page.locator('video').evaluate('v => v.currentTime')
        self.page.wait_for_function("before => document.querySelector('video').currentTime > before + .08",arg=before,timeout=4000)
        runner.tick()

    def test_resume_command_clicks_native_sdk_button_and_confirms_real_progress(self):
        controller, runner, _ = self.playback_fixture()
        self.page.locator('#playButton').click()
        self.assert_playback_advances(runner)
        controller.command('pause')
        runner.tick()
        self.assertTrue(self.page.locator('video').evaluate('v => v.paused'))
        self.assertEqual(self.page.locator('#playButton').get_attribute('class'),'playButton')
        controller.command('resume')
        runner.tick()
        self.assertEqual(controller.snapshot()['state'],'starting')
        self.assert_playback_advances(runner)
        self.assertEqual(controller.snapshot()['state'],'running')
        self.assertEqual(self.page.locator('#playButton').get_attribute('data-clicks'),'3')

    def test_manual_play_is_recognized_without_another_continue_command(self):
        controller, runner, _ = self.playback_fixture()
        self.page.locator('#playButton').click()
        self.assert_playback_advances(runner)
        controller.command('pause')
        runner.tick()
        self.page.locator('#playButton').click()
        self.assert_playback_advances(runner)
        self.assertFalse(controller.pause.is_set())
        self.assertEqual(controller.snapshot()['state'],'running')
        self.assertEqual(self.page.locator('#playButton').get_attribute('data-clicks'),'3')

    def test_resume_waits_for_delayed_player_then_actually_plays(self):
        controller, runner, html = self.playback_fixture()
        self.page.set_content('<p>播放器加载中</p>')
        controller.command('resume')
        runner.tick()
        self.assertTrue(runner.resume_pending)
        self.page.set_content(html)
        self.page.wait_for_function("document.querySelector('video').readyState >= 2")
        runner.tick()
        self.assert_playback_advances(runner)
        self.assertEqual(controller.snapshot()['state'],'running')

    def test_resume_survives_dialog_until_it_closes(self):
        controller, runner, _ = self.playback_fixture()
        self.page.evaluate("() => {const d=document.createElement('div');d.setAttribute('role','dialog');d.textContent='平台提示需要处理';document.body.append(d)}")
        controller.command('resume')
        runner.tick()
        self.assertEqual(controller.snapshot()['state'],'waiting_user')
        self.assertTrue(runner.resume_pending)
        self.page.locator('[role=dialog]').evaluate('e => e.remove()')
        runner.tick()
        self.assert_playback_advances(runner)
        self.assertEqual(controller.snapshot()['state'],'running')

    def test_player_in_iframe_selected_over_main_page_preview(self):
        _, runner, _ = self.playback_fixture()
        self.page.set_content("<video width='600' height='400' data-kind='preview'></video><iframe width='500' height='300'></iframe>")
        frame = self.page.frames[1]
        frame.set_content("<div id='container'><video width='480' height='270' data-kind='course'></video></div>")
        self.assertEqual(runner.video().get_attribute('data-kind'),'course')

    def practice_fixture(self, types=('判断题',)):
        controller, runner, html = self.playback_fixture()
        self.page.route('https://wisdom-mooc.zhihuishu.com/**', lambda route: route.fulfill(body=html,content_type='text/html'))
        self.page.goto('https://wisdom-mooc.zhihuishu.com/study/index')
        self.page.wait_for_function("document.querySelector('video').readyState >= 2")
        self.page.evaluate("""types => {
            const popup=document.createElement('div');
            popup.className='el-dialog ai-class-exercise-dialog';popup.setAttribute('role','dialog');
            popup.innerHTML=`<div class="ai-class-exercise-dialog-header"><p class="tit">AI随堂练习</p>
                <p class="tips">以下所有内容均由AI生成请注意甄别</p><button class="header-icon">关闭</button></div>
                <div class="ques-list"></div><div class="dialog-footer"><button class="btn">提交作答</button></div>`;
            types.forEach((type,i)=>{
                const row=document.createElement('div');row.className='item';
                row.innerHTML=`<div class="row"><span class="type">${i+1}. [${type}]</span><span class="question-info">测试练习题${i+1}</span></div>`;
                ['选项甲','选项乙','选项丙'].forEach((answer,j)=>{
                    const option=document.createElement('div');option.className='option';
                    option.innerHTML=`<span class="class-question-select">${String.fromCharCode(65+j)}</span><span class="answer">${answer}</span>`;
                    option.onclick=()=>{
                        if(type!=='多选题') row.querySelectorAll('.isSelect').forEach(e=>e.classList.remove('isSelect'));
                        option.firstChild.classList.toggle('isSelect');
                    };row.append(option);
                });popup.querySelector('.ques-list').append(row);
            });
            popup.querySelector('.btn').onclick=()=>{
                document.body.dataset.submits=Number(document.body.dataset.submits||0)+1;
                const b=popup.querySelector('.btn');b.classList.add('is-finish');b.textContent='已提交';
            };
            popup.querySelector('.header-icon').onclick=()=>popup.remove();
            const overlay=document.createElement('div');overlay.className='el-overlay-dialog';overlay.setAttribute('role','dialog');
            popup.removeAttribute('role');overlay.append(popup);document.body.append(overlay);
        }""", list(types))
        return controller, runner

    def test_wisdom_practice_recognized_answered_submitted_and_closed(self):
        controller, runner = self.practice_fixture()
        runner.solver = Mock(return_value=[1])
        self.assertIsNone(runner.blockers())
        runner.tick()
        runner.solver.assert_called_once_with('1. [判断题]测试练习题1',['选项甲','选项乙','选项丙'],False)
        self.assertEqual(self.page.locator('.isSelect').inner_text(),'B')
        self.assertEqual(self.page.locator('body').get_attribute('data-submits'),'1')
        runner.tick()
        self.assertEqual(self.page.locator('.ai-class-exercise-dialog').count(),0)
        self.assertEqual(runner.solver.call_count,1)
        runner.tick()
        self.assert_playback_advances(runner)
        self.assertEqual(controller.snapshot()['state'],'running')

    def test_wisdom_multiquestion_and_manual_selection_reconciliation(self):
        _, runner = self.practice_fixture(('单选题','多选题'))
        self.page.locator('.item').nth(0).locator('.option').nth(0).click()
        self.page.locator('.item').nth(1).locator('.option').nth(1).click()
        runner.solver = Mock(side_effect=[[1],[0,2]])
        runner.tick()
        self.assertEqual(self.page.locator('.item').nth(0).locator('.isSelect').all_text_contents(),['B'])
        self.assertEqual(self.page.locator('.item').nth(1).locator('.isSelect').all_text_contents(),['A','C'])
        self.assertEqual(self.page.locator('body').get_attribute('data-submits'),'1')

    def test_wisdom_no_solver_does_not_submit_or_close(self):
        controller, runner = self.practice_fixture()
        runner.tick()
        self.assertEqual(controller.snapshot()['state'],'waiting_user')
        self.assertIn('未启用自动答题',controller.snapshot()['message'])
        self.assertIsNone(self.page.locator('body').get_attribute('data-submits'))
        self.assertEqual(self.page.locator('.ai-class-exercise-dialog').count(),1)

    def test_wisdom_failure_is_not_hidden_or_retried_next_tick(self):
        from automation import AutomationError
        controller, runner = self.practice_fixture()
        runner.solver = Mock(side_effect=AutomationError('AI 置信度不足'))
        runner.tick()
        runner.tick()
        self.assertIn('置信度不足',controller.snapshot()['message'])
        self.assertEqual(runner.solver.call_count,1)
        self.assertIsNone(self.page.locator('body').get_attribute('data-submits'))

    def test_wisdom_changed_question_before_ai_response_cancels_clicks(self):
        _, runner = self.practice_fixture()
        def solve(*args):
            self.page.locator('.question-info').evaluate("e => e.textContent='另一道题'")
            return [0]
        runner.solver = Mock(side_effect=solve)
        runner.tick()
        self.assertEqual(self.page.locator('.isSelect').count(),0)
        self.assertIsNone(self.page.locator('body').get_attribute('data-submits'))

    def test_wisdom_pause_during_ai_cancels_clicks(self):
        controller, runner = self.practice_fixture()
        def solve(*args):
            controller.pause.set()
            return [0]
        runner.solver = Mock(side_effect=solve)
        runner.tick()
        self.assertEqual(self.page.locator('.isSelect').count(),0)
        self.assertIsNone(self.page.locator('body').get_attribute('data-submits'))

    def test_wisdom_unconfirmed_submission_not_duplicated(self):
        _, runner = self.practice_fixture()
        self.page.locator('.dialog-footer .btn').evaluate("e => e.onclick=()=>document.body.dataset.submits=Number(document.body.dataset.submits||0)+1")
        runner.solver = Mock(return_value=[0])
        runner.tick()
        runner.tick()
        self.assertEqual(runner.solver.call_count,1)
        self.assertEqual(self.page.locator('body').get_attribute('data-submits'),'1')

    def test_similar_dialog_without_confirmed_header_still_blocks(self):
        _, runner = self.practice_fixture()
        self.page.locator('.tit').evaluate("e => e.textContent='课程考试'")
        self.assertIsNotNone(runner.blockers())
        runner.solver = Mock(return_value=[0])
        runner.tick()
        runner.solver.assert_not_called()

    def notice_fixture(self):
        controller, runner = self.practice_fixture()
        self.page.locator('.ai-class-exercise-dialog').evaluate("e => e.style.display='none'")
        self.page.evaluate("""() => {
            const notice=document.createElement('div');
            notice.className='el-dialog ai-notice-dialog';notice.setAttribute('role','dialog');
            notice.innerHTML='<img class="teacher-img" alt="引导人物"><div class="content-wrapper"><div class="text">videoConfigTip01</div><button class="btn">好的</button></div>';
            notice.querySelector('.btn').onclick=()=>document.body.dataset.noticeClicks=Number(document.body.dataset.noticeClicks||0)+1;
            const overlay=document.createElement('div');overlay.className='el-overlay-dialog';overlay.setAttribute('role','dialog');
            notice.removeAttribute('role');overlay.append(notice);document.body.append(overlay);
        }""")
        runner.solver = Mock(return_value=[0])
        runner.media_action = Mock(wraps=runner.media_action)
        return controller, runner

    def test_notice_waits_without_click_playback_or_ai_then_handles_question(self):
        controller, runner = self.notice_fixture()
        self.assertIsNone(runner.blockers())
        runner.tick()
        runner.tick()
        self.assertIn('等待平台自动切换',controller.snapshot()['message'])
        self.assertFalse(controller.pause.is_set())
        self.assertFalse(runner.blocked)
        runner.solver.assert_not_called()
        runner.media_action.assert_not_called()
        self.assertIsNone(self.page.locator('body').get_attribute('data-notice-clicks'))
        self.page.evaluate("""() => setTimeout(()=>{
            document.querySelector('.ai-notice-dialog').remove();
            document.querySelector('.ai-class-exercise-dialog').style.display='';
        }, 50)""")
        self.page.locator('.ai-notice-dialog').wait_for(state='detached')
        runner.tick()
        self.assertIsNone(runner.notice_started)
        self.assertEqual(runner.solver.call_count,1)
        self.assertEqual(self.page.locator('body').get_attribute('data-submits'),'1')
        runner.tick()
        runner.tick()
        self.assert_playback_advances(runner)
        self.assertEqual(controller.snapshot()['state'],'running')

    def test_notice_timeout_keeps_observing_without_click_or_latching_pause(self):
        controller, runner = self.notice_fixture()
        with patch('automation.time.monotonic',return_value=100):
            runner.tick()
        with patch('automation.time.monotonic',return_value=125):
            runner.tick()
            runner.tick()
        self.assertEqual(controller.snapshot()['state'],'waiting_user')
        self.assertIn('超过20秒',controller.snapshot()['message'])
        self.assertFalse(controller.pause.is_set())
        runner.solver.assert_not_called()
        runner.media_action.assert_not_called()
        self.assertIsNone(self.page.locator('body').get_attribute('data-notice-clicks'))
        self.page.locator('.ai-notice-dialog').evaluate('e => e.remove()')
        self.page.locator('.ai-class-exercise-dialog').evaluate("e => e.style.display=''")
        runner.tick()
        self.assertEqual(runner.solver.call_count,1)

    def test_gap_between_notice_and_question_does_not_resume_video_early(self):
        controller, runner = self.notice_fixture()
        runner.tick()
        self.page.locator('.ai-notice-dialog').evaluate('e => e.remove()')
        runner.tick()
        self.assertIn('等待随堂练习题目加载',controller.snapshot()['message'])
        runner.media_action.assert_not_called()
        runner.solver.assert_not_called()
        self.page.locator('.ai-class-exercise-dialog').evaluate("e => e.style.display=''")
        runner.tick()
        self.assertEqual(runner.solver.call_count,1)
        self.assertFalse(runner.notice_waiting_question)

    def test_real_verification_alongside_notice_still_blocks(self):
        controller, runner = self.notice_fixture()
        self.page.evaluate("""() => {
            const verification=document.createElement('div');verification.className='yidun_popup';
            verification.innerHTML='<div class="yidun_control" style="width:200px;height:50px">拖动验证</div>';
            document.body.append(verification);
        }""")
        runner.tick()
        self.assertIn('验证码',controller.snapshot()['message'])
        runner.solver.assert_not_called()
        self.assertIsNone(self.page.locator('body').get_attribute('data-notice-clicks'))

    def directory_fixture(self, active='2.5.3', collapsed_next=False):
        controller, runner = self.practice_fixture()
        self.page.locator('.ai-class-exercise-dialog').evaluate('e => e.remove()')
        self.page.evaluate("""args=>{
            const catalog=document.createElement('div');catalog.className='catalog-com';
            catalog.innerHTML=`<div class="chapter-wrapper">
            <div class="el-collapse-item"><button class="el-collapse-item__header" aria-expanded="true">第二章</button><div class="el-collapse-item__wrap">
            <div class="chapter-item"><div class="item-box"><div class="item-num">2.5</div><div class="item-name">带分支标题</div></div>
              <div class="chapter-content-second"><div class="item-num">2.5.2</div><div class="item-name">分支二</div></div>
              <div class="chapter-content-second"><div class="item-num">2.5.3</div><div class="item-name">最后分支</div></div></div>
            <div class="chapter-item"><div class="item-box"><div class="item-num">2.6</div><div class="item-name">没有分支的视频</div></div></div>
            <div class="chapter-item"><div class="item-box"><div class="item-num">2.7</div><div class="item-name">另一个分组</div></div>
              <div class="chapter-content-second"><div class="item-num">2.7.1</div><div class="item-name">第一分支</div></div></div>
            <div class="test-box">章节测试</div></div></div>
            <div class="el-collapse-item"><button class="el-collapse-item__header" aria-expanded="${!args.collapsed}">第三章</button><div class="el-collapse-item__wrap" style="display:${args.collapsed?'none':'block'}">
            <div class="chapter-item"><div class="item-box"><div class="item-num">3.1</div><div class="item-name">下一章视频</div></div></div></div></div></div>`;
            catalog.querySelectorAll('.chapter-item,.chapter-content-second').forEach(row=>{
                const box=row.querySelector(':scope > .item-box') || row;
                const number=box.querySelector(':scope > .item-num').textContent;
                if(number===args.active)row.classList.add('current');
                const target=row.matches('.chapter-item')?box:row;
                target.addEventListener('click',event=>{
                    event.stopPropagation();document.body.dataset.chosen=number;
                    document.body.dataset.lessonClicks=Number(document.body.dataset.lessonClicks||0)+1;
                });
            });
            catalog.querySelectorAll('.el-collapse-item__header').forEach(header=>{
                header.onclick=()=>{header.setAttribute('aria-expanded','true');header.nextElementSibling.style.display='block'};
            });document.body.append(catalog);
        }""", {'active':active,'collapsed':collapsed_next})
        runner.last_src='ended-fixture'
        return controller, runner

    def advance_directory(self, runner):
        with patch('automation.time.monotonic',return_value=100):
            runner.next_lesson(None)
        self.assertIsNone(self.page.locator('body').get_attribute('data-chosen'))
        with patch('automation.time.monotonic',return_value=105):
            runner.next_lesson(None)

    def test_last_branch_advances_to_heading_without_children(self):
        _, runner = self.directory_fixture()
        self.advance_directory(runner)
        self.assertEqual(self.page.locator('body').get_attribute('data-chosen'),'2.6')
        with patch('automation.time.monotonic',return_value=106):
            runner.next_lesson(None)
        self.assertEqual(self.page.locator('body').get_attribute('data-lesson-clicks'),'1')

    def test_plain_lesson_advances_to_first_child_not_group_heading(self):
        _, runner = self.directory_fixture(active='2.6')
        self.advance_directory(runner)
        self.assertEqual(self.page.locator('body').get_attribute('data-chosen'),'2.7.1')

    def test_last_child_expands_next_chapter_and_plays_plain_heading(self):
        _, runner = self.directory_fixture(active='2.7.1',collapsed_next=True)
        self.advance_directory(runner)
        self.assertEqual(self.page.locator('body').get_attribute('data-chosen'),'3.1')
        self.assertEqual(self.page.locator('.el-collapse-item__header').nth(1).get_attribute('aria-expanded'),'true')

    def test_directory_end_does_not_click_chapter_exam_or_loop(self):
        controller, runner = self.directory_fixture(active='3.1')
        self.advance_directory(runner)
        self.assertIsNone(self.page.locator('body').get_attribute('data-chosen'))
        self.assertIn('最后一个视频课时',controller.snapshot()['message'])

    def test_platform_directory_advance_prevents_second_click(self):
        _, runner = self.directory_fixture()
        with patch('automation.time.monotonic',return_value=100):runner.next_lesson(None)
        self.page.locator('.current').evaluate("e=>e.classList.remove('current')")
        self.page.locator('.chapter-item').nth(1).evaluate("e=>e.classList.add('current')")
        with patch('automation.time.monotonic',return_value=105):runner.next_lesson(None)
        self.assertIsNone(self.page.locator('body').get_attribute('data-chosen'))

    def test_ambiguous_current_lesson_is_not_guessed(self):
        controller, runner = self.directory_fixture()
        self.page.locator('.chapter-item').nth(1).evaluate("e=>e.classList.add('current')")
        self.advance_directory(runner)
        self.assertIsNone(self.page.locator('body').get_attribute('data-chosen'))
        self.assertIn('唯一确认',controller.snapshot()['message'])

    def test_outer_role_dialog_is_not_classified_as_unknown_before_question(self):
        controller, runner = self.practice_fixture()
        self.assertEqual(self.page.locator('.el-overlay-dialog[role=dialog] > .el-dialog.ai-class-exercise-dialog').count(),1)
        self.assertIsNone(runner.blockers())
        runner.solver=Mock(return_value=[0])
        runner.tick()
        self.assertEqual(self.page.locator('body').get_attribute('data-submits'),'1')
        self.assertNotIn('未知',controller.snapshot()['message'])
        self.assertNotIn('尚未适配',controller.snapshot()['message'])

    def test_empty_outer_dialog_after_close_does_not_block_resume(self):
        controller, runner = self.practice_fixture()
        runner.solver=Mock(return_value=[0])
        runner.tick()
        runner.tick()
        self.assertEqual(self.page.locator('.el-dialog').count(),0)
        self.assertEqual(self.page.locator('.el-overlay-dialog[role=dialog]').count(),1)
        self.assertIsNone(runner.blockers())
        runner.tick()
        self.assert_playback_advances(runner)
        self.assertEqual(controller.snapshot()['state'],'running')

    def test_unknown_inner_dialog_is_not_ignored_with_its_outer_wrapper(self):
        controller, runner = self.practice_fixture()
        self.page.locator('.tit').evaluate("e=>e.textContent='需要人工处理的新提示'")
        runner.solver=Mock(return_value=[0])
        runner.tick()
        self.assertEqual(controller.snapshot()['state'],'waiting_user')
        self.assertIn('尚未适配',controller.snapshot()['message'])
        runner.solver.assert_not_called()

    def test_inner_verification_still_blocks_under_element_plus_wrapper(self):
        controller, runner = self.practice_fixture()
        self.page.locator('.ai-class-exercise-dialog').evaluate("e=>{e.className='el-dialog';e.innerHTML='请完成人脸验证'}")
        runner.tick()
        self.assertIn('验证',controller.snapshot()['message'])
        self.assertEqual(controller.snapshot()['state'],'waiting_user')

    def test_no_result_after_submit_still_closes_and_resumes_actual_video(self):
        controller, runner = self.practice_fixture()
        self.page.locator('.dialog-footer .btn').evaluate("e=>e.onclick=()=>document.body.dataset.submits=Number(document.body.dataset.submits||0)+1")
        runner.solver=Mock(return_value=[0])
        runner.tick()
        self.assertEqual(self.page.locator('.dialog-footer .btn').inner_text(),'提交作答')
        runner.tick()
        self.assertEqual(self.page.locator('.ai-class-exercise-dialog').count(),0)
        self.assert_playback_advances(runner)
        self.assertEqual(controller.snapshot()['state'],'running')
        self.assertEqual(runner.solver.call_count,1)
        self.assertEqual(self.page.locator('body').get_attribute('data-submits'),'1')

    def test_no_result_and_lingering_panel_does_not_block_or_resubmit(self):
        controller, runner = self.practice_fixture()
        self.page.locator('.dialog-footer .btn').evaluate("e=>e.onclick=()=>document.body.dataset.submits=Number(document.body.dataset.submits||0)+1")
        self.page.locator('.header-icon').evaluate('e=>e.onclick=()=>{}')
        runner.solver=Mock(return_value=[0])
        runner.tick()
        runner.tick()
        self.assertEqual(self.page.locator('.ai-class-exercise-dialog').count(),1)
        self.assert_playback_advances(runner)
        runner.tick()
        self.assertEqual(controller.snapshot()['state'],'running')
        self.assertEqual(runner.solver.call_count,1)
        self.assertEqual(self.page.locator('body').get_attribute('data-submits'),'1')

    def test_chapter_tests_are_never_added_to_video_navigation(self):
        _, runner = self.directory_fixture()
        self.page.locator('.test-box').evaluate("e=>{e.innerHTML='<div class=chapter-item><div class=item-box><div class=item-num>2.5.4</div><div class=item-name>章节测试</div></div></div>';document.querySelector('.chapter-item').after(e)}")
        self.advance_directory(runner)
        self.assertEqual(self.page.locator('body').get_attribute('data-chosen'),'2.6')

    def test_chapter_test_dialog_never_calls_ai_or_submits(self):
        controller, runner = self.practice_fixture()
        self.page.locator('.tit').evaluate("e=>e.textContent='第二章 章节测试'")
        runner.solver=Mock(return_value=[0])
        runner.tick()
        runner.solver.assert_not_called()
        self.assertIsNone(self.page.locator('body').get_attribute('data-submits'))
        self.assertEqual(controller.snapshot()['state'],'waiting_user')


if __name__ == '__main__':
    unittest.main()
