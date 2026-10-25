#!/usr/bin/env python3
"""Local credential onboarding. status is offline; test makes one SearchPro call.

configure reads secrets only through a non-echoing local terminal prompt.
There is deliberately no --api-key argument or plaintext-key output.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile
import warnings

import yuanbao_search as client

CONSOLE_URL = "https://console.cloud.tencent.com/wsapi/index"
GUIDE_URL = "https://cloud.tencent.com/document/product/1806/130615#21242563-b79b-4db5-962a-12a9a39ebc16"
PROBE_QUERY = "腾讯云 元宝搜索 官方文档"


class SetupError(ValueError):
    """Only fixed, safe onboarding messages; never includes user input."""


def credential_status():
    """Return metadata only. This does not establish service connectivity."""
    source = "environment" if os.environ.get("TENCENTCLOUD_WSA_APIKEY", "").strip() else "local_file"
    result = {"ok": True, "scope": "local_cli", "configured": False, "connected": None,
              "network_checked": False, "mcp_status": "not_checked", "api_calls": 0,
              "credential_source": source, "config_file": str(client.KEY_FILE),
              "console_url": CONSOLE_URL, "guide_url": GUIDE_URL}
    try:
        client.load_key()  # Never return the key, its prefix, suffix, or length.
    except client.SearchError as exc:
        result.update(status=exc.kind, message=str(exc))
        if exc.kind == "missing_credentials" and source == "local_file":
            result["credential_source"] = "none"
        result["next_step"] = "可先继续需求澄清；在本机完成配置后再执行真实搜索。已有远端元宝MCP时先检查其实际调用，不重复索要本机密钥。"
        return result
    result.update(configured=True, status="configured_not_tested",
                  message="已找到本机配置，无需重新提供 API Key；尚未测试接口连通。",
                  next_step="优先用第一条实际调查查询验证连接；也可显式运行 test，消耗一次元宝请求。")
    if source == "local_file" and os.name == "posix":
        try:
            result["permissions_restricted"] = client.KEY_FILE.stat().st_mode & 0o077 == 0
        except OSError:
            result["permissions_restricted"] = None
    return result


def save_key(key):
    """Explicit local setup action. Preserve unrelated dotenv lines; write 0600."""
    if not isinstance(key, str) or not key.strip() or key.strip() == "replace_with_your_service_api_key":
        raise SetupError("请输入控制台创建的服务 API KEY；留空不会写入配置。")
    key = key.strip()
    if any(not 33 <= ord(ch) <= 126 for ch in key) or any(ch in key for ch in "\"'"):
        raise SetupError("请输入完整服务 API KEY，不要包含中文、换行、空白或引号。")
    path = client.KEY_FILE
    if path.is_symlink():
        raise SetupError("当前配置为符号链接；请在其原有管理位置修改，或使用环境变量，本向导不会覆盖链接。")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    lines, replaced = [], False
    for line in content.splitlines():
        name, sep, _ = line.partition("=")
        if sep and name.strip() == "TENCENTCLOUD_WSA_APIKEY":
            if not replaced:
                lines.append("TENCENTCLOUD_WSA_APIKEY=" + key)
                replaced = True
        else:
            lines.append(line)
    if not replaced:
        lines.append("TENCENTCLOUD_WSA_APIKEY=" + key)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            if os.name == "posix":
                os.fchmod(temp.fileno(), 0o600)
            temp.write("\n".join(lines) + "\n")
            temp.flush()
            os.fsync(temp.fileno())
        temp_path.replace(path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def configure(replace=False, prompt=None):
    state = credential_status()
    env_present = bool(os.environ.get("TENCENTCLOUD_WSA_APIKEY", "").strip())
    if env_present:
        if replace:
            raise SetupError("环境变量优先于配置文件。请在你自己的环境变量管理处更新密钥；向导不会写一个被旧环境变量遮盖的新密钥。")
        return state
    if state["configured"] and not replace:
        return state
    prompt = prompt or getpass.getpass
    # Never let getpass fall back to echoed stdin in a non-interactive agent pipe.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            key = prompt("请粘贴元宝搜索服务 API KEY（输入不回显，回车提交）：")
        except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
            raise SetupError("当前终端无法安全隐藏输入，已取消且未写入。请在自己的交互终端运行 configure；不需要把密钥发到聊天。") from None
    if not isinstance(key, str) or not key.strip():
        state["message"] = "这次没有输入 API Key，配置保持原状。可以先继续需求澄清，准备好后再连接元宝搜索。"
        return state
    save_key(key)
    state = credential_status()
    state["message"] = "API Key 已保存在本机配置文件中，尚未验证接口连通。"
    return state


def check_connection():
    state = credential_status()
    if not state["configured"]:
        state["ok"] = False
        return state
    # One real benign request, no auto-retry. Do not put the secret in the query.
    try:
        result = client.search(PROBE_QUERY, limit=1)
    except client.SearchError as exc:
        advice = {
            "authentication": "接口暂未连通：请核对服务 API KEY 是否有效、是否属于已开通元宝搜索的账号；不用在聊天中发送密钥。",
            "quota": "接口暂未连通：请在控制台检查额度或限频信息。不会自动购买或反复重试；限频时可稍后再试。",
            "network": "接口暂未连通：请检查网络、代理或服务状态。此错误不等于 API Key 无效。",
            "parameter": "接口暂未连通：服务返回参数错误，请核对官方接入方式；不要求用户反复更换密钥。",
            "service": "接口暂未连通：请检查服务是否开通、账号资源及服务状态，暂不进行重复请求。",
        }
        local_failure = exc.kind in {"missing_credentials", "configuration"}
        state.update(ok=False, connected=False, network_checked=not local_failure,
                     api_calls=0 if local_failure else 1, status=exc.kind,
                     message=advice.get(exc.kind, "本机配置暂不可用，请重新检查首次接入步骤。"))
        return state
    state.update(ok=True, connected=True, network_checked=True, api_calls=1, status="connected",
                 request_id=result["request_id"], retrieved_at=result["retrieved_at"],
                 total_results=result["total_results"],
                 message="元宝搜索已连通，可以开始带来源的搜索提问。此次仅为连接测试，尚未完成目标项目调查。",
                 next_step="回到之前的需求继续调查；连接测试不能代替开工前的发现—追查和原文问答。")
    if result.get("version"):
        state["version"] = result["version"]
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("status", help="只检查本机配置，不联网、不消耗搜索次数")
    setup = subs.add_parser("configure", help="在用户自己的终端隐藏输入并保存服务API KEY")
    setup.add_argument("--replace", action="store_true", help="明确替换已有文件配置；不覆盖环境变量")
    setup.add_argument("--test", action="store_true", help="保存后发起一次真实连接测试，可能消耗API额度")
    subs.add_parser("test", help="用现有配置发起一次真实连接测试，可能消耗API额度")
    args = parser.parse_args()
    try:
        if args.command == "status":
            result = credential_status()
        elif args.command == "test":
            result = check_connection()
        else:
            print("开工侦探 · 连接元宝搜索\n请在这个终端输入密钥，不必发送到聊天。", file=sys.stderr)
            result = configure(args.replace)
            if args.test and result["configured"]:
                print("现在发起一次真实连接测试，可能消耗1次API额度。", file=sys.stderr)
                result = check_connection()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    except SetupError as exc:
        print(json.dumps({"ok": False, "status": "configuration", "message": str(exc), "guide_url": GUIDE_URL}, ensure_ascii=False), file=sys.stderr)
        return 1
    except (ValueError, OSError, UnicodeError):
        # Configuration errors can include file content; use a fixed public message.
        print(json.dumps({"ok": False, "status": "configuration", "message": "本机配置未完成或写入取消；没有输出密钥。请检查交互终端、文件编码和访问权限，已有环境变量请在原处修改。", "guide_url": GUIDE_URL}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
