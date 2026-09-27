import base64
import hashlib
import hmac
import json
import time

from flask import Blueprint, g, jsonify, request

from config import Config

auth_bp = Blueprint("auth", __name__)

AUTH_PUBLIC_PATHS = {"/api/auth/login", "/api/auth/logout", "/api/health"}

def base64url_encode(data):
    """JWT 使用的 base64url 编码，不带填充符。"""
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def base64url_decode(data):
    """JWT 使用的 base64url 解码，自动补齐填充符。"""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8"))


def generate_jwt_token(username):
    """生成 HS256 JWT。"""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "username": username,
        "iat": now,
        "exp": now + Config.AUTH_TOKEN_EXPIRY * 3600,
    }

    signing_input = ".".join([
        base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        base64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ])
    signature = hmac.new(
        Config.AUTH_JWT_SECRET.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{base64url_encode(signature)}"


def validate_jwt_token(token):
    """
    验证 JWT 并返回 (payload, error)。
    error 为 None/expired/invalid。
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None, "invalid"

        signing_input = ".".join(parts[:2])
        expected_signature = hmac.new(
            Config.AUTH_JWT_SECRET.encode("utf-8"),
            signing_input.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        actual_signature = base64url_decode(parts[2])
        if not hmac.compare_digest(expected_signature, actual_signature):
            return None, "invalid"

        payload = json.loads(base64url_decode(parts[1]).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None, "expired"
        if not payload.get("username"):
            return None, "invalid"

        return payload, None
    except Exception:
        return None, "invalid"


# 与上游 pansou 中间件保持一致的认证错误格式
def auth_error(message, auth_code, status_code=401):
    """返回上游 pansou 兼容的认证错误响应。"""
    return jsonify({"error": message, "code": auth_code}), status_code


def authenticate_request():
    """从 Authorization 头验证 Bearer Token。"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header:
        return None, auth_error("未授权：缺少认证令牌", "AUTH_TOKEN_MISSING")
    if not auth_header.startswith("Bearer "):
        return None, auth_error("未授权：令牌格式错误", "AUTH_TOKEN_INVALID_FORMAT")

    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        return None, auth_error("未授权：令牌格式错误", "AUTH_TOKEN_INVALID_FORMAT")

    payload, error = validate_jwt_token(token)
    if error == "expired":
        return None, auth_error("未授权：令牌无效或已过期", "AUTH_TOKEN_INVALID")
    if error:
        return None, auth_error("未授权：令牌无效或已过期", "AUTH_TOKEN_INVALID")

    return payload, None


def get_forward_auth_headers():
    """将客户端 Authorization 头透传给上游 pansou。"""
    auth_header = request.headers.get("Authorization")
    return {"Authorization": auth_header} if auth_header else None


def auth_middleware():
    """启用认证后保护除登录、登出和健康检查以外的 API。"""
    if not Config.AUTH_ENABLED:
        return None
    if request.method == "OPTIONS":
        return None
    if request.path in AUTH_PUBLIC_PATHS:
        return None

    payload, response = authenticate_request()
    if response is not None:
        return response

    g.auth_username = payload.get("username")
    return None


@auth_bp.route('/api/auth/login', methods=['POST'])
def auth_login():
    """认证登录接口，响应格式与上游 pansou 保持一致（平铺 token/expires_at/username）。"""
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))

    if not username or not password:
        return jsonify({"error": "参数错误：用户名和密码不能为空"}), 400

    if not Config.AUTH_ENABLED:
        return jsonify({"error": "认证功能未启用"}), 403

    users = Config.get_auth_users()
    if not users:
        return jsonify({"error": "认证系统未正确配置"}), 500

    expected_password = users.get(username)
    if expected_password is None or not hmac.compare_digest(expected_password, password):
        return jsonify({"error": "用户名或密码错误"}), 401

    token = generate_jwt_token(username)
    expires_at = int(time.time()) + Config.AUTH_TOKEN_EXPIRY * 3600
    return jsonify({
        "token": token,
        "expires_at": expires_at,
        "username": username,
    })


@auth_bp.route('/api/auth/verify', methods=['POST'])
def auth_verify():
    """认证验证接口，上游 pansou 仅提供 POST，此处与上游保持一致。"""
    if not Config.AUTH_ENABLED:
        return jsonify({"valid": True, "message": "认证功能未启用"})

    return jsonify({
        "valid": True,
        "username": getattr(g, "auth_username", ""),
    })


@auth_bp.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    """认证登出接口。JWT 无服务端状态，客户端丢弃 token 即可。"""
    return jsonify({"message": "退出成功"})


def register_auth(app):
    app.before_request(auth_middleware)
    app.register_blueprint(auth_bp)
