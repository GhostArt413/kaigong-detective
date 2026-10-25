#!/usr/bin/env python3
"""SearchPro API-key client. Standard library only; never writes credentials."""

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

ENDPOINT = "https://api.wsa.cloud.tencent.com/SearchPro"
KEY_FILE = Path.home() / ".config/yuanbao-search/.env"


class SearchError(RuntimeError):
    def __init__(self, message, kind="service"):
        super().__init__(message)
        self.kind = kind


def request_parameters(query, parameters=None):
    """Whitelist documented business fields; never accept headers or credentials."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("查询不能为空。")
    if parameters is None:
        parameters = {}
    allowed = {"Site", "Freshness", "Mode", "Industry", "Cnt", "Deeplinks"}
    if not isinstance(parameters, dict) or not set(parameters) <= allowed:
        raise ValueError("仅接受已接入的 SearchPro 业务参数，不接受认证字段或 Query 覆盖。")
    for name, value in parameters.items():
        if name in {"Mode", "Cnt"}:
            choices = {0, 1, 2} if name == "Mode" else {10, 20, 30, 40, 50}
            if type(value) is not int or value not in choices:
                raise ValueError(name + " 不在文档允许的整数枚举内。")
        elif name == "Deeplinks":
            if type(value) is not bool:
                raise ValueError("Deeplinks 必须为布尔值。")
        elif name == "Industry":
            if not isinstance(value, str) or value not in {"gov", "news", "acad", "finance"}:
                raise ValueError("Industry 不在文档允许的枚举内。")
        elif name == "Site":
            if not isinstance(value, str):
                raise ValueError("Site 必须是域名或 exclude:域名列表。")
            domains = value.removeprefix("exclude:").split("|")
            count = 5 if value.startswith("exclude:") else 1
            domain_pattern = r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
            if not 1 <= len(domains) <= count or not all(re.fullmatch(domain_pattern, d) for d in domains):
                raise ValueError("Site 需指定一个域名，或 exclude: 后最多五个域名；不含协议和路径。")
        elif name == "Freshness":
            if not isinstance(value, str):
                raise ValueError("Freshness 必须为字符串。")
            relative = re.fullmatch(r"([dmy])([1-9][0-9]*)?", value)
            if relative:
                if int(relative[2] or 1) > {"d": 30, "m": 12, "y": 5}[relative[1]]:
                    raise ValueError("Freshness 的相对时间范围超出官方限制。")
            else:
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError()
                    day = date.fromisoformat(value)
                    if not date(1970, 1, 1) <= day <= datetime.now(timezone.utc).date():
                        raise ValueError()
                except ValueError:
                    raise ValueError("Freshness 使用 d1–d30、m1–m12、y1–y5 或有效单日；日期区间暂不接入。") from None
    return {"Query": query.strip(), **parameters}


def load_key():
    value = os.environ.get("TENCENTCLOUD_WSA_APIKEY", "").strip()
    try:
        if not value and KEY_FILE.is_file():
            for line in KEY_FILE.read_text(encoding="utf-8").splitlines():
                name, sep, raw = line.partition("=")
                if sep and name.strip() == "TENCENTCLOUD_WSA_APIKEY":
                    value = raw.strip().strip("\"'")
                    break
    except (OSError, UnicodeError):
        raise SearchError("无法读取本机元宝搜索配置。请检查文件编码、访问权限，或改用环境变量；配置内容不会输出。", "configuration") from None
    if not value or value == "replace_with_your_service_api_key":
        raise SearchError("还没找到元宝搜索 API Key。请按首次接入指引在本机配置 TENCENTCLOUD_WSA_APIKEY；无需将密钥发到聊天。", "missing_credentials")
    if any(not 33 <= ord(ch) <= 126 for ch in value):
        raise SearchError("本机元宝搜索 API Key 含无效字符，请重新配置；密钥内容不会输出。", "configuration")
    return value


def normalize(data, query):
    if not isinstance(data, dict):
        raise SearchError("元宝搜索返回结构异常。")
    result = data.get("Response", data)
    if not isinstance(result, dict):
        raise SearchError("元宝搜索 Response 结构异常。")
    if result.get("Error"):
        error = result["Error"] if isinstance(result["Error"], dict) else {}
        code = str(error.get("Code", "")).partition(".")[0]
        safe_codes = {"UnauthorizedOperation", "AuthFailure", "ResourceNotFound", "ResourceUnavailable",
                      "RequestLimitExceeded", "LimitExceeded", "InvalidParameter", "InternalError", "FailedOperation"}
        code = code if code in safe_codes else "ServiceError"
        kind = {"InvalidParameter": "parameter", "UnauthorizedOperation": "authentication", "AuthFailure": "authentication",
                "RequestLimitExceeded": "quota", "LimitExceeded": "quota"}.get(code, "service")
        raise SearchError("元宝搜索错误：" + code + "；停止相同请求，检查服务权限与额度。", kind)
    if not isinstance(result.get("Pages"), list):
        raise SearchError("元宝搜索响应缺少 Pages 数组。")
    pages = []
    try:
        for raw in result["Pages"]:
            page = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(page, dict):
                raise ValueError()
            pages.append(page)
    except (ValueError, TypeError):
        raise SearchError("元宝搜索 Pages 中含无效数据。") from None
    if not isinstance(result.get("RequestId"), str) or not result["RequestId"].strip():
        raise SearchError("元宝搜索缺少 RequestId，无法记录为可追溯查询。")
    normalized = {
        "provider": "腾讯云元宝搜索",
        "query": query,
        "request_id": result["RequestId"],
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "total_results": len(pages),
        "message": result.get("Msg", ""),
        "results": pages,
    }
    if isinstance(result.get("Version"), str) and result["Version"].strip():
        normalized["version"] = result["Version"]
    return normalized


def search(query, limit=10, parameters=None):
    payload = request_parameters(query, parameters)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("limit 必须为 1–50 的整数，只控制本地展示条数。")
    key = load_key()
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        # Server error bodies and headers can contain reflected credentials.
        kind = {400: "parameter", 401: "authentication", 403: "authentication", 429: "quota"}.get(exc.code, "service")
        raise SearchError(f"元宝搜索 HTTP {exc.code}；检查权限、额度或服务状态，不重试相同认证/额度错误。", kind) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise SearchError("元宝搜索连接失败或非 JSON 响应；暂时网络错误至多重试一次。", "network") from None
    normalized = normalize(data, query.strip())
    normalized["results"] = normalized["results"][:limit]
    normalized["request_parameters"] = payload
    return normalized
