import logging
import threading
import time

import httpx

from auth import get_forward_auth_headers
from config import Config

logger = logging.getLogger(__name__)

_token_lock = threading.RLock()
_cached_token = None
_cached_token_expires_at = 0


def reset_cached_pansou_token():
    """清空上游 pansou token 缓存，主要用于测试。"""
    global _cached_token, _cached_token_expires_at
    with _token_lock:
        _cached_token = None
        _cached_token_expires_at = 0


def get_pansou_auth_headers(client, force_refresh=False):
    """
    返回访问上游 pansou 的 Authorization 头。
    未启用上游认证时，继续透传调用方带来的 Authorization。
    """
    if not Config.PANSOU_AUTH_ENABLED:
        return get_forward_auth_headers()

    token = get_pansou_token(client, force_refresh=force_refresh)
    return {"Authorization": f"Bearer {token}"} if token else None


def get_pansou_token(client, force_refresh=False):
    """获取并缓存上游 pansou token。"""
    if Config.PANSOU_AUTH_TOKEN:
        return Config.PANSOU_AUTH_TOKEN

    now = time.time()
    with _token_lock:
        if not force_refresh and _cached_token and now < _cached_token_expires_at:
            return _cached_token

        token, expires_in = login_to_pansou(client)
        cache_seconds = max(int(expires_in or 3600) - 60, 60)
        set_cached_pansou_token(token, cache_seconds)
        return token


def set_cached_pansou_token(token, cache_seconds):
    """缓存 token，预留刷新缓冲时间。"""
    global _cached_token, _cached_token_expires_at
    _cached_token = token
    _cached_token_expires_at = time.time() + cache_seconds


def login_to_pansou(client):
    """调用上游 pansou 登录接口。"""
    payload = {
        "username": Config.PANSOU_AUTH_USERNAME,
        "password": Config.PANSOU_AUTH_PASSWORD,
    }
    login_urls = get_pansou_login_urls()
    last_error = None

    for login_url in login_urls:
        try:
            response = client.post(login_url, json=payload)
            response.raise_for_status()
            data = response.json()
            token, expires_in = extract_token_from_login_response(data)
            if not token:
                raise ValueError("上游 pansou 登录成功但未返回 token")

            logger.info("上游 pansou 认证成功，token 已缓存，接口: %s", login_url)
            return token, expires_in
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if should_try_next_login_url(exc, login_url):
                logger.info("上游登录接口不存在，尝试下一个候选路径: %s", login_url)
                continue
            raise

    if last_error:
        raise last_error
    raise ValueError("未找到可用的上游 pansou 登录接口")


def get_pansou_login_urls():
    """返回上游 pansou 登录接口候选路径。"""
    if Config.PANSOU_AUTH_LOGIN_URL:
        return [normalize_pansou_login_url(Config.PANSOU_AUTH_LOGIN_URL)]

    base_url = Config.SEARCH_API_URL.rstrip("/")
    return [
        f"{base_url}/api/auth/login",
        f"{base_url}/api/login",
    ]


def normalize_pansou_login_url(login_url):
    """将自定义登录地址规范化为可请求的完整 URL。"""
    if login_url.startswith(("http://", "https://")):
        return login_url
    if not login_url.startswith("/"):
        login_url = f"/{login_url}"
    return f"{Config.SEARCH_API_URL.rstrip('/')}{login_url}"


def should_try_next_login_url(error, login_url):
    """仅在默认候选路径 404 时继续尝试下一个登录地址。"""
    return (
        not Config.PANSOU_AUTH_LOGIN_URL
        and error.response is not None
        and error.response.status_code == 404
        and login_url.endswith("/api/auth/login")
    )


def extract_token_from_login_response(data):
    """兼容常见登录响应结构提取 token 和有效期。"""
    if not isinstance(data, dict):
        return None, None

    if data.get("token"):
        return data.get("token"), data.get("expires_in")

    payload = data.get("data")
    if isinstance(payload, dict):
        token = (
            payload.get("token")
            or payload.get("access_token")
            or payload.get("jwt")
        )
        expires_in = (
            payload.get("expires_in")
            or payload.get("expires")
            or payload.get("expire")
        )
        return token, expires_in

    return None, None


def is_unauthorized_error(error):
    """判断 httpx 异常是否为上游 401。"""
    return (
        isinstance(error, httpx.HTTPStatusError)
        and error.response is not None
        and error.response.status_code == 401
    )
