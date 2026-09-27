import logging
import time
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from flask import Blueprint, jsonify, request

from config import Config
from pansou_auth import get_pansou_auth_headers

logger = logging.getLogger(__name__)
pancheck_bp = Blueprint("pancheck", __name__)


def extract_links_from_search_data(search_data):
    """
    从搜索结果数据中提取所有链接。
    """
    unique_links = set()

    merged_by_type = search_data.get("data", {}).get("merged_by_type", {})
    for links in merged_by_type.values():
        for item in links:
            if "url" in item:
                unique_links.add(item["url"])

    results = search_data.get("data", {}).get("results", [])
    for res in results:
        for link_obj in res.get("links", []):
            if "url" in link_obj:
                unique_links.add(link_obj["url"])

    return unique_links


def extract_valid_links_from_check_data(check_data):
    """从 PanCheck 常见响应结构中提取有效链接集合。"""
    if not isinstance(check_data, dict):
        return set()

    valid_links = check_data.get("valid_links")
    if isinstance(valid_links, list):
        return {str(link) for link in valid_links}

    data = check_data.get("data")
    if isinstance(data, dict):
        valid_links = data.get("valid_links")
        if isinstance(valid_links, list):
            return {str(link) for link in valid_links}

        results = data.get("results")
        if isinstance(results, list):
            return {
                str(item.get("url") or item.get("normalized_url"))
                for item in results
                if isinstance(item, dict) and item.get("valid")
            }

    results = check_data.get("results")
    if isinstance(results, list):
        return {
            str(item.get("url") or item.get("normalized_url"))
            for item in results
            if isinstance(item, dict) and item.get("valid")
        }

    return set()


def call_pancheck_api(client, links_to_check, selected_platforms=None):
    """调用 PanCheck API 检测链接，失败时向上抛出异常。"""
    response_data = {
        "links": list(links_to_check),
        "selected_platforms": selected_platforms or Config.SUPPORTED_PLATFORMS,
    }
    check_res = client.post(Config.CHECK_API_URL, json=response_data)
    check_res.raise_for_status()
    return check_res.json()


def call_check_api(client, links_to_check):
    """调用 PanCheck API 验证链接有效性。"""
    try:
        check_data = call_pancheck_api(client, links_to_check)
        valid_links = extract_valid_links_from_check_data(check_data)
        if valid_links:
            return valid_links

        logger.warning("验证API未返回有效链接字段，跳过过滤以防搜索结果被误清空")
        return set(links_to_check)
    except Exception as e:
        logger.warning(f"验证API错误: {str(e)}，跳过验证，返回所有链接以防搜索完全不可用")
        return set(links_to_check)


def normalize_disk_type(disk_type):
    """把 pansou 的网盘类型转换为 PanCheck 的平台名。"""
    return Config.PANCHECK_PLATFORM_ALIASES.get(str(disk_type or "").strip().lower())


def normalize_check_url(url, disk_type, password):
    """补全带提取码的检测链接。"""
    if not password:
        return url

    parsed = urlparse(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    existing_keys = {key.lower() for key, _ in query}
    if {"pwd", "password", "pass"} & existing_keys:
        return url

    param_name = "password" if str(disk_type).strip().lower() in {"115", "pan115"} else "pwd"
    query.append((param_name, password))
    return urlunparse(parsed._replace(query=urlencode(query)))


def build_check_result(item, state, summary, checked_at=None, expires_at=None):
    """构建与 pansou /api/check/links 兼容的单条检测结果。"""
    checked_at = checked_at or int(time.time() * 1000)
    expires_at = expires_at or checked_at + 24 * 3600 * 1000
    url = str(item.get("url", ""))
    disk_type = str(item.get("disk_type", ""))
    password = str(item.get("password", ""))

    return {
        "url": url,
        "disk_type": disk_type,
        "state": state,
        "normalized_url": normalize_check_url(url, disk_type, password),
        "cache_hit": False,
        "checked_at": checked_at,
        "expires_at": expires_at,
        "summary": summary,
    }


def get_netdisk_statistics(original_data, filtered_data):
    """获取原始和过滤后的网盘类型统计。"""
    original_counts = Counter()
    filtered_counts = Counter()

    merged_by_type = original_data.get("data", {}).get("merged_by_type", {})
    for netdisk_type, links in merged_by_type.items():
        original_counts[netdisk_type] += len(links)

    results = original_data.get("data", {}).get("results", [])
    for res in results:
        for link_obj in res.get("links", []):
            netdisk_type = link_obj.get("type", "unknown")
            original_counts[netdisk_type] += 1

    filtered_merged = filtered_data.get("data", {}).get("merged_by_type", {})
    for netdisk_type, links in filtered_merged.items():
        filtered_counts[netdisk_type] = len(links)

    filtered_results = filtered_data.get("data", {}).get("results", [])
    for res in filtered_results:
        for link_obj in res.get("links", []):
            netdisk_type = link_obj.get("type", "unknown")
            filtered_counts[netdisk_type] += 1

    return original_counts, filtered_counts


def filter_search_results_sync(search_data, client, request_type="POST"):
    """同步版本的过滤搜索结果函数。"""
    unique_links = extract_links_from_search_data(search_data)

    if not unique_links:
        logger.info(f"{request_type}请求：无链接需要验证，返回原始数据")
        return search_data

    total_before_filter = len(unique_links)
    logger.info(f"{request_type}请求：开始验证 {total_before_filter} 个唯一链接")

    start_time = time.time()
    valid_links_set = call_check_api(client, unique_links)
    filter_duration = time.time() - start_time

    original_merged_by_type = search_data.get("data", {}).get("merged_by_type", {})
    new_merged = {}
    for netdisk_type, links in original_merged_by_type.items():
        filtered_links = [link for link in links if link.get("url") in valid_links_set]
        if filtered_links:
            new_merged[netdisk_type] = filtered_links

    original_results = search_data.get("data", {}).get("results", [])
    new_results = []
    for result in original_results:
        original_links = result.get("links", [])
        filtered_result_links = [link for link in original_links if link.get("url") in valid_links_set]

        if filtered_result_links:
            result_copy = dict(result)
            result_copy["links"] = filtered_result_links
            new_results.append(result_copy)

    original_counts, filtered_counts = get_netdisk_statistics(search_data, {
        "data": {
            "merged_by_type": new_merged,
            "results": new_results,
        }
    })

    total_filtered_out = total_before_filter - len(valid_links_set)
    logger.info(f"{request_type}请求：过滤完成，耗时 {filter_duration:.2f}秒")
    logger.info(
        f"{request_type}请求 - 过滤前链接数: {total_before_filter}, "
        f"过滤后链接数: {len(valid_links_set)}, 过滤掉: {total_filtered_out}"
    )

    for netdisk_type in original_counts:
        original_count = original_counts[netdisk_type]
        filtered_count = filtered_counts[netdisk_type]
        logger.info(
            f"{request_type}请求 - 网盘 {netdisk_type}: {original_count} -> "
            f"{filtered_count} (过滤: {original_count - filtered_count})"
        )

    return {
        "code": search_data.get("code", 0),
        "message": search_data.get("message", ""),
        "data": {
            "total": len(new_results) if new_results else len(valid_links_set),
            "results": new_results,
            "merged_by_type": new_merged,
        },
    }


@pancheck_bp.route('/api/check/links', methods=['POST'])
def check_links():
    """链接有效性检测接口。

    新版上游 pansou 自带 /api/check/links（支持 proxy_url 等参数），优先原样透传；
    上游为旧版本无此接口（404）或透传失败时，回退到本地 PanCheck 检测，
    回退实现的响应格式也已对齐上游的平铺 results 结构。
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"code": 400, "message": "请求体必须是 JSON 对象"}), 400

    items = body.get("items")
    if items is None and isinstance(body.get("links"), list):
        # 本代理历史兼容格式：上游只认 items，透传前先重写，否则旧调用方请求会被上游 400 拒绝
        body["items"] = items = [
            {"url": link, "disk_type": body.get("disk_type", "")}
            for link in body.get("links", [])
            if isinstance(link, str)
        ]

    if not isinstance(items, list) or not items:
        return jsonify({"code": 400, "message": "缺少必需字段: items"}), 400

    if len(items) > Config.CHECK_LINKS_MAX_ITEMS:
        return jsonify({
            "code": 400,
            "message": f"items 数量 {len(items)} 超过上限 {Config.CHECK_LINKS_MAX_ITEMS}",
        }), 400

    # 优先透传新版上游 pansou 的 /api/check/links
    if is_check_links_passthrough_enabled():
        upstream_response = try_upstream_check_links(body)
        # 上游 401 且代理用登录态访问时，刷新 token 后重试一次（与 /api/search 行为一致）
        if (
            upstream_response is not None
            and upstream_response[0] == 401
            and Config.PANSOU_AUTH_ENABLED
            and not Config.PANSOU_AUTH_TOKEN
        ):
            logger.info("上游 pansou 链接检测返回 401，刷新 token 后重试")
            upstream_response = try_upstream_check_links(body, force_refresh=True)
        if upstream_response is not None:
            status_code, payload = upstream_response
            response = jsonify(payload)
            response.status_code = status_code
            response.headers["Content-Type"] = "application/json; charset=utf-8"
            return response

    if not Config.CHECK_LINKS_FALLBACK_ENABLED:
        return jsonify({"code": 502, "message": "上游链接检测不可用，且回退检测已禁用"}), 502

    return check_links_with_pancheck(items)


def is_check_links_passthrough_enabled():
    """透传开关：默认开启，便于异常时临时关闭。"""
    return Config.CHECK_LINKS_PASSTHROUGH_ENABLED


def try_upstream_check_links(body, force_refresh=False):
    """转发请求到上游 pansou /api/check/links。

    返回 (status_code, payload)；上游 404（旧版本无此接口）或其他可回退错误时
    返回 None，由调用方走本地回退。上游 4xx（参数错误等）必须原样返回，
    不能吞掉后重新检测，否则会把上游对 proxy_url 等参数的校验结论丢掉。
    """
    url = f"{Config.SEARCH_API_URL.rstrip('/')}/api/check/links"
    try:
        with httpx.Client(timeout=Config.CLIENT_TIMEOUT) as client:
            headers = get_pansou_auth_headers(client, force_refresh=force_refresh) or {}
            upstream = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as e:
        logger.warning(f"上游 pansou 链接检测不可用，回退到本地 PanCheck: {e}")
        return None

    if upstream.status_code == 404:
        logger.info("上游 pansou 未提供 /api/check/links（旧版本），回退到本地 PanCheck")
        return None

    if upstream.status_code >= 500:
        logger.warning(f"上游 pansou 链接检测返回 {upstream.status_code}，回退到本地 PanCheck")
        return None

    try:
        payload = upstream.json()
    except ValueError:
        logger.warning("上游 pansou 链接检测返回非 JSON 内容，回退到本地 PanCheck")
        return None

    if upstream.status_code == 401:
        # 上游开启了认证且代理未配置 PANSOU_AUTH_*：透传 401，让客户端感知配置缺失
        logger.warning("上游 pansou 链接检测返回 401，请检查 PANSOU_AUTH_* 配置")

    return upstream.status_code, payload


def check_links_with_pancheck(items):
    """本地 PanCheck 回退实现，响应格式对齐上游 pansou（平铺 results）。"""
    now = int(time.time() * 1000)
    expires_at = now + 24 * 3600 * 1000
    results = [None] * len(items)
    pending_links = []
    pending_index = {}
    selected_platforms = set()

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            results[index] = build_check_result(
                {"url": "", "disk_type": ""},
                "bad",
                "检测项格式错误",
                checked_at=now,
                expires_at=expires_at,
            )
            continue

        url = str(item.get("url", "")).strip()
        disk_type = str(item.get("disk_type", "")).strip()
        password = str(item.get("password", "")).strip()

        if not url:
            results[index] = build_check_result(
                item,
                "bad",
                "链接不能为空",
                checked_at=now,
                expires_at=expires_at,
            )
            continue

        pancheck_platform = normalize_disk_type(disk_type)
        if disk_type and not pancheck_platform:
            results[index] = build_check_result(
                item,
                "unsupported",
                "当前平台暂不支持检测",
                checked_at=now,
                expires_at=expires_at,
            )
            continue

        normalized_url = normalize_check_url(url, disk_type, password)
        pending_links.append(normalized_url)
        pending_index.setdefault(normalized_url, []).append((index, item))
        if pancheck_platform:
            selected_platforms.add(pancheck_platform)

    if pending_links:
        platforms = sorted(selected_platforms) if selected_platforms else Config.SUPPORTED_PLATFORMS
        try:
            with httpx.Client(timeout=Config.CLIENT_TIMEOUT) as client:
                check_data = call_pancheck_api(client, pending_links, platforms)
            valid_links = extract_valid_links_from_check_data(check_data)

            for normalized_url, indexed_items in pending_index.items():
                is_valid = normalized_url in valid_links
                for index, item in indexed_items:
                    results[index] = build_check_result(
                        item,
                        "ok" if is_valid else "bad",
                        "链接有效" if is_valid else "链接失效",
                        checked_at=now,
                        expires_at=expires_at,
                    )
        except Exception as e:
            logger.warning(f"链接检测API错误: {str(e)}，返回不确定状态")
            for indexed_items in pending_index.values():
                for index, item in indexed_items:
                    results[index] = build_check_result(
                        item,
                        "uncertain",
                        "检测失败或结果不确定",
                        checked_at=now,
                        expires_at=expires_at,
                    )

    return jsonify({
        "results": results,
    })
