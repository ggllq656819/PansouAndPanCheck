import os
import logging
import time
import json
from collections import Counter
import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
from flask import Flask, request, jsonify, make_response

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 环境变量配置
SEARCH_API_URL = os.getenv("SEARCH_API_URL", "http://127.0.0.1:8888")
CHECK_API_URL = os.getenv("CHECK_API_URL", "http://127.0.0.1/api/v1/links/check")


def filter_search_results_sync(search_data, client, request_type="POST"):
    """同步版本的过滤搜索结果函数"""
    # 1. 收集所有需要校验的链接 (去重)
    unique_links = set()

    # 处理 merged_by_type 结构
    merged_by_type = search_data.get("data", {}).get("merged_by_type", {})
    for netdisk_type, links in merged_by_type.items():
        for item in links:
            unique_links.add(item["url"])

    # 处理 results 结构
    results = search_data.get("data", {}).get("results", [])
    for res in results:
        for link_obj in res.get("links", []):
            unique_links.add(link_obj["url"])

    if not unique_links:
        logger.info(f"{request_type}请求：无链接需要验证，返回原始数据")
        return search_data

    # 记录过滤前的总数
    total_before_filter = len(unique_links)
    logger.info(f"{request_type}请求：开始验证 {total_before_filter} 个唯一链接")

    # 2. 调用校验接口过滤无效数据
    start_time = time.time()
    valid_links_set = set()

    try:
        # 同步发送请求
        response_data = {
            "links": list(unique_links),
            "selected_platforms": ["quark", "uc", "baidu", "tianyi", "pan123", "pan115", "xunlei", "aliyun"]
        }
        check_res = client.post(CHECK_API_URL, json=response_data)
        check_res.raise_for_status()  # 确保HTTP状态码正常
        check_data = check_res.json()
        valid_links_set = set(check_data.get("valid_links", []))
    except Exception as e:
        logger.warning(f"{request_type}请求：验证API错误: {str(e)}，跳过验证")
        # 如果校验接口失效，放行所有结果以防搜索完全不可用
        valid_links_set = unique_links

    end_time = time.time()
    filter_duration = end_time - start_time

    # 统计各网盘过滤情况
    # 重新统计各个网盘类型的数据量
    original_counts = Counter()
    filtered_counts = Counter()

    # 统计原始数据 - 从merged_by_type获取
    for netdisk_type, links in merged_by_type.items():
        original_counts[netdisk_type] += len(links)

    # 统计原始数据 - 从results获取
    for res in results:
        for link_obj in res.get("links", []):
            # 使用Link对象中的type字段作为网盘类型
            netdisk_type = link_obj.get('type', 'unknown')
            original_counts[netdisk_type] += 1

    # 过滤 merged_by_type 并统计过滤后的数量
    new_merged = {}
    for netdisk_type, links in merged_by_type.items():
        filtered_links = [l for l in links if l["url"] in valid_links_set]
        if filtered_links:
            new_merged[netdisk_type] = filtered_links
        filtered_counts[netdisk_type] = len(filtered_links)

    # 过滤 results 并统计过滤后的数量
    new_results = []
    for res in results:
        # 仅保留有效的链接对象，保持SearchResult对象的完整结构
        original_links = res.get("links", [])
        filtered_result_links = [l for l in original_links if l["url"] in valid_links_set]

        if filtered_result_links:  # 如果该条目还有有效链接，则保留该条目
            # 创建新的SearchResult对象，保持原有的所有字段
            res_copy = {
                "message_id": res.get("message_id", ""),
                "unique_id": res.get("unique_id", ""),
                "channel": res.get("channel", ""),
                "datetime": res.get("datetime", ""),
                "title": res.get("title", ""),
                "content": res.get("content", ""),
                "links": filtered_result_links,
                "tags": res.get("tags", []),
                "images": res.get("images", [])
            }
            # 保留其他可能存在的字段
            for key, value in res.items():
                if key not in ["message_id", "unique_id", "channel", "datetime", "title", "content", "links", "tags", "images"]:
                    res_copy[key] = value
            
            new_results.append(res_copy)
        
        # 统计过滤后的结果中的网盘类型（基于链接对象的type字段）
        for link_obj in filtered_result_links:
            netdisk_type = link_obj.get('type', 'unknown')
            filtered_counts[netdisk_type] += 1

    # 计算总的过滤统计
    total_after_filter = sum(filtered_counts.values())
    total_filtered_out = total_before_filter - len(valid_links_set)

    # 打印详细日志
    logger.info(f"{request_type}请求：过滤完成，耗时 {filter_duration:.2f}秒")
    logger.info(
        f"{request_type}请求 - 过滤前链接数: {total_before_filter}, 过滤后链接数: {len(valid_links_set)}, 过滤掉: {total_filtered_out}")

    for netdisk_type in original_counts.keys():
        original_count = original_counts[netdisk_type]
        filtered_count = filtered_counts[netdisk_type]
        logger.info(
            f"{request_type}请求 - 网盘 {netdisk_type}: {original_count} -> {filtered_count} (过滤: {original_count - filtered_count})")

    # 构建最终响应，保持与 pansou 一致
    final_response = {
        "code": search_data.get("code", 0),
        "message": search_data.get("message", ""),
        "data": {
            "total": len(new_results) if new_results else len(valid_links_set),
            "results": new_results,
            "merged_by_type": new_merged
        },
    }

    return final_response


@app.route('/api/search', methods=['POST'])
def proxy_search():
    # 1. 获取原始请求参数
    try:
        # 获取内容类型
        content_type = request.content_type or ""
        logger.info(f"收到请求 Content-Type: {content_type}")
        
        # 根据内容类型解析请求体
        if request.data:  # 检查是否有请求体
            logger.info(f"请求体字节长度: {len(request.data)}")
            
            if "application/json" in content_type.lower():
                # 直接使用 request.json 获取 JSON 数据
                body = request.get_json()
                if body is None:
                    logger.error("JSON解析失败，请求体不是有效的JSON格式")
                    return jsonify({"error": "请求体不是有效的JSON格式"}), 400
            elif "application/x-www-form-urlencoded" in content_type.lower() or "multipart/form-data" in content_type.lower():
                # 处理表单数据
                body = request.form.to_dict()
            else:
                # 尝试解析为JSON，不管Content-Type是什么
                try:
                    body = request.get_json(force=True)  # 强制解析
                    if body is None:
                        # 如果强制解析也失败，尝试手动解码
                        raw_data = request.data.decode('utf-8')
                        body = json.loads(raw_data)
                except Exception as e:
                    logger.error(f"无法解析请求体: {str(e)}")
                    return jsonify({"error": "请求体格式错误"}), 400
        else:
            # 请求体为空时，尝试从查询参数中获取kw
            params = request.args.to_dict()
            logger.info(f"请求体为空，查询参数: {params}")
            if "kw" not in params or not params["kw"]:
                return jsonify({"error": "缺少必需字段: kw"}), 400
            # 构造body对象
            body = {
                "kw": params["kw"],
                "res": params.get("res", "merge"),
                "src": params.get("src", "")
            }
            
        logger.info(f"解析得到的请求体: {body}")
        if "kw" not in body:
            return jsonify({"error": "缺少必需字段: kw"}), 400
    except Exception as e:
        logger.error(f"请求参数解析失败: {str(e)}")
        return jsonify({"error": f"请求参数解析失败: {str(e)}"}), 400

    # 使用同步客户端
    with httpx.Client(timeout=60.0) as client:
        # 2. 调用原始 pansou 接口获取数据
        try:
            search_res = client.post(f"{SEARCH_API_URL}/api/search", json=body)
            search_res.raise_for_status()  # 确保HTTP状态码正常

            # 尝试处理响应内容
            try:
                # 先获取文本内容，再解析为JSON，以确保正确的字符编码处理
                content = search_res.text
                search_data = json.loads(content)
            except Exception as e:
                # 如果JSON解析失败，记录原始响应内容
                logger.warning(f"搜索API返回的内容不是有效的JSON: {str(e)}, 原始响应: {content[:500] if 'content' in locals() else search_res.content[:500]}...")
                return jsonify({"error": "搜索API返回的内容格式错误"}), 500

        except httpx.ConnectError:
            logger.error(f"无法连接到搜索API: {SEARCH_API_URL}")
            return jsonify({"error": f"无法连接到搜索API: {SEARCH_API_URL}"}), 503
        except httpx.TimeoutException:
            logger.error("搜索API请求超时")
            return jsonify({"error": "搜索API请求超时"}), 408
        except Exception as e:
            logger.error(f"搜索API错误: {str(e)}")
            return jsonify({"error": f"搜索API错误: {str(e)}"}), 500

        # 使用过滤函数处理结果
        try:
            result = filter_search_results_sync(search_data, client, "POST")
            response = make_response(jsonify(result))
            response.headers['Content-Type'] = 'application/json; charset=utf-8'
            return response
        except Exception as e:
            logger.error(f"过滤结果时发生错误: {str(e)}")
            return jsonify({"error": f"处理搜索结果时发生错误: {str(e)}"}), 500


# 支持 GET 请求透传
@app.route('/api/search', methods=['GET'])
def proxy_search_get():
    params = request.args.to_dict()
    # 将查询参数转换为适合搜索API的格式
    search_params = {
        "kw": params.get("kw", ""),
        "res": params.get("res", "merge"),
        "src": params.get("src", "")
    }

    with httpx.Client(timeout=60.0) as client:
        # 2. 调用原始 pansou 接口获取数据
        try:
            search_res = client.get(f"{SEARCH_API_URL}/api/search", params=search_params)
            search_res.raise_for_status()  # 确保HTTP状态码正常

            # 尝试处理响应内容
            try:
                # 先获取文本内容，再解析为JSON，以确保正确的字符编码处理
                content = search_res.text
                search_data = json.loads(content)
            except Exception as e:
                # 如果JSON解析失败，记录原始响应内容
                logger.warning(f"GET请求 - 搜索API返回的内容不是有效的JSON: {str(e)}, 原始响应: {search_res.content[:500] if hasattr(search_res, 'content') else 'No content'}...")
                return jsonify({"error": "搜索API返回的内容格式错误"}), 500

        except httpx.ConnectError:
            logger.error(f"无法连接到搜索API: {SEARCH_API_URL}")
            return jsonify({"error": f"无法连接到搜索API: {SEARCH_API_URL}"}), 503
        except httpx.TimeoutException:
            logger.error("搜索API请求超时")
            return jsonify({"error": "搜索API请求超时"}), 408
        except Exception as e:
            logger.error(f"搜索API错误: {str(e)}")
            return jsonify({"error": f"搜索API错误: {str(e)}"}), 500

        # 使用过滤函数处理结果
        try:
            result = filter_search_results_sync(search_data, client, "GET")
            response = make_response(jsonify(result))
            response.headers['Content-Type'] = 'application/json; charset=utf-8'
            return response
        except Exception as e:
            logger.error(f"GET请求 - 过滤结果时发生错误: {str(e)}")
            return jsonify({"error": f"处理搜索结果时发生错误: {str(e)}"}), 500


@app.route('/api/health', methods=['GET'])
def health():
    """pansou健康检查接口"""
    with httpx.Client(timeout=60.0) as client:
        try:
            search_res = client.get(f"{SEARCH_API_URL}/api/health")
            search_res.raise_for_status()  # 确保HTTP状态码正常
            
            # 直接返回响应内容
            content = search_res.text
            data = json.loads(content)
            response = make_response(jsonify(data))
            response.headers['Content-Type'] = 'application/json; charset=utf-8'
            return response
                    
        except httpx.ConnectError:
            logger.error(f"无法连接到健康检查API: {SEARCH_API_URL}")
            return jsonify({"error": f"无法连接到健康检查API: {SEARCH_API_URL}"}), 503
        except httpx.TimeoutException:
            logger.error("健康检查API请求超时")
            return jsonify({"error": "健康检查API请求超时"}), 408
        except Exception as e:
            logger.error(f"健康检查API错误: {str(e)}")
            return jsonify({"error": f"健康检查API错误: {str(e)}"}), 500


if __name__ == "__main__":
    import os
    
    # 使用 Flask 内置开发服务器
    port = int(os.environ.get("PORT", 1566))
    app.run(host="0.0.0.0", port=port, debug=False)