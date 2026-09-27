import unittest
from unittest.mock import patch

import httpx

import main
import pancheck
import pansou_auth
import proxy
from config import Config
from proxy import (
    get_query_param_pairs,
    make_api_request,
    make_pansou_api_request,
    parse_request_body,
)


class ConfigTests(unittest.TestCase):
    def test_auth_requires_fixed_jwt_secret(self):
        original_enabled = Config.AUTH_ENABLED
        original_users = Config.AUTH_USERS_RAW
        original_secret = Config.AUTH_JWT_SECRET

        try:
            Config.AUTH_ENABLED = True
            Config.AUTH_USERS_RAW = "admin:secret"
            Config.AUTH_JWT_SECRET = ""

            with self.assertRaises(ValueError):
                Config.validate()
        finally:
            Config.AUTH_ENABLED = original_enabled
            Config.AUTH_USERS_RAW = original_users
            Config.AUTH_JWT_SECRET = original_secret

    def test_pansou_auth_requires_token_or_credentials(self):
        original_enabled = Config.PANSOU_AUTH_ENABLED
        original_username = Config.PANSOU_AUTH_USERNAME
        original_password = Config.PANSOU_AUTH_PASSWORD
        original_token = Config.PANSOU_AUTH_TOKEN

        try:
            Config.PANSOU_AUTH_ENABLED = True
            Config.PANSOU_AUTH_USERNAME = ""
            Config.PANSOU_AUTH_PASSWORD = ""
            Config.PANSOU_AUTH_TOKEN = ""

            with self.assertRaises(ValueError):
                Config.validate()
        finally:
            Config.PANSOU_AUTH_ENABLED = original_enabled
            Config.PANSOU_AUTH_USERNAME = original_username
            Config.PANSOU_AUTH_PASSWORD = original_password
            Config.PANSOU_AUTH_TOKEN = original_token


class RequestParsingTests(unittest.TestCase):
    def test_form_post_body_is_parsed(self):
        with main.app.test_request_context(
            "/api/search",
            method="POST",
            data={"kw": "abc", "res": "merge"},
        ):
            self.assertEqual(parse_request_body()["kw"], "abc")

    def test_get_query_pairs_keep_duplicate_values(self):
        with main.app.test_request_context("/api/search?channels=a&channels=b&kw=abc"):
            self.assertEqual(
                get_query_param_pairs(),
                [("channels", "a"), ("channels", "b"), ("kw", "abc")],
            )


class ApiResponseParsingTests(unittest.TestCase):
    def test_invalid_utf8_response_is_decoded_lossily(self):
        class DummyClient:
            def get(self, url, params=None, headers=None):
                return httpx.Response(
                    200,
                    content=b'{"code":0,"message":"bad \xff bytes","data":{}}',
                    request=httpx.Request("GET", url),
                )

        data = make_api_request(DummyClient(), "http://example.test/api", method="GET")

        self.assertEqual(data["code"], 0)
        self.assertIn("bad", data["message"])


class PansouAuthTests(unittest.TestCase):
    def setUp(self):
        self.original_enabled = Config.PANSOU_AUTH_ENABLED
        self.original_username = Config.PANSOU_AUTH_USERNAME
        self.original_password = Config.PANSOU_AUTH_PASSWORD
        self.original_token = Config.PANSOU_AUTH_TOKEN
        self.original_login_url = Config.PANSOU_AUTH_LOGIN_URL
        self.original_search_url = Config.SEARCH_API_URL
        Config.PANSOU_AUTH_ENABLED = True
        Config.PANSOU_AUTH_USERNAME = "WebAdmin"
        Config.PANSOU_AUTH_PASSWORD = "PansouWeb"  # noqa: S105 - 测试夹具，非真实凭据
        Config.PANSOU_AUTH_TOKEN = ""
        Config.PANSOU_AUTH_LOGIN_URL = ""
        Config.SEARCH_API_URL = "http://pansou.test"
        pansou_auth.reset_cached_pansou_token()

    def tearDown(self):
        Config.PANSOU_AUTH_ENABLED = self.original_enabled
        Config.PANSOU_AUTH_USERNAME = self.original_username
        Config.PANSOU_AUTH_PASSWORD = self.original_password
        Config.PANSOU_AUTH_TOKEN = self.original_token
        Config.PANSOU_AUTH_LOGIN_URL = self.original_login_url
        Config.SEARCH_API_URL = self.original_search_url
        pansou_auth.reset_cached_pansou_token()

    def test_static_pansou_token_is_used(self):
        Config.PANSOU_AUTH_TOKEN = "static-token"  # noqa: S105 - 测试夹具，非真实凭据

        headers = pansou_auth.get_pansou_auth_headers(client=None)

        self.assertEqual(headers, {"Authorization": "Bearer static-token"})

    def test_pansou_login_token_is_cached(self):
        class DummyClient:
            def __init__(self):
                self.login_count = 0

            def post(self, url, json=None, headers=None):
                self.login_count += 1
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"token": "login-token", "expires_in": 3600}},
                    request=httpx.Request("POST", url),
                )

        client = DummyClient()

        first = pansou_auth.get_pansou_auth_headers(client)
        second = pansou_auth.get_pansou_auth_headers(client)

        self.assertEqual(first, {"Authorization": "Bearer login-token"})
        self.assertEqual(second, {"Authorization": "Bearer login-token"})
        self.assertEqual(client.login_count, 1)

    def test_pansou_login_falls_back_to_legacy_route_when_auth_path_404(self):
        class DummyClient:
            def __init__(self):
                self.login_urls = []

            def post(self, url, json=None, headers=None):
                self.login_urls.append(url)
                if url.endswith("/api/auth/login"):
                    return httpx.Response(
                        404,
                        json={"code": 404, "message": "not found"},
                        request=httpx.Request("POST", url),
                    )
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"token": "legacy-token", "expires_in": 3600}},
                    request=httpx.Request("POST", url),
                )

        client = DummyClient()

        headers = pansou_auth.get_pansou_auth_headers(client)

        self.assertEqual(headers, {"Authorization": "Bearer legacy-token"})
        self.assertEqual(
            client.login_urls,
            [
                "http://pansou.test/api/auth/login",
                "http://pansou.test/api/login",
            ],
        )

    def test_custom_pansou_login_url_is_supported(self):
        class DummyClient:
            def __init__(self):
                self.login_urls = []

            def post(self, url, json=None, headers=None):
                self.login_urls.append(url)
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"token": "custom-token", "expires_in": 3600}},
                    request=httpx.Request("POST", url),
                )

        Config.PANSOU_AUTH_LOGIN_URL = "/custom/login"
        client = DummyClient()

        headers = pansou_auth.get_pansou_auth_headers(client)

        self.assertEqual(headers, {"Authorization": "Bearer custom-token"})
        self.assertEqual(client.login_urls, ["http://pansou.test/custom/login"])

    def test_pansou_token_refreshes_once_on_401(self):
        class DummyClient:
            def __init__(self):
                self.get_headers = []
                self.login_count = 0

            def get(self, url, params=None, headers=None):
                self.get_headers.append(headers)
                if len(self.get_headers) == 1:
                    return httpx.Response(
                        401,
                        json={"code": 401, "message": "expired"},
                        request=httpx.Request("GET", url),
                    )
                return httpx.Response(
                    200,
                    json={"code": 0, "message": "ok", "data": {}},
                    request=httpx.Request("GET", url),
                )

            def post(self, url, json=None, headers=None):
                self.login_count += 1
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"token": "fresh-token", "expires_in": 3600}},
                    request=httpx.Request("POST", url),
                )

        client = DummyClient()
        pansou_auth.set_cached_pansou_token("expired-token", cache_seconds=3600)

        data = make_pansou_api_request(client, "http://pansou.test/api/health", method="GET")

        self.assertEqual(data["code"], 0)
        self.assertEqual(client.login_count, 1)
        self.assertEqual(client.get_headers[0], {"Authorization": "Bearer expired-token"})
        self.assertEqual(client.get_headers[1], {"Authorization": "Bearer fresh-token"})


class AuthTests(unittest.TestCase):
    """认证接口响应格式需与上游 pansou 保持一致（平铺 token / valid / message）。"""

    def setUp(self):
        self.original_enabled = Config.AUTH_ENABLED
        self.original_users = Config.AUTH_USERS_RAW
        self.original_secret = Config.AUTH_JWT_SECRET
        Config.AUTH_ENABLED = True
        Config.AUTH_USERS_RAW = "admin:secret"
        Config.AUTH_JWT_SECRET = "test-secret"  # noqa: S105 - 测试夹具，非真实凭据
        self.client = main.app.test_client()

    def tearDown(self):
        Config.AUTH_ENABLED = self.original_enabled
        Config.AUTH_USERS_RAW = self.original_users
        Config.AUTH_JWT_SECRET = self.original_secret

    def test_verify_requires_token_when_auth_enabled(self):
        response = self.client.get("/api/auth/verify")
        self.assertEqual(response.status_code, 401)
        body = response.get_json()
        # 与上游一致的错误格式：{"error": ..., "code": "AUTH_TOKEN_*"}
        self.assertEqual(body.get("code"), "AUTH_TOKEN_MISSING")

    def test_login_returns_flat_upstream_format(self):
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "secret"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        # 上游 pansou 平铺格式：token / expires_at / username
        self.assertIn("token", body)
        self.assertIn("expires_at", body)
        self.assertEqual(body["username"], "admin")
        self.assertNotIn("data", body)

    def test_verify_post_with_token(self):
        login = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "secret"},
        )
        token = login.get_json()["token"]

        # 上游 pansou 的 verify 仅提供 POST
        verify = self.client.post(
            "/api/auth/verify",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(verify.status_code, 200)
        body = verify.get_json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["username"], "admin")
        self.assertNotIn("data", body)

    def test_logout_returns_upstream_message(self):
        response = self.client.post("/api/auth/logout")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"message": "退出成功"})

    def test_login_rejected_when_auth_disabled(self):
        Config.AUTH_ENABLED = False
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "secret"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {"error": "认证功能未启用"})


class CheckLinksTests(unittest.TestCase):
    def setUp(self):
        self.original_enabled = Config.AUTH_ENABLED
        self.original_search_url = Config.SEARCH_API_URL
        self.original_fallback = Config.CHECK_LINKS_FALLBACK_ENABLED
        self.original_max_items = Config.CHECK_LINKS_MAX_ITEMS
        self.original_passthrough = Config.CHECK_LINKS_PASSTHROUGH_ENABLED
        self.original_pansou_auth_enabled = Config.PANSOU_AUTH_ENABLED
        self.original_pansou_auth_token = Config.PANSOU_AUTH_TOKEN
        Config.AUTH_ENABLED = False
        Config.SEARCH_API_URL = "http://pansou.test"
        Config.PANSOU_AUTH_ENABLED = False
        Config.PANSOU_AUTH_TOKEN = ""
        # 显式设定透传/回退/数量上限，避免开发者本机 .env 中的配置影响用例
        Config.CHECK_LINKS_PASSTHROUGH_ENABLED = True
        Config.CHECK_LINKS_FALLBACK_ENABLED = True
        Config.CHECK_LINKS_MAX_ITEMS = 256
        self.client = main.app.test_client()

    def tearDown(self):
        Config.AUTH_ENABLED = self.original_enabled
        Config.SEARCH_API_URL = self.original_search_url
        Config.CHECK_LINKS_FALLBACK_ENABLED = self.original_fallback
        Config.CHECK_LINKS_MAX_ITEMS = self.original_max_items
        Config.CHECK_LINKS_PASSTHROUGH_ENABLED = self.original_passthrough
        Config.PANSOU_AUTH_ENABLED = self.original_pansou_auth_enabled
        Config.PANSOU_AUTH_TOKEN = self.original_pansou_auth_token
        pansou_auth.reset_cached_pansou_token()

    def test_check_links_passes_through_upstream_response(self):
        """新版上游 pansou 有 /api/check/links 时原样透传（含 proxy_url 参数）。"""
        upstream_payload = {
            "results": [
                {
                    "disk_type": "quark",
                    "url": "https://pan.quark.cn/s/abc",
                    "state": "ok",
                    "cache_hit": True,
                }
            ]
        }

        class DummyClient:
            def __init__(self):
                self.posts = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json=None, headers=None):
                self.posts.append((url, json))
                return httpx.Response(
                    200,
                    json=upstream_payload,
                    request=httpx.Request("POST", url),
                )

        payload = {
            "items": [{"url": "https://pan.quark.cn/s/abc", "disk_type": "quark"}],
            "proxy_url": "socks5://127.0.0.1:1080",
        }

        with patch.object(pancheck.httpx, "Client", return_value=DummyClient()):
            response = self.client.post("/api/check/links", json=payload)

        self.assertEqual(response.status_code, 200)
        # 平铺 results 格式与上游一致
        self.assertEqual(response.get_json(), upstream_payload)

    def test_check_links_falls_back_to_pancheck_on_404(self):
        """上游旧版无 /api/check/links（404）时回退本地 PanCheck，响应仍为平铺格式。"""

        class Dummy404Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json=None, headers=None):
                # 透传开启时上游返回 404，代理应回退本地检测
                return httpx.Response(
                    404,
                    json={"message": "not found"},
                    request=httpx.Request("POST", url),
                )

        def fake_call_pancheck(client, links, selected_platforms=None):
            return {"valid_links": list(links)}

        payload = {
            "items": [
                {"url": "https://pan.quark.cn/s/abc", "disk_type": "quark", "password": "1234"},
                {"url": "https://example.com/bad", "disk_type": "unknown"},
            ]
        }

        with (
            patch.object(pancheck.httpx, "Client", return_value=Dummy404Client()),
            patch.object(pancheck, "call_pancheck_api", fake_call_pancheck),
        ):
            response = self.client.post("/api/check/links", json=payload)

        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        # 平铺 results，不再有 code/data 包裹
        self.assertNotIn("code", data)
        self.assertNotIn("data", data)
        self.assertEqual(data["results"][0]["state"], "ok")
        self.assertEqual(data["results"][1]["state"], "unsupported")

    def test_check_links_rejects_too_many_items(self):
        Config.CHECK_LINKS_MAX_ITEMS = 2
        payload = {
            "items": [
                {"url": f"https://pan.quark.cn/s/{i}", "disk_type": "quark"} for i in range(3)
            ]
        }

        response = self.client.post("/api/check/links", json=payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn("超过上限", response.get_json()["message"])

    def test_check_links_requires_items(self):
        response = self.client.post("/api/check/links", json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["message"], "缺少必需字段: items")

    def test_check_links_passes_upstream_4xx_through(self):
        """上游 4xx（如 proxy_url 参数非法）必须原样透传，不能吞掉走回退。"""
        error_payload = {"code": 400, "message": "无效的代理参数: 不支持的代理协议: ftp"}

        class Dummy400Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json=None, headers=None):
                return httpx.Response(
                    400,
                    json=error_payload,
                    request=httpx.Request("POST", url),
                )

        payload = {
            "items": [{"url": "https://pan.quark.cn/s/abc", "disk_type": "quark"}],
            "proxy_url": "ftp://bad",
        }

        with patch.object(pancheck.httpx, "Client", return_value=Dummy400Client()):
            response = self.client.post("/api/check/links", json=payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), error_payload)

    def test_check_links_disabled_fallback_returns_502(self):
        """上游不可用且回退被禁用时返回 502。"""
        Config.CHECK_LINKS_FALLBACK_ENABLED = False

        class DummyConnectFailClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json=None, headers=None):
                raise httpx.ConnectError("boom")

        payload = {
            "items": [{"url": "https://pan.quark.cn/s/abc", "disk_type": "quark"}],
        }

        with patch.object(pancheck.httpx, "Client", return_value=DummyConnectFailClient()):
            response = self.client.post("/api/check/links", json=payload)

        self.assertEqual(response.status_code, 502)

    def test_check_links_passthrough_disabled_uses_local_pancheck(self):
        """透传开关关闭时直接走本地 PanCheck，不请求上游。"""
        Config.CHECK_LINKS_PASSTHROUGH_ENABLED = False

        class DummyRecordingClient:
            def __init__(self):
                self.posts = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json=None, headers=None):
                self.posts.append(url)
                return httpx.Response(
                    200,
                    json={"results": []},
                    request=httpx.Request("POST", url),
                )

        def fake_call_pancheck(client, links, selected_platforms=None):
            return {"valid_links": list(links)}

        payload = {"items": [{"url": "https://pan.quark.cn/s/abc", "disk_type": "quark"}]}

        dummy = DummyRecordingClient()
        with (
            patch.object(pancheck.httpx, "Client", return_value=dummy),
            patch.object(pancheck, "call_pancheck_api", fake_call_pancheck),
        ):
            response = self.client.post("/api/check/links", json=payload)

        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        # 透传关闭：不应向上游 /api/check/links 发任何请求，直接走本地检测
        self.assertEqual(dummy.posts, [])
        self.assertEqual(data["results"][0]["state"], "ok")

    def test_check_links_legacy_links_body_is_rewritten_to_items(self):
        """旧版 links 请求体格式在透传前应重写为 items，不能原样丢给上游 400。"""

        class DummyClient:
            def __init__(self):
                self.posts = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json=None, headers=None):
                self.posts.append(json)
                return httpx.Response(
                    200,
                    json={"results": []},
                    request=httpx.Request("POST", url),
                )

        payload = {
            "links": ["https://pan.quark.cn/s/abc", "https://pan.quark.cn/s/def", 123],
            "disk_type": "quark",
        }

        dummy = DummyClient()
        with patch.object(pancheck.httpx, "Client", return_value=dummy):
            response = self.client.post("/api/check/links", json=payload)

        self.assertEqual(response.status_code, 200)
        forwarded = dummy.posts[0]
        # 非字符串项被丢弃，其余重写为上游认的 items 结构
        self.assertEqual(
            forwarded["items"],
            [
                {"url": "https://pan.quark.cn/s/abc", "disk_type": "quark"},
                {"url": "https://pan.quark.cn/s/def", "disk_type": "quark"},
            ],
        )

    def test_check_links_refreshes_pansou_token_once_on_401(self):
        """登录态访问上游 401 时刷新 token 重试一次，成功后透传响应。"""
        Config.PANSOU_AUTH_ENABLED = True
        Config.PANSOU_AUTH_TOKEN = ""

        class Dummy401Then200Client:
            def __init__(self):
                self.check_calls = []
                self.login_count = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json=None, headers=None):
                if url.endswith("/api/check/links"):
                    self.check_calls.append(headers.get("Authorization") if headers else None)
                    if len(self.check_calls) == 1:
                        return httpx.Response(
                            401,
                            json={"error": "未授权：令牌无效或已过期", "code": "AUTH_TOKEN_INVALID"},
                            request=httpx.Request("POST", url),
                        )
                    return httpx.Response(
                        200,
                        json={"results": [{"url": "https://pan.quark.cn/s/abc", "state": "ok"}]},
                        request=httpx.Request("POST", url),
                    )
                # 登录接口
                self.login_count += 1
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"token": f"token-{self.login_count}", "expires_in": 3600}},
                    request=httpx.Request("POST", url),
                )

        payload = {
            "items": [{"url": "https://pan.quark.cn/s/abc", "disk_type": "quark"}],
        }

        dummy = Dummy401Then200Client()
        pansou_auth.set_cached_pansou_token("stale-token", cache_seconds=3600)
        with patch.object(pancheck.httpx, "Client", return_value=dummy):
            response = self.client.post("/api/check/links", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(dummy.check_calls), 2)
        self.assertEqual(dummy.check_calls[0], "Bearer stale-token")
        self.assertEqual(dummy.check_calls[1], "Bearer token-1")

    def test_check_links_401_passthrough_without_pansou_auth(self):
        """未启用上游认证时，上游 401 原样透传，不触发刷新重试。"""

        class Dummy401Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json=None, headers=None):
                return httpx.Response(
                    401,
                    json={"error": "未授权：缺少认证令牌", "code": "AUTH_TOKEN_MISSING"},
                    request=httpx.Request("POST", url),
                )

        payload = {
            "items": [{"url": "https://pan.quark.cn/s/abc", "disk_type": "quark"}],
        }

        with patch.object(pancheck.httpx, "Client", return_value=Dummy401Client()) as client_factory:
            response = self.client.post("/api/check/links", json=payload)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.get_json(),
            {"error": "未授权：缺少认证令牌", "code": "AUTH_TOKEN_MISSING"},
        )
        # 未启用 PANSOU_AUTH，只调用一次，不重试
        self.assertEqual(client_factory.call_count, 1)


class PluginWebProxyTests(unittest.TestCase):
    """上游 pansou 插件 Web 管理路由（gying/qqpd/weibo/panlian）代理。"""

    def setUp(self):
        self.original_enabled = Config.AUTH_ENABLED
        self.original_search_url = Config.SEARCH_API_URL
        Config.AUTH_ENABLED = False
        Config.SEARCH_API_URL = "http://pansou.test"
        self.client = main.app.test_client()

    def tearDown(self):
        Config.AUTH_ENABLED = self.original_enabled
        Config.SEARCH_API_URL = self.original_search_url

    def test_unknown_plugin_path_returns_404(self):
        response = self.client.get("/notaplugin/anything")
        self.assertEqual(response.status_code, 404)

    def test_plugin_web_get_is_proxied(self):
        class DummyHTMLClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, params=None, headers=None):
                self.requested_url = url
                return httpx.Response(
                    200,
                    text="<html>manage page</html>",
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    request=httpx.Request("GET", url),
                )

        dummy = DummyHTMLClient()
        with patch.object(proxy.httpx, "Client", return_value=dummy):
            response = self.client.get("/gying/manage")

        self.assertEqual(response.status_code, 200)
        self.assertIn("manage page", response.get_data(as_text=True))
        self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(dummy.requested_url, "http://pansou.test/gying/manage")

    def test_plugin_web_post_is_proxied(self):
        class DummyFormClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, data=None, headers=None):
                self.requested_url = url
                self.form = data
                return httpx.Response(
                    200,
                    json={"ok": True},
                    request=httpx.Request("POST", url),
                )

        dummy = DummyFormClient()
        with patch.object(proxy.httpx, "Client", return_value=dummy):
            response = self.client.post("/qqpd/save", data={"user": "a"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(dummy.requested_url, "http://pansou.test/qqpd/save")
        self.assertIn(("user", "a"), dummy.form)


if __name__ == "__main__":
    unittest.main()
