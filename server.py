#!/usr/bin/env python3
"""Local-only course reader and playback automation. Python 3.10+, see requirements.txt."""
import argparse
import importlib.util
import json
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
STATE = {"cookie": "", "authorization": "", "ai": {}}
COURSES = {}
ADAPTER_CACHE = {"mtime": None, "module": None}
AUTOMATION = None
MAX_BODY = 2_000_000


class AppError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AppError("远端发生重定向，请检查登录状态或接口地址。凭据未转发。")


def request_json(url, payload=None, headers=None, form=False):
    body = None
    headers = dict(headers or {})
    if payload is not None:
        body = (urllib.parse.urlencode(payload) if form else json.dumps(payload)).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=40) as response:
            raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise AppError("响应超过 2 MB，请缩小请求范围。")
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AppError("平台或 API 登录校验失败（HTTP " + str(e.code) + "）。请检查本机填写的 Cookie / Token 或 API Key。") from None
        if e.code in (422, 429):
            raise AppError("平台需要验证或限制了请求频率，请在原平台处理后稍后重试。") from None
        raise AppError(f"远端 HTTP {e.code}，请检查凭据、权限或 API 配置。") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AppError("远端网络连接失败或超时。") from None
    except (ValueError, UnicodeError):
        raise AppError("远端未返回 JSON，可能已跳转登录或接口发生变化。") from None


def platform_request(url, payload=None, form=False):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or not (host == "zhihuishu.com" or host.endswith(".zhihuishu.com"))):
        raise AppError("平台接口仅允许 HTTPS 智慧树域名。")
    with LOCK:
        cookie, authorization = STATE["cookie"], STATE["authorization"]
    if not cookie:
        raise AppError("请先在会话设置中提交本机 Cookie。")
    headers = {"Cookie": cookie, "Referer": "https://onlineweb.zhihuishu.com/", "User-Agent": "Mozilla/5.0"}
    if authorization:
        headers["Authorization"] = authorization
    return request_json(url, payload, headers, form)


def session_snapshot():
    with LOCK:
        return {key: STATE[key] for key in ("cookie", "authorization")}


# Used inside the local adapter only; never served as an HTTP endpoint.
platform_request.session_snapshot = session_snapshot


def get_adapter():
    path = ROOT / "plugins" / "platform_adapter.py"
    if not path.exists():
        raise AppError("平台适配器文件缺失，请重新解压完整程序。")
    with LOCK:
        mtime = path.stat().st_mtime_ns
        if ADAPTER_CACHE["mtime"] != mtime:
            spec = importlib.util.spec_from_file_location("platform_adapter", path)
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except ModuleNotFoundError:
                raise AppError("平台适配器缺少依赖，请在程序目录执行 python3 -m pip install -r requirements.txt。") from None
            ADAPTER_CACHE.update(mtime=mtime, module=module)
        return ADAPTER_CACHE["module"]


def adapter_call(method, *args):
    adapter = get_adapter()
    try:
        return getattr(adapter, method)(platform_request, *args)
    except getattr(adapter, "AdapterError", AppError) as error:
        raise AppError(str(error)) from None


def automation_controller():
    global AUTOMATION
    if AUTOMATION is None:
        from automation import AutomationController
        with LOCK:
            if AUTOMATION is None:
                AUTOMATION = AutomationController(request_json)
    return AUTOMATION


def normalize_courses(data):
    if isinstance(data, dict):
        data = data.get("courses", data.get("result", data.get("data", data)))
    if isinstance(data, dict):
        data = data.get("courseOpenDtos", data.get("courseList", data))
    if not isinstance(data, list):
        raise AppError("未识别课程列表，需要 courses 数组或 result.courseOpenDtos。")
    courses = []
    seen = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise AppError("课程必须是 JSON 对象。")
        cid = str(item.get("id", item.get("courseId", index + 1)))
        if cid in seen:
            raise AppError("课程 ID 重复，请检查导入文件。")
        seen.add(cid)
        name = str(item.get("name", item.get("courseName", "未命名课程")))
        chapters = item.get("chapters", [])
        validate_nodes(chapters)
        courses.append({"id": cid, "name": name, "teacher": str(item.get("teacher", item.get("teacherName", ""))),
                        "url": safe_link(item.get("url", "")), "chapters": chapters})
    return courses


def safe_link(value):
    if not isinstance(value, str):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    p = urllib.parse.urlsplit(value)
    host = p.hostname or ""
    return value if p.scheme == "https" and not p.username and not p.password and (host == "zhihuishu.com" or host.endswith(".zhihuishu.com")) else ""


def validate_nodes(nodes, depth=0):
    if not isinstance(nodes, list) or depth > 12 or len(nodes) > 2000:
        raise AppError("章节需为数组，最多支持 12 层，每层最多 2000 项。")
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("name"), str):
            raise AppError("每个章节需包含 name 字符串，可附 children 数组。")
        if "url" in node:
            node["url"] = safe_link(node["url"])
        validate_nodes(node.get("children", []), depth + 1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log bodies or credentials.

    def trusted_host(self):
        return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

    def send(self, status, data, content_type="application/json; charset=utf-8"):
        body = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, (dict, list)) else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.trusted_host():
            return self.send(403, {"error": "仅允许本机访问。"})
        files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
        if self.path not in files:
            return self.send(404, {"error": "不存在的页面。"})
        filename, mime = files[self.path]
        body = (ROOT / "static" / filename).read_bytes().replace(b"__LOCAL_TOKEN__", TOKEN.encode())
        self.send(200, body, mime)

    def do_POST(self):
        origin = self.headers.get("Origin")
        allowed = (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}")
        if not self.trusted_host() or self.headers.get("X-Local-Token") != TOKEN or (origin and origin not in allowed):
            return self.send(403, {"error": "本机会话校验失败，请刷新页面。"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= MAX_BODY:
                raise AppError("请求过大。")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise AppError("请求必须是对象。")
            result = self.dispatch(data)
            self.send(200, result)
        except (AppError, ValueError, TypeError, KeyError, RecursionError) as e:
            message = str(e) if isinstance(e, AppError) else "输入格式不正确，请检查配置。"
            self.send(400, {"error": message})
        except Exception:
            self.send(500, {"error": "处理失败，请检查本地适配器。为保护凭据，不返回内部异常。"})

    def dispatch(self, data):
        if self.path == "/api/automation-status":
            return automation_controller().snapshot()
        if self.path == "/api/automation-start":
            with LOCK:
                course = COURSES.get(str(data.get("course_id", "")))
                cookie = STATE["cookie"]
                ai = dict(STATE["ai"])
            if course is None:
                raise AppError("请先读取平台课程，再选择要自动播放的课程。")
            auto_answer = data.get("auto_answer", False)
            if type(auto_answer) is not bool:
                raise AppError("AI 弹题选项格式无效。")
            start_unlearned = data.get("start_unlearned", True)
            if type(start_unlearned) is not bool:
                raise AppError("起始章节选项格式无效。")
            try:
                return automation_controller().start(course, cookie, ai, auto_answer, start_unlearned)
            except ValueError as error:
                raise AppError(str(error)) from None
        if self.path == "/api/automation-command":
            try:
                return automation_controller().command(data.get("action"))
            except ValueError as error:
                raise AppError(str(error)) from None
        if self.path == "/api/session":
            cookie = data.get("cookie", "").strip()
            authorization = data.get("authorization", "").strip()
            if any(c in cookie + authorization for c in "\r\n") or len(cookie) > 32000:
                raise AppError("Cookie 或 Token 格式不正确。")
            if AUTOMATION is not None and AUTOMATION.thread and AUTOMATION.thread.is_alive():
                raise AppError("请先停止自动学习，再更换平台会话。")
            with LOCK:
                STATE.update(cookie=cookie, authorization=authorization)
                COURSES.clear()
            return {"message": "会话已暂存在本机内存。现在可点“读取平台课程”验证并读取。"}
        if self.path == "/api/clear":
            if AUTOMATION is not None:
                AUTOMATION.command("stop")
            with LOCK:
                STATE.update(cookie="", authorization="", ai={})
                COURSES.clear()
            return {"message": "Cookie、Token 和 API Key 已从服务内存清除。"}
        if self.path == "/api/import":
            return {"courses": normalize_courses(data["data"])}
        if self.path == "/api/courses":
            with LOCK:
                session = (STATE["cookie"], STATE["authorization"])
            result = adapter_call("list_courses")
            raw = result.get("courses", []) if isinstance(result, dict) else result
            courses = normalize_courses(raw)
            with LOCK:
                if session != (STATE["cookie"], STATE["authorization"]):
                    raise AppError("读取过程中会话发生变化，请重新读取。")
                COURSES.clear()
                COURSES.update({str(course["id"]): course for course in raw})
            return {"courses": courses, "warnings": result.get("warnings", []) if isinstance(result, dict) else [],
                    "scope": result.get("scope", "") if isinstance(result, dict) else ""}
        if self.path == "/api/chapters":
            with LOCK:
                course = COURSES.get(str(data["course_id"]))
            if course is None:
                raise AppError("请先重新读取平台课程列表，再同步章节。")
            nodes = adapter_call("list_chapters", course)
            validate_nodes(nodes)
            return {"chapters": nodes}
        if self.path == "/api/ai-config":
            url = data["url"].strip()
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise AppError("AI 接口需要完整 HTTPS chat/completions 地址。")
            key, model = data["key"].strip(), data["model"].strip()
            if not key or not model or any(c in key for c in "\r\n"):
                raise AppError("请填写有效的 API Key 和模型名。")
            with LOCK:
                STATE["ai"] = {"url": url, "key": key, "model": model}
            return {"message": "AI 配置已暂存在本机内存；开启自动弹题时发送可见题目与选项。"}
        raise AppError("未知接口。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"知到学习助手：http://127.0.0.1:{server.server_port}（Ctrl+C 退出并清除内存凭据）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if AUTOMATION is not None:
            AUTOMATION.command("stop")
            if AUTOMATION.thread is not None:
                AUTOMATION.thread.join(timeout=45)
        server.server_close()
