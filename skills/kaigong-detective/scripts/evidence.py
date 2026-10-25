#!/usr/bin/env python3
"""Trace Yuanbao queries; check sourced interviews, priorities and legacy reports.

This checks traceability/quotes, not semantic entailment or truth. No LLM needed.
Case files have one writer; do not run parallel mutations against the same case.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, urlunsplit

from yuanbao_search import SearchError, search, request_parameters

TIERS = {"direct": 0, "multi": 1, "deep": 2}
PURPOSES = ("discover", "follow_up", "countercheck", "crosscheck")


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def url_key(url):
    if not isinstance(url, str):
        raise ValueError("来源 URL 必须为字符串。")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("仅记录不含凭据的 HTTP(S) 来源 URL。")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def normalized_text(text):
    return re.sub(r"\s+", " ", text).strip()


def digest(text):
    return hashlib.sha256(normalized_text(text).encode("utf-8")).hexdigest()


def load_case(directory):
    case = read_json(Path(directory) / "case.json")
    if case.get("schema_version") != 1:
        raise ValueError("不支持的 case 版本。")
    return case


def save_case(directory, case):
    write_json(Path(directory) / "case.json", case)


def init_case(directory, topic, budget=18, test_only=False):
    path = Path(directory) / "case.json"
    if path.exists():
        raise ValueError("调查目录已存在；继续使用该记录，或为新一轮创建新目录。")
    if not topic.strip() or isinstance(budget, bool) or budget < 1:
        raise ValueError("topic 不能为空，budget 必须大于零。")
    case = {"schema_version": 1, "topic": topic, "created_at": now(), "budget": budget, "test_only": test_only,
            "attempts": [], "searches": [], "sources": []}
    save_case(directory, case)
    return case


def validate_search_position(case, tier, follows, purpose):
    if tier not in TIERS or purpose not in PURPOSES:
        raise ValueError("未知调查层级或查询目的。")
    known = {q["id"]: q for q in case["searches"]}
    if len(follows) != len(set(follows)) or any(q not in known for q in follows):
        raise ValueError("follows 必须指向已经存在且不重复的查询。")
    if any(TIERS[known[q]["tier"]] > TIERS[tier] for q in follows):
        raise ValueError("不能让较早层依赖较深层结果。")
    if tier != "direct" and not follows:
        raise ValueError("多轮与深度查询必须记录 follows，体现基于已有结果的追问。")
    if tier == "direct" and (follows or purpose != "discover"):
        raise ValueError("直接搜索必须是无前置查询的 discover。")


def normalize_receipt(receipt):
    if not isinstance(receipt, dict) or receipt.get("provider") != "腾讯云元宝搜索":
        raise ValueError("只能导入真实元宝工具的规范化返回，不能将其他引擎改名。")
    for field in ("query", "request_id", "retrieved_at"):
        if not isinstance(receipt.get(field), str) or not receipt[field].strip():
            raise ValueError("元宝查询缺少 " + field)
    datetime.fromisoformat(receipt["retrieved_at"].replace("Z", "+00:00"))
    if not isinstance(receipt.get("results"), list):
        raise ValueError("元宝查询缺少 results 列表。")
    for page in receipt["results"]:
        if not isinstance(page, dict):
            raise ValueError("元宝 results 项必须为对象。")
        if page.get("url"):
            url_key(page["url"])
    if "request_parameters" in receipt:
        params = receipt["request_parameters"]
        if not isinstance(params, dict) or params.get("Query") != receipt["query"]:
            raise ValueError("请求参数记录必须包含与查询一致的 Query。")
        request_parameters(receipt["query"], {k: v for k, v in params.items() if k != "Query"})
    return receipt


def record_receipt(case, tier, query_reason, purpose, follows, receipt):
    validate_search_position(case, tier, follows, purpose)
    if not query_reason.strip():
        raise ValueError("每次查询须说明与前次发现或决策条件的关系。")
    receipt = normalize_receipt(receipt)
    if any(q["request_id"] == receipt["request_id"] for q in case["searches"]):
        raise ValueError("RequestId 已存在，不能把同次查询伪装成多轮。")
    entry = dict(receipt)
    entry.update(id=f"Q{len(case['searches']) + 1:02d}", tier=tier, reason=query_reason,
                 purpose=purpose, follows=list(follows))
    case["searches"].append(entry)
    return entry


def run_search(directory, tier, query, reason, purpose, follows, limit=10, parameters=None):
    case = load_case(directory)
    validate_search_position(case, tier, follows, purpose)
    if not query.strip() or not reason.strip():
        raise ValueError("查询与 reason 不能为空。")
    payload = request_parameters(query, parameters)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit 必须为 1–50 的整数。")
    if len(case["attempts"]) >= case["budget"]:
        raise ValueError("达到本轮调用尝试预算；停止查询并标明未完成项。")
    attempt = {"at": now(), "tier": tier, "query": query, "status": "pending", "request_parameters": payload}
    case["attempts"].append(attempt)
    save_case(directory, case)  # Crash/timeout still consumes a recorded attempt.
    try:
        receipt = search(query, limit, parameters) if parameters else search(query, limit)
        entry = record_receipt(case, tier, reason, purpose, follows, receipt)
    except (SearchError, ValueError) as exc:
        if getattr(exc, "kind", "") in {"missing_credentials", "configuration"}:
            # Local setup failed before any HTTP request. Do not spend a query attempt.
            case["attempts"].pop()
            case["connection_status"] = {"status": exc.kind, "at": now(), "message": str(exc)}
            save_case(directory, case)
            raise
        attempt["status"] = "failed"
        attempt["error"] = str(exc)
        attempt["error_kind"] = getattr(exc, "kind", "validation")
        save_case(directory, case)
        raise
    attempt.update(status="complete", search_id=entry["id"])
    save_case(directory, case)
    return entry


def import_search(directory, tier, reason, purpose, follows, receipt):
    case = load_case(directory)
    if len(case["attempts"]) >= case["budget"]:
        raise ValueError("达到本轮预算，不能继续导入查询。")
    entry = record_receipt(case, tier, reason, purpose, follows, receipt)
    case["attempts"].append({"at": now(), "status": "imported", "search_id": entry["id"],
                             "note": "外部工具真实返回；字段检查不证明请求发生"})
    save_case(directory, case)
    return entry


def capture_source(directory, url, text, title, subject, origin, method, from_search, via_source):
    case = load_case(directory)
    key = url_key(url)
    if not all(isinstance(s, str) and s.strip() for s in (text, title, subject, origin)):
        raise ValueError("原文、标题、subject、origin 均不能为空。")
    if method not in {"http", "api", "browser", "web_reader"}:
        raise ValueError("method 必须是实际原文读取方式，不能是摘要。")
    known_q = {q["id"]: q for q in case["searches"]}
    known_s = {s["id"]: s for s in case["sources"]}
    if not from_search and not via_source:
        raise ValueError("来源必须来自搜索发现或已读原文的实际链接。")
    if len(from_search) != len(set(from_search)) or len(via_source) != len(set(via_source)):
        raise ValueError("来源关联编号不能重复。")
    for qid in from_search:
        if qid not in known_q or key not in {url_key(p['url']) for p in known_q[qid]['results'] if p.get('url')}:
            raise ValueError("来源 URL 未出现在指定元宝查询中：" + qid)
    for sid in via_source:
        if sid not in known_s or url not in known_s[sid]["text"]:
            raise ValueError("上游已读原文中没有这个完整链接：" + sid)
    if any(url_key(s["url"]) == key and s["subject"] == subject for s in case["sources"]):
        raise ValueError("同一对象的该来源已读取；不要用重复快照凑独立来源。")
    # Prevent retroactively attributing a late read to an earlier result snapshot.
    lineages = list(from_search)
    for sid in via_source:
        lineages.extend(known_s[sid]["search_ids"])
    lineages = list(dict.fromkeys(lineages))
    source = {"id": f"S{len(case['sources']) + 1:02d}", "url": url, "title": title,
              "subject": subject, "origin": origin, "method": method, "read_at": now(),
              "available_tier": max((q["tier"] for q in case["searches"]), key=TIERS.get),
              "search_ids": lineages, "via_sources": list(via_source), "text": text,
              "content_sha256": digest(text)}
    case["sources"].append(source)
    save_case(directory, case)
    return {k: v for k, v in source.items() if k != "text"}


def validate_report(case, report):
    if isinstance(report, dict) and report.get("schema_version") == 2:
        from interview import validate_interview, validate_start
        return validate_start(case, report) if report.get("stage") == "ready_to_start" else validate_interview(case, report)
    errors = []
    if not isinstance(report, dict):
        return ["报告必须为 JSON 对象。"]
    def need(condition, message):
        if not condition:
            errors.append(message)
    need(isinstance(report.get("question"), str) and bool(report["question"].strip()), "缺少访谈问题。")
    need(isinstance(report.get("recommendation"), str) and bool(report["recommendation"].strip()), "缺少原访谈推荐。")
    results = report.get("results")
    if not isinstance(results, list) or [r.get("level") if isinstance(r, dict) else None for r in results] != list(TIERS):
        return [*errors, "必须按顺序提供 direct、multi、deep 三个实际结果。"]
    queries = {q["id"]: q for q in case["searches"]}
    sources = {s["id"]: s for s in case["sources"]}
    for result in results:
        tier = result["level"]
        tag = tier + ": "
        status = result.get("status", "complete")
        need(status in {"complete", "incomplete"}, tag + "未知执行状态。")
        if status == "incomplete":
            need(isinstance(result.get("incomplete_reason"), str) and bool(result["incomplete_reason"].strip()), tag + "未完成须说明原因。")
        need(isinstance(result.get("summary"), str) and bool(result["summary"].strip()), tag + "缺少结论。")
        qids = result.get("search_ids", [])
        if not isinstance(qids, list) or not all(isinstance(q, str) for q in qids):
            errors.append(tag + "search_ids 必须是编号列表。")
            qids = []
        need(len(qids) == len(set(qids)), tag + "查询编号重复。")
        selected = [queries[q] for q in qids if q in queries]
        need(len(selected) == len(qids), tag + "引用了不存在的查询。")
        need(all(TIERS[q["tier"]] <= TIERS[tier] for q in selected), tag + "较早层引用了后续调查。")
        if status == "complete":
            need(any(q["tier"] == "direct" for q in selected), tag + "缺少直接搜索。")
        if status == "complete" and tier in {"multi", "deep"}:
            need(sum(q["tier"] == "multi" for q in selected) >= 2, tag + "没有两次实际 multi 追问。")
        if status == "complete" and tier == "deep":
            own = [q for q in selected if q["tier"] == "deep"]
            need(len(own) >= 2, tag + "没有两次实际 deep 查询。")
            need(any(q["purpose"] == "countercheck" for q in own), tag + "缺少反证查询。")
        for q in selected:
            need(all(parent in qids for parent in q["follows"]), tag + "遗漏查询依赖。")
        checks = result.get("checks")
        if not isinstance(checks, list) or not checks:
            errors.append(tag + "缺少逐项核验。")
            checks = []
        for index, check in enumerate(checks):
            prefix = f"{tag}核验{index}: "
            if not isinstance(check, dict):
                errors.append(prefix + "核验须为对象。")
                continue
            verdict = check.get("verdict")
            need(verdict in {"lead", "supported", "conflict", "unknown"}, prefix + "未知判定。")
            need(all(isinstance(check.get(k), str) and bool(check[k].strip()) for k in ("subject", "claim")), prefix + "缺少对象或完整主张。")
            if tier == "direct":
                need(verdict in {"lead", "unknown"}, prefix + "直接搜索不能称原文核实。")
            if status == "incomplete":
                need(verdict in {"lead", "unknown"}, prefix + "未执行完的层级不可声称完成核验。")
            evidence = check.get("evidence", [])
            if not isinstance(evidence, list):
                errors.append(prefix + "evidence 须为列表。")
                evidence = []
            supporting, opposing = [], []
            seen = set()
            for item in evidence:
                if not isinstance(item, dict):
                    errors.append(prefix + "证据须为对象。")
                    continue
                sid = item.get("source_id")
                source = sources.get(sid) if isinstance(sid, str) else None
                if not source:
                    errors.append(prefix + "来源不存在。")
                    continue
                need(sid not in seen, prefix + "同一来源重复引用。")
                seen.add(sid)
                need(source["subject"] == check.get("subject"), prefix + "引文属于其他对象。")
                need(TIERS[source["available_tier"]] <= TIERS[tier], prefix + "把后续读取的原文倒填到早期结果。")
                need(all(q in qids for q in source["search_ids"]), prefix + "来源不属于本层查询。")
                quote = item.get("quote")
                need(isinstance(quote, str) and bool(normalized_text(quote)) and normalized_text(quote) in normalized_text(source["text"]), prefix + "引文不在原文中。")
                need(isinstance(item.get("review"), str) and bool(item["review"].strip()), prefix + "缺少语义核对理由。")
                stance = item.get("stance")
                need(stance in {"supports", "contradicts", "context"}, prefix + "缺少证据方向。")
                if stance == "supports":
                    supporting.append(source)
                elif stance == "contradicts":
                    opposing.append(source)
            if verdict == "supported":
                need(bool(supporting) and not opposing, prefix + "没有纯支持证据或存在未解决反证。")
                if tier == "deep":
                    need(len({s["origin"].casefold().strip() for s in supporting}) >= 2 and len({s["content_sha256"] for s in supporting}) >= 2,
                         prefix + "未取得两组独立且不同正文的支持，保留未知。")
            if verdict == "conflict":
                need(bool(opposing), prefix + "冲突判定缺少反证。")
        options = result.get("options")
        if not isinstance(options, list) or not options:
            errors.append(tag + "缺少供用户选择的实际方向。")
            options = []
        for option in options:
            if not isinstance(option, dict):
                errors.append(tag + "选项须为对象。")
                continue
            need(all(isinstance(option.get(k), str) and bool(option[k].strip()) for k in ("label", "answer", "tradeoff")), tag + "选项缺少内容或取舍。")
            indexes = option.get("check_indexes")
            need(isinstance(indexes, list) and bool(indexes) and all(type(i) is int and 0 <= i < len(checks) for i in indexes), tag + "选项没有有效的核验依据。")
        unknowns = result.get("unknowns")
        need(isinstance(unknowns, list) and all(isinstance(x, str) for x in unknowns), tag + "unknowns 须为字符串列表，可为空列表。")
    return errors


def render_report(case, report):
    if isinstance(report, dict) and report.get("schema_version") == 2:
        from interview import render_interview
        return render_interview(case, report)
    errors = validate_report(case, report)
    if errors:
        raise ValueError("\n".join(errors))
    sources = {s["id"]: s for s in case["sources"]}
    queries = {q["id"]: q for q in case["searches"]}
    labels = {"direct": "1. 直接搜索结果", "multi": "2. 多轮搜索结果", "deep": "3. 多轮搜索、交叉核对的深度调查"}
    verdicts = {"lead": "搜索线索", "supported": "原文支持", "conflict": "存在冲突", "unknown": "待验证"}
    lines = [f"# {report['question']}", "", f"➡️ {report['recommendation']}", "", "以下为额外的三层搜索结果，决策仍由你选择。"]
    for result in report["results"]:
        lines += ["", "## " + labels[result["level"]], "", result["summary"]]
        if result.get("status") == "incomplete":
            lines += ["", "**未完成**：" + result["incomplete_reason"]]
        for index, check in enumerate(result["checks"]):
            lines += ["", f"**核验 {index} · {verdicts[check['verdict']]}**：{check['claim']}"]
            for item in check.get("evidence", []):
                source = sources[item["source_id"]]
                lines += ["", "> " + item["quote"].replace("\n", "\n> "), "",
                          f"[{source['title']}]({source['url']}) · {source['id']} · {item['review']}"]
        lines += ["", "可选方向："]
        for option in result["options"]:
            statuses = "、".join(dict.fromkeys(verdicts[result['checks'][i]['verdict']] for i in option['check_indexes']))
            lines += [f"- **{option['label']}**（{statuses}）：{option['answer']} 取舍：{option['tradeoff']}"]
        if result["unknowns"]:
            lines += ["", "仍不知道：" + "；".join(result["unknowns"])]
        lines += ["", "查询记录："]
        for qid in result["search_ids"]:
            q = queries[qid]
            lines += [f"- {qid} · {q['query']} · {q['retrieved_at']} · RequestId `{q['request_id']}`"]
            if result["level"] == "direct":
                for page in q["results"]:
                    if page.get("url"):
                        lines += [f"  - 搜索线索：[{page.get('title') or page['url']}]({page['url']})"]
    lines += ["", "请选择你接受的方向，或提出自己的条件。", "", "记录与引文检查不等于语义正确或已运行方案；来源独立性也需人工/Agent核对。"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    p = subs.add_parser("init")
    p.add_argument("--case", required=True); p.add_argument("--topic", required=True); p.add_argument("--budget", type=int, default=18)
    p.add_argument("--test-only", action="store_true", help="仅测试技能，禁止转为样例项目开工")
    for command in ("search", "record-search"):
        p = subs.add_parser(command)
        p.add_argument("--case", required=True); p.add_argument("--tier", choices=TIERS, required=True)
        p.add_argument("--reason", required=True); p.add_argument("--purpose", choices=PURPOSES, required=True)
        p.add_argument("--follows", action="append", default=[])
        if command == "search":
            p.add_argument("--query", required=True); p.add_argument("--limit", type=int, default=10)
            p.add_argument("--site"); p.add_argument("--freshness")
            p.add_argument("--mode", type=int, choices=(0, 1, 2))
            p.add_argument("--industry", choices=("gov", "news", "acad", "finance"))
            p.add_argument("--cnt", type=int, choices=(10, 20, 30, 40, 50))
            p.add_argument("--deeplinks", action="store_true", default=None)
        else:
            p.add_argument("--input", required=True, help="MCP真实返回的规范化JSON")
    p = subs.add_parser("capture")
    for flag in ("case", "url", "text-file", "title", "subject", "origin", "method"):
        p.add_argument("--" + flag, required=True)
    p.add_argument("--from-search", action="append", default=[]); p.add_argument("--via-source", action="append", default=[])
    for command in ("check", "render", "check-start"):
        p = subs.add_parser(command)
        p.add_argument("--case", required=True); p.add_argument("--report", required=True)
        if command == "render": p.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = init_case(args.case, args.topic, args.budget, args.test_only)
        elif args.command == "search":
            parameters = {key: value for key, value in {
                "Site": args.site, "Freshness": args.freshness, "Mode": args.mode,
                "Industry": args.industry, "Cnt": args.cnt, "Deeplinks": args.deeplinks,
            }.items() if value is not None}
            result = run_search(args.case, args.tier, args.query, args.reason, args.purpose, args.follows, args.limit, parameters)
        elif args.command == "record-search":
            result = import_search(args.case, args.tier, args.reason, args.purpose, args.follows, read_json(args.input))
        elif args.command == "capture":
            result = capture_source(args.case, args.url, Path(args.text_file).read_text(encoding="utf-8"), args.title,
                                    args.subject, args.origin, args.method, args.from_search, args.via_source)
        else:
            case, report = load_case(args.case), read_json(args.report)
            if args.command == "check-start":
                from interview import validate_start
                errors = validate_start(case, report)
            else:
                errors = validate_report(case, report)
            result = {"ok": not errors, "errors": errors, "scope": "记录、归属、引文与保守标签；不验证语义蕴含或用户回答真实性",
                      "ready_to_start": not errors and (args.command == "check-start" or report.get("stage") == "ready_to_start")}
            if errors:
                print(json.dumps(result, ensure_ascii=False, indent=2)); return 1
            if args.command == "render":
                output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(render_report(case, report), encoding="utf-8")
                result["output"] = str(output)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (ValueError, SearchError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr); return 1


if __name__ == "__main__":
    sys.exit(main())
