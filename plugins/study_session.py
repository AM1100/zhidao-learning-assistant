"""Create a separate, memory-only SSO session for the video service."""
import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request


class SessionError(ValueError):
    pass


def trusted(url):
    p = urllib.parse.urlsplit(url)
    host = p.hostname or ""
    return (p.scheme == "https" and not p.username and not p.password
            and p.port in (None, 443)
            and (host == "zhihuishu.com" or host.endswith(".zhihuishu.com")))


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 10
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not trusted(newurl):
            raise SessionError("视频服务登录重定向超出智慧树域名，已停止。")
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result:
            for key in ("Cookie", "Authorization"):
                result.remove_header(key)
        return result


def snapshot(callback):
    if hasattr(callback, "session_snapshot"):
        return callback.session_snapshot()
    # Compatibility with already running v0.1 hosts: access their in-process
    # session directly. Nothing is written to disk or returned to the browser.
    namespace = getattr(callback, "__globals__", {})
    if getattr(callback, "__name__", "") == "platform_request" and "LOCK" in namespace and "STATE" in namespace:
        with namespace["LOCK"]:
            return {key: namespace["STATE"][key] for key in ("cookie", "authorization")}
    return None


class StudySession:
    def __init__(self, cookie):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(SafeRedirect(), urllib.request.HTTPCookieProcessor(self.jar))
        # SESSION / SERVERID / acw_tc belong to the source service. Sending a
        # home-page SESSION to the video service caused upstream HTTP 500.
        # Only reuse the unmodified common SSO cookies, then let official SSO
        # issue the video service's own cookies.
        for pair in cookie.split(";"):
            name, sep, value = pair.strip().partition("=")
            if not sep or name not in ("CASLOGC", "CASTGC", "jt-cas", "locale"):
                continue
            self.jar.set_cookie(http.cookiejar.Cookie(
                version=0, name=name, value=value, port=None, port_specified=False,
                domain=".zhihuishu.com", domain_specified=True, domain_initial_dot=True,
                path="/", path_specified=True, secure=True, expires=None, discard=True,
                comment=None, comment_url=None, rest={}))

    def login(self):
        url = "https://studyservice-api.zhihuishu.com/login/gologin?" + urllib.parse.urlencode({
            "fromurl": "https://studyvideoh5.zhihuishu.com/stuStudy"})
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with self.opener.open(req, timeout=25) as response:
                # No account pages are exposed or logged; only obtain cookies.
                response.read(4096)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise SessionError("视频服务的单点登录未完成。请先在原平台打开视频课程，确认账号登录正常后重试。") from None

    def __call__(self, url, payload=None, form=False):
        if not trusted(url):
            raise SessionError("视频服务请求超出智慧树 HTTPS 域名，已停止。")
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://studyvideoh5.zhihuishu.com/",
                   "Origin": "https://studyvideoh5.zhihuishu.com"}
        body = None
        if payload is not None:
            body = (urllib.parse.urlencode(payload) if form else json.dumps(payload)).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        req = urllib.request.Request(url, body, headers)
        try:
            with self.opener.open(req, timeout=30) as response:
                if not trusted(response.url):
                    raise SessionError("视频服务重定向地址无效。")
                raw = response.read(2_000_001)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise SessionError("视频服务登录失效。请从视频页的接口重新复制 Cookie，至少包含有效 CASTGC 单点登录凭据。") from None
            raise SessionError(f"视频服务返回 HTTP {error.code}。请确认原平台可进入这门课程后重试。") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise SessionError("视频目录连接失败或超时，请稍后重试。") from None
        try:
            if len(raw) > 2_000_000:
                raise SessionError("视频目录响应过大。")
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise SessionError("视频服务尚未返回目录数据，请先在原平台完成登录或验证。") from None
