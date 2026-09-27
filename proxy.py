import json
import logging

import httpx
from flask import Blueprint, jsonify, make_response, request

from config import Config
from pancheck import filter_search_results_sync
from pansou_auth import get_pansou_auth_headers, is_unauthorized_error

logger = logging.getLogger(__name__)
proxy_bp = Blueprint("proxy", __name__)


def get_query_param_pairs():
    """保留重复查询参数，避免 channels=a&channels=b 这类参数被压扁。"""
    return [
        (key, value)
        for key, values in request.args.lists()
        for value in values
    ]


def parse_request_body():
    """解析搜索请求体并提取参数。"""
    content_type = request.content_type or ""
    content_type_lower = content_type.lower()
    logger.info(f"收到请求 Content-Type: {content_type}")

    if "application/json" in content_type_lower:
        body = request.get_json(silent=True)
        if body is None:
            logger.error("JSON解析失败，请求体不是有效的JSON格式")
            raise ValueError("请求体不是有效的JSON格式")
    elif (
        "application/x-www-form-urlencoded" in content_type_lower
        or "multipart/form-data" in content_type_lower
    ):
        body = request.form.to_dict(flat=True)
    elif request.data:
        logger.info(f"请求体字节长度: {len(request.data)}")
        try:
            raw_data = request.data.decode("utf-8")
            body = json.loads(raw_data)
        except Exception as e:
            logger.error(f"无法解析请求体: {str(e)}")
            raise ValueError("请求体格式错误") from e
    else:
        params = request.args.to_dict(flat=True)
        logger.info(f"请求体为空，查询参数: {params}")
        body = {
            "kw": params.get("kw", ""),
            "res": params.get("res", "merge"),
            "src": params.get("src", ""),
        }

    logger.info(f"解析得到的请求体: {body}")
    if not isinstance(body, dict):
        raise ValueError("请求体必须是对象")
    if not body.get("kw"):
        raise ValueError("缺少必需字段: kw")

    return body


def make_api_request(client, url, method="POST", data=None, params=None, headers=None):
    """发起 API 请求的通用方法。"""
    try:
        if method.upper() == "POST":
            response = client.post(url, json=data, headers=headers)
        else:
            response = client.get(url, params=params, headers=headers)

        response.raise_for_status()
        return parse_json_response(response)
    except httpx.ConnectError:
        logger.error(f"无法连接到API: {url}")
        raise ConnectionError(f"无法连接到API: {url}") from None
    except httpx.TimeoutException:
        logger.error("API请求超时")
        raise TimeoutError("API请求超时") from None
    except json.JSONDecodeError:
        logger.warning(
            "API返回的内容不是有效的JSON, 原始响应: "
            f"{response.content[:500] if hasattr(response, 'content') else 'No content'}..."
        )
        raise ValueError("API返回的内容格式错误") from None
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 401:
            raise
        logger.error(f"API错误: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"API错误: {str(e)}")
        raise e


def make_pansou_api_request(client, url, method="POST", data=None, params=None):
    """发起上游 pansou 请求；token 过期时自动刷新重试一次。"""
    try:
        return make_api_request(
            client,
            url,
            method=method,
            data=data,
            params=params,
            headers=get_pansou_auth_headers(client),
        )
    except Exception as e:
        if Config.PANSOU_AUTH_ENABLED and not Config.PANSOU_AUTH_TOKEN and is_unauthorized_error(e):
            logger.info("上游 pansou token 失效，刷新后重试")
            return make_api_request(
                client,
                url,
                method=method,
                data=data,
                params=params,
                headers=get_pansou_auth_headers(client, force_refresh=True),
            )
        raise


def parse_json_response(response):
    """解析 JSON 响应；上游偶发非法 UTF-8 时使用替换字符容错。"""
    try:
        return response.json()
    except UnicodeDecodeError:
        logger.warning("API返回内容包含非法UTF-8字节，已使用替换字符容错解析")
        encoding = response.encoding or "utf-8"
        content = response.content.decode(encoding, errors="replace")
        return json.loads(content)


@proxy_bp.route('/api/search', methods=['POST'])
def proxy_search():
    """代理搜索接口 - POST请求。"""
    try:
        body = parse_request_body()
    except ValueError as e:
        logger.error(f"请求参数解析失败: {str(e)}")
        return jsonify({"error": f"请求参数解析失败: {str(e)}"}), 400
    except Exception as e:
        logger.error(f"请求参数解析失败: {str(e)}")
        return jsonify({"error": f"请求参数解析失败: {str(e)}"}), 400

    with httpx.Client(timeout=Config.CLIENT_TIMEOUT) as client:
        try:
            search_data = make_pansou_api_request(
                client,
                f"{Config.SEARCH_API_URL}/api/search",
                method="POST",
                data=body,
            )
        except ConnectionError as e:
            logger.error(str(e))
            return jsonify({"error": str(e)}), 503
        except TimeoutError as e:
            logger.error(str(e))
            return jsonify({"error": str(e)}), 408
        except ValueError as e:
            logger.error(str(e))
            return jsonify({"error": str(e)}), 500
        except Exception as e:
            logger.error(f"搜索API错误: {str(e)}")
            return jsonify({"error": f"搜索API错误: {str(e)}"}), 500

        try:
            result = filter_search_results_sync(search_data, client, "POST")
            response = make_response(jsonify(result))
            response.headers["Content-Type"] = "application/json; charset=utf-8"
            return response
        except Exception as e:
            logger.error(f"过滤结果时发生错误: {str(e)}")
            return jsonify({"error": f"处理搜索结果时发生错误: {str(e)}"}), 500


@proxy_bp.route('/api/search', methods=['GET'])
def proxy_search_get():
    """代理搜索接口 - GET请求。"""
    search_params = get_query_param_pairs()

    with httpx.Client(timeout=Config.CLIENT_TIMEOUT) as client:
        try:
            search_data = make_pansou_api_request(
                client,
                f"{Config.SEARCH_API_URL}/api/search",
                method="GET",
                params=search_params,
            )
        except ConnectionError as e:
            logger.error(str(e))
            return jsonify({"error": str(e)}), 503
        except TimeoutError as e:
            logger.error(str(e))
            return jsonify({"error": str(e)}), 408
        except ValueError as e:
            logger.error(str(e))
            return jsonify({"error": str(e)}), 500
        except Exception as e:
            logger.error(f"搜索API错误: {str(e)}")
            return jsonify({"error": f"搜索API错误: {str(e)}"}), 500

        try:
            result = filter_search_results_sync(search_data, client, "GET")
            response = make_response(jsonify(result))
            response.headers["Content-Type"] = "application/json; charset=utf-8"
            return response
        except Exception as e:
            logger.error(f"GET请求 - 过滤结果时发生错误: {str(e)}")
            return jsonify({"error": f"处理搜索结果时发生错误: {str(e)}"}), 500


@proxy_bp.route('/api/health', methods=['GET'])
def health():
    """健康检查接口。"""
    with httpx.Client(timeout=Config.CLIENT_TIMEOUT) as client:
        try:
            health_data = make_pansou_api_request(client, f"{Config.SEARCH_API_URL}/api/health", method="GET")
            if isinstance(health_data, dict):
                health_data["auth_enabled"] = Config.AUTH_ENABLED
            response = make_response(jsonify(health_data))
            response.headers["Content-Type"] = "application/json; charset=utf-8"
            return response
        except ConnectionError as e:
            logger.error(str(e))
            return jsonify({"error": str(e)}), 503
        except TimeoutError as e:
            logger.error(str(e))
            return jsonify({"error": str(e)}), 408
        except Exception as e:
            logger.error(f"健康检查API错误: {str(e)}")
            return jsonify({"error": f"健康检查API错误: {str(e)}"}), 500


# 上游 pansou 插件注册的 Web 管理路由前缀（见 pansou plugin RegisterWebRoutes）
PLUGIN_WEB_PREFIXES = ("gying", "qqpd", "weibo", "panlian")


def proxy_plugin_web_request(path_prefix, param):
    """透传上游 pansou 插件 Web 管理页请求（GET/POST /<plugin>/<param>）。

    这些路由不在 /api 下、由插件自行注册，响应多为 HTML 而非 JSON，
    所以原样转发状态码、响应体与常见的内容类型，不做任何解析改写。
    """
    upstream_path = f"/{path_prefix}/{param}"
    url = f"{Config.SEARCH_API_URL.rstrip('/')}{upstream_path}"

    try:
        with httpx.Client(timeout=Config.CLIENT_TIMEOUT) as client:
            headers = get_pansou_auth_headers(client) or {}
            if request.method == "POST":
                form_pairs = [
                    (key, value)
                    for key, values in request.form.lists()
                    for value in values
                ]
                upstream = client.post(url, data=form_pairs, headers=headers)
            else:
                query_pairs = get_query_param_pairs()
                upstream = client.get(url, params=query_pairs, headers=headers)
    except httpx.HTTPError as e:
        logger.error(f"插件 Web 路由代理失败: {upstream_path}: {e}")
        return jsonify({"error": "无法连接到上游 pansou 服务"}), 503

    content_type = upstream.headers.get("Content-Type", "text/html; charset=utf-8")
    response = make_response(upstream.content)
    response.status_code = upstream.status_code
    response.headers["Content-Type"] = content_type
    return response


@proxy_bp.route('/<path_prefix>/<path:param>', methods=['GET', 'POST'])
def proxy_plugin_web(path_prefix, param):
    """插件 Web 管理页代理，仅匹配上游插件注册过的路由前缀。"""
    if path_prefix not in PLUGIN_WEB_PREFIXES:
        return jsonify({"error": "接口不存在"}), 404
    return proxy_plugin_web_request(path_prefix, param)
