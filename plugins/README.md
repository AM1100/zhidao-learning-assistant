# 已内置的只读平台适配器

当前程序已包含 `platform_adapter.py` 和 `study_session.py`，不需要用户自己寻找或编写适配器。

## 来源与协议

2026-10-15 检查的官方静态资源：

- 入口：<https://onlineweb.zhihuishu.com/onlines>
- 学生端接口定义：<https://onlinestuh5.zhihuishu.com/vue-runtime.60e00844.js>
- 学生端组件：<https://onlinestuh5.zhihuishu.com/app.60e00844.js>
- 首页参数封装：<https://base2.zhihuishu.com/js/2.js>
- 视频参数封装：<https://base2.zhihuishu.com/js/4.js>
- 视频接口定义：<https://studyvideoh5.zhihuishu.com/app.53640cec.1783993776046.js>
- 视频组件：<https://studyvideoh5.zhihuishu.com/stuStudy.53640cec.1783993776046.js>
- 当前智慧慕课页面：<https://wisdom-mooc.zhihuishu.com/study/index>

客户端按公开前端流程向 `appcomm-user.zhihuishu.com/app-commserv-user/c/has` 协商业务参数封装数据，使用公开 RSA 参数和 AES CBC/PKCS7 生成 secretStr。没有平台私钥，没有伪造用户身份或修改 Cookie 中的身份字段。

已接入的业务读接口：

| 类型 | 接口 | 模块 |
| --- | --- | --- |
| 共享课程 | POST onlineservice-api.zhihuishu.com/gateway/t/v1/student/course/share/queryShareCourseInfo | 13 |
| 校内课程列表 | GET hike-teaching-api.zhihuishu.com/gateway/t/v1/student/online/course/getCourseList | 13 |
| 共享课程信息 | POST studyservice-api.zhihuishu.com/gateway/t/v1/learning/queryCourse | 11 |
| 视频目录 | POST studyservice-api.zhihuishu.com/gateway/t/v1/learning/videolist | 11 |

共享课程按 status 0/1 分别分页，课程 ID 同时绑定课程类型、courseId、recruitId，避免同课不同学期串用目录。服务端保存目录所需标识，常规响应不返回额外 platform 字段。

## 会话隔离修复

把首页 Cookie 中的 SESSION、SERVERID、acw_tc 原样带到视频服务会造成会话冲突。视频目录读取只复用原样的通用 SSO Cookie，访问官方 `/login/gologin`，让服务正常签发独立会话。CookieJar 只在内存中存在；跳转限定 HTTPS 智慧树域名。若需要人工登录、验证码或其他验证，停止并提示用户。

兼容正在运行的 v0.1 服务时，适配器在同一 Python 进程内读取旧回调所使用的会话；不导出凭据、不写中转文件。新服务使用 `platform_request.session_snapshot()` 明确提供本机会话。

## 扩展约定

`list_courses(request)` 返回 `{courses: [...], warnings: [...], scope: '...'}`；标准课程含 id/name/teacher/url/chapters，内部 platform 元数据仅供后端关联。

`list_chapters(request, course)` 接收服务端缓存的课程对象，返回 `[{name:'第一章', children:[{name:'第一节'}]}]`。为兼容旧服务，也接受课程 ID，并重新读取列表获取匹配关系。

适配器只实现课程与目录读取。不要接入学习时长、进度上报、出勤、选退课或答案提交接口。插件为本地 Python 代码，仅安装自己信任的实现。平台版本变化仍可能需要更新适配器。
