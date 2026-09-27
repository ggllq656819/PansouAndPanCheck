"""
应用配置文件
"""

import os
import secrets

from dotenv import load_dotenv

load_dotenv()


class Config:
    """基础配置类"""
    # API 地址配置
    SEARCH_API_URL = os.getenv("SEARCH_API_URL", "http://127.0.0.1:8888")
    CHECK_API_URL = os.getenv("CHECK_API_URL", "http://127.0.0.1/api/v1/links/check")

    # 应用配置
    PORT = int(os.getenv("PORT", 1566))
    HOST = os.getenv("HOST", "0.0.0.0")  # noqa: S104 - Docker 部署需要对外提供服务，有意绑定全部接口
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"

    # 认证配置，与 pansou 保持一致，默认关闭
    AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() in ("true", "1", "yes", "on")
    AUTH_USERS_RAW = os.getenv("AUTH_USERS", "")
    AUTH_TOKEN_EXPIRY = int(os.getenv("AUTH_TOKEN_EXPIRY", 24))  # 小时
    AUTH_JWT_SECRET = os.getenv("AUTH_JWT_SECRET", "")

    # 上游 pansou 认证配置，代理访问 pansou 服务时使用
    PANSOU_AUTH_ENABLED = os.getenv("PANSOU_AUTH_ENABLED", "false").lower() in ("true", "1", "yes", "on")
    PANSOU_AUTH_USERNAME = os.getenv("PANSOU_AUTH_USERNAME", "")
    PANSOU_AUTH_PASSWORD = os.getenv("PANSOU_AUTH_PASSWORD", "")
    PANSOU_AUTH_TOKEN = os.getenv("PANSOU_AUTH_TOKEN", "")
    PANSOU_AUTH_LOGIN_URL = os.getenv("PANSOU_AUTH_LOGIN_URL", "").strip()

    # HTTP 客户端配置
    CLIENT_TIMEOUT = float(os.getenv("CLIENT_TIMEOUT", 60.0))  # 秒
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))

    # /api/check/links 透传与回退配置
    # 与上游 pansou 一致的单次检测链接数量上限
    CHECK_LINKS_MAX_ITEMS = int(os.getenv("CHECK_LINKS_MAX_ITEMS", 256))
    # 上游 pansou 无 /api/check/links（旧版本）或透传失败时，回退到本地 PanCheck 检测
    CHECK_LINKS_FALLBACK_ENABLED = os.getenv("CHECK_LINKS_FALLBACK_ENABLED", "true").lower() in ("true", "1", "yes", "on")
    # /api/check/links 是否优先透传上游新版接口，便于异常时临时关闭
    CHECK_LINKS_PASSTHROUGH_ENABLED = os.getenv("CHECK_LINKS_PASSTHROUGH_ENABLED", "true").lower() in ("true", "1", "yes", "on")

    # 日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # 支持的网盘平台
    SUPPORTED_PLATFORMS = [
        "quark", "uc", "baidu", "tianyi",
        "pan123", "pan115", "xunlei", "aliyun"
    ]

    # pansou 网盘类型到 PanCheck 平台名的映射
    PANCHECK_PLATFORM_ALIASES = {
        "quark": "quark",
        "uc": "uc",
        "baidu": "baidu",
        "aliyun": "aliyun",
        "tianyi": "tianyi",
        "xunlei": "xunlei",
        "123": "pan123",
        "pan123": "pan123",
        "115": "pan115",
        "pan115": "pan115",
    }

    @classmethod
    def get_auth_users(cls):
        """解析 AUTH_USERS，格式：user1:pass1,user2:pass2"""
        users = {}
        if not cls.AUTH_USERS_RAW:
            return users

        for pair in cls.AUTH_USERS_RAW.split(","):
            if ":" not in pair:
                continue
            username, password = pair.split(":", 1)
            username = username.strip()
            password = password.strip()
            if username and password:
                users[username] = password
        return users

    @classmethod
    def validate(cls):
        """验证配置的有效性"""
        if not cls.SEARCH_API_URL:
            raise ValueError("SEARCH_API_URL 不能为空")
        if not cls.CHECK_API_URL:
            raise ValueError("CHECK_API_URL 不能为空")
        if cls.PORT <= 0 or cls.PORT > 65535:
            raise ValueError("PORT 必须在 1-65535 范围内")
        if cls.CLIENT_TIMEOUT <= 0:
            raise ValueError("CLIENT_TIMEOUT 必须大于 0")
        if cls.AUTH_TOKEN_EXPIRY <= 0:
            raise ValueError("AUTH_TOKEN_EXPIRY 必须大于 0")
        if cls.AUTH_ENABLED and not cls.get_auth_users():
            raise ValueError("启用 AUTH_ENABLED 时必须配置 AUTH_USERS")
        if cls.AUTH_ENABLED and not cls.AUTH_JWT_SECRET:
            raise ValueError("启用 AUTH_ENABLED 时必须配置固定的 AUTH_JWT_SECRET")
        if cls.PANSOU_AUTH_ENABLED:
            has_static_token = bool(cls.PANSOU_AUTH_TOKEN)
            has_login_credentials = bool(cls.PANSOU_AUTH_USERNAME and cls.PANSOU_AUTH_PASSWORD)
            if not has_static_token and not has_login_credentials:
                raise ValueError(
                    "启用 PANSOU_AUTH_ENABLED 时必须配置 PANSOU_AUTH_TOKEN "
                    "或 PANSOU_AUTH_USERNAME/PANSOU_AUTH_PASSWORD"
                )

    @classmethod
    def ensure_runtime_defaults(cls):
        """认证未启用时为本地开发提供临时 JWT 密钥。"""
        if not cls.AUTH_ENABLED and not cls.AUTH_JWT_SECRET:
            cls.AUTH_JWT_SECRET = secrets.token_urlsafe(32)
