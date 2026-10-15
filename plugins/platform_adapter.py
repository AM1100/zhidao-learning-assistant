"""Read-only adapter for the official student/course JSON endpoints.

Protocol references and scope are documented in README.md. No playback,
attendance, learning progress, enrollment, or answer submission endpoints.
"""
import base64
import json
import time
import urllib.parse
import importlib.util
from pathlib import Path

from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PUBLIC_KEY = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCgfZmpLpPEpEFRKBe+ZjWJUjPe+7qg7pGqcfN3j2egJ8H2mrKwaEqZEnPnpi2O3hN8HRyaFozDOp8gwZiYfiIZjWy0Jr/FNAiiKYh5bq0GsEn+ieMmRyJg/+i1rqizhvCXvFdrdGhFTw5EBwTpsGdwe1utdlrvIJUAFWj9Yh4qbQIDAQAB"
IV = b"1g3qqdh4jvbskb9x"
HOME = "https://onlineservice-api.zhihuishu.com"
STUDY = "https://studyservice-api.zhihuishu.com"
HIKE = "https://hike-teaching-api.zhihuishu.com"
PUBLIC_SERVICE = "https://appcomm-user.zhihuishu.com/app-commserv-user/c/has"
MAX_PAGES = 100
PAGE_SIZE = 20


class AdapterError(ValueError):
    """User-safe diagnostic: never include cookies, keys or raw responses."""


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def recover_key(signed):
    """Match official JS's RSA public operation; only a public key is used."""
    public = serialization.load_der_public_key(base64.b64decode(PUBLIC_KEY))
    numbers = public.public_numbers()
    raw = base64.b64decode(signed, validate=True)
    if len(raw) != public.key_size // 8:
        raise AdapterError("平台参数协商响应长度异常，请稍后重试。")
    recovered = pow(int.from_bytes(raw, "big"), numbers.e, numbers.n).to_bytes(len(raw), "big")
    if not recovered.startswith(b"\x00\x01"):
        raise AdapterError("平台参数协商格式已变化，请更新适配器。")
    separator = recovered.find(b"\x00", 2)
    if separator < 10 or any(x != 255 for x in recovered[2:separator]):
        raise AdapterError("平台参数协商校验失败。")
    data = json.loads(recovered[separator + 1:])
    key = data["cKey"].encode()
    if len(key) not in (16, 24, 32):
        raise AdapterError("平台参数封装密钥格式已变化。")
    return key


def load_key(request, module):
    public = serialization.load_der_public_key(base64.b64decode(PUBLIC_KEY))
    uid = base64.b64encode(public.encrypt(compact({"module": module}).encode(), PKCS1v15())).decode()
    # This is the same public parameter negotiation as the official frontend.
    data = request(PUBLIC_SERVICE + "?" + urllib.parse.urlencode({"uid": uid}))
    try:
        return recover_key(data["rt"]["sl"])
    except AdapterError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise AdapterError("平台参数协商未返回有效结果，请稍后重试。") from None


def encode_payload(data, key):
    padder = padding.PKCS7(128).padder()
    plain = compact(data).encode()
    padded = padder.update(plain) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(IV)).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


def check_response(data, label):
    if not isinstance(data, dict):
        raise AdapterError(label + "返回格式异常。")
    codes = [data[k] for k in ("code", "status") if k in data]
    if not codes or any(str(c) not in ("0", "200") for c in codes):
        message = str(data.get("message", data.get("msg", "")))
        if any(str(c) in ("401", "403", "-1", "1001", "100001", "40001") for c in codes) or any(k in message for k in ("登录", "登陆", "过期", "认证")):
            raise AdapterError(label + "未通过登录校验。请从该接口的 Request Headers 重新复制 Cookie，并在有 Authorization 时一起填写。")
        if any(str(c) in ("419", "422", "429") for c in codes):
            raise AdapterError(label + "需要在原平台完成验证，或请求过于频繁；请返回原平台处理后重试。")
        safe_codes = ",".join(str(c)[:20] for c in codes if isinstance(c, (int, str)) and str(c).lstrip('-').isdigit())
        raise AdapterError(label + "请求未成功" + (f"（状态码 {safe_codes}）" if safe_codes else "") + "。请在原平台确认该课程可正常访问。")
    return data


class Client:
    def __init__(self, request):
        self.request = request
        self.keys = {}

    def call(self, url, payload, label, module=13, method="POST"):
        key = self.keys.get(module)
        if key is None:
            key = self.keys[module] = load_key(self.request, module)
        now = int(time.time()) * 1000
        date_field = "dateFormate" if module == 11 else "date"
        business = dict(payload)
        if module == 11:
            business["dateFormate"] = now
        data = {"secretStr": encode_payload(business, key), date_field: now}
        if method == "GET":
            result = self.request(url + "?" + urllib.parse.urlencode(data))
        else:
            result = self.request(url, data, form=True)
        return check_response(result, label)


def course_record(raw, kind):
    cid = raw.get("courseId", raw.get("id"))
    rid = raw.get("recruitId", "")
    if cid is None:
        raise AdapterError("课程响应缺少 courseId，无法安全建立章节关联。")
    name = raw.get("courseName", raw.get("name", "未命名课程"))
    # The course list's `secret` is already URL-escaped; the video API expects
    # its decoded value. Encoding it twice makes the backend reject the ID.
    secret = urllib.parse.unquote(str(raw.get("secret", raw.get("recruitAndCourseId", ""))))
    url = ""
    mode = "standard"
    if raw.get("mapCourse") == 7 and int(raw.get("termId") or 0) >= 24:
        mode = "wisdom"
    if kind == "share" and secret:
        host = "wisdom-mooc.zhihuishu.com/study/index" if mode == "wisdom" else "studyvideoh5.zhihuishu.com/stuStudy"
        url = "https://" + host + "?" + urllib.parse.urlencode({"recruitAndCourseId": secret})
    elif kind == "aided":
        # The official home still lists these courses; do not guess their video API.
        url = "https://onlineweb.zhihuishu.com/onlines"
    return {"id": f"{kind}:{cid}:{rid}", "name": str(name),
            "teacher": str(raw.get("teacherName", raw.get("teacherNames", ""))),
            "url": url, "chapters": [], "platform": {"kind": kind, "course_id": str(cid),
            "recruit_id": str(rid), "recruit_and_course_id": secret, "mode": mode}}


def read_shared(client):
    found, visited = [], set()
    for status in (0, 1):  # Official frontend uses 0 for current and 1 for completed.
        for page in range(1, MAX_PAGES + 1):
            result = client.call(HOME + "/gateway/t/v1/student/course/share/queryShareCourseInfo",
                                 {"status": status, "pageNo": page, "pageSize": PAGE_SIZE}, "共享课程")
            body = result.get("result")
            if body is None:
                raise AdapterError("共享课程返回空 result，无法确认登录或课程状态，请在原平台重新登录后再试。")
            if not isinstance(body, dict) or "courseOpenDtos" not in body:
                raise AdapterError("共享课程列表结构已变化，不能将未知响应视为零门课程。")
            rows = body["courseOpenDtos"]
            if rows is None:
                rows = []
            if not isinstance(rows, list):
                raise AdapterError("共享课程列表格式已变化。")
            added = 0
            for row in rows:
                course = course_record(row, "share")
                if course["id"] not in visited:
                    found.append(course); visited.add(course["id"]); added += 1
            if len(rows) < PAGE_SIZE:
                break
            if not added:
                raise AdapterError("平台分页返回重复数据，已停止读取，未将不完整结果作为完整列表。")
        else:
            raise AdapterError("课程分页超出读取上限，请缩小范围后重试。")
    return found


def read_aided(client):
    found, visited = [], set()
    for page in range(1, MAX_PAGES + 1):
        result = client.call(HIKE + "/gateway/t/v1/student/online/course/getCourseList",
                             {"pageNum": page, "pageSize": PAGE_SIZE}, "校内课程", method="GET")
        body = result.get("rt")
        if not isinstance(body, dict) or not isinstance(body.get("pageList"), list):
            raise AdapterError("校内课程列表结构已变化。")
        rows = body["pageList"]
        added = 0
        for row in rows:
            course = course_record(row, "aided")
            if course["id"] not in visited:
                found.append(course); visited.add(course["id"]); added += 1
        total = body.get("totalPages")
        if (total is not None and page >= int(total)) or len(rows) < PAGE_SIZE:
            return found
        if not added:
            raise AdapterError("校内课程分页返回重复数据，已停止读取。")
    raise AdapterError("校内课程分页超出读取上限。")


def list_courses(request):
    client = Client(request)
    courses, warnings = [], []
    success_count = 0
    for reader in (read_shared, read_aided):
        try:
            courses.extend(reader(client))
            success_count += 1
        except AdapterError as error:
            warnings.append(str(error))
    if not success_count:
        raise AdapterError("；".join(warnings))
    return {"courses": courses, "warnings": warnings,
            "scope": "共享课程（进行中和已结束）及校内课程；其他课程类型暂未接入。"}


def chapter_nodes(items, depth=0):
    if not isinstance(items, list) or depth > 12:
        raise AdapterError("章节目录格式异常。")
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise AdapterError("章节条目格式异常。")
        name = item.get("name", item.get("chapterName", item.get("lessonName", item.get("videoName"))))
        if not name:
            raise AdapterError("章节缺少名称，平台目录格式可能已变化。")
        node = {"name": str(name)}
        for field in ("videoLessons", "videoSmallLessons", "children"):
            if item.get(field):
                node["children"] = chapter_nodes(item[field], depth + 1)
                break
        result.append(node)
    return result


def list_chapters(request, course):
    # Compatibility with the first local host, which passed only a course ID.
    if isinstance(course, str):
        matches = [item for item in read_shared(Client(request)) if item["id"] == course]
        if len(matches) != 1:
            raise AdapterError("课程会话已变化，请重新读取课程列表。")
        course = matches[0]
    meta = course.get("platform", {})
    if meta.get("kind") != "share":
        raise AdapterError("这门课属于校内翻转课，当前视频目录接口不适用。请点“在原平台打开课程”查看；不会错误显示为空目录。")
    secret = meta.get("recruit_and_course_id")
    if not secret:
        raise AdapterError("课程列表未返回视频页标识，暂时无法读取该课目录。")
    spec = importlib.util.spec_from_file_location("study_session", Path(__file__).with_name("study_session.py"))
    sessions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sessions)
    session = sessions.snapshot(request)
    if session is not None:
        if not session["cookie"]:
            raise AdapterError("请先填写本机平台 Cookie。")
        transport = sessions.StudySession(session["cookie"])
        try:
            transport.login()
        except sessions.SessionError as error:
            raise AdapterError(str(error)) from None
    else:
        transport = request
    client = Client(transport)
    try:
        return read_chapters(client, secret)
    except sessions.SessionError as error:
        raise AdapterError(str(error)) from None


def read_chapters(client, secret):
    client.call(STUDY + "/gateway/t/v1/learning/queryCourse",
                {"recruitAndCourseId": secret}, "课程信息", module=11)
    result = client.call(STUDY + "/gateway/t/v1/learning/videolist",
                                  {"recruitAndCourseId": secret}, "视频目录", module=11)
    body = result.get("data")
    if not isinstance(body, dict) or "videoChapterDtos" not in body:
        raise AdapterError("视频目录响应结构已变化，或当前课程使用其他学习页面。")
    return chapter_nodes(body["videoChapterDtos"])
