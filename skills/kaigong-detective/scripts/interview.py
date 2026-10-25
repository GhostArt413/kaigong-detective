"""Evidence-backed interview questions and qualitative candidate priority checks.

Mechanical checks cannot establish entailment, motive, or optimal ranking.
"""
import re
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from yuanbao_search import request_parameters

KINDS = {"implementation", "rationale", "limitation", "status", "license", "cost", "context"}
QUALITY = {"fit", "evidence", "maintenance", "reuse_cost", "license"}


def text(value):
    return isinstance(value, str) and bool(value.strip())


def normalized(value):
    return re.sub(r"\s+", " ", value).strip()


def url_key(url):
    if not isinstance(url, str):
        return None
    try:
        p = urlsplit(url)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            return None
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))
    except ValueError:
        return None


def validate_interview(case, report):
    errors = []
    def need(ok, message):
        if not ok:
            errors.append(message)
    if not isinstance(report, dict) or report.get("schema_version") != 2:
        return ["访谈须指定 schema_version=2。"]
    need(text(report.get("topic")), "缺少访谈主题。")
    status = report.get("search_status")
    need(status in {"complete", "partial", "unavailable", "not_needed"}, "未知搜索执行状态。")
    ids = report.get("search_ids")
    if not isinstance(ids, list) or not all(text(x) for x in ids):
        errors.append("search_ids 须为查询编号列表。")
        ids = []
    queries = {q["id"]: q for q in case["searches"]}
    sources = {s["id"]: s for s in case["sources"]}
    need(len(ids) == len(set(ids)) and all(q in queries for q in ids), "查询不存在或重复。")
    if status == "complete":
        need(bool(ids), "未实际搜索不能声称搜索完成。")
    if status == "not_needed":
        need(not ids, "不需要搜索的访谈不能混入搜索收据。")
    for qid in ids:
        if qid in queries:
            need(all(p in ids for p in queries[qid]["follows"]), "遗漏查询前置依赖。")

    def refs(value, context, subject=None, required=False):
        if not isinstance(value, list):
            errors.append(context + "：证据须为列表。")
            return []
        if required:
            need(bool(value), context + "：缺少原文出处。")
        found, seen = [], set()
        for ref in value:
            if not isinstance(ref, dict):
                errors.append(context + "：证据须为对象。")
                continue
            sid = ref.get("source_id")
            source = sources.get(sid) if isinstance(sid, str) else None
            if not source:
                errors.append(context + "：来源不存在。")
                continue
            need(source.get("read_status", "ok") == "ok" and not source["text"].lstrip().startswith(("Client Challenge", "Just a moment...")), context + "：读取失败或挑战页不能作为原文。")
            need(ref.get("subject") == source["subject"] and (subject is None or source["subject"] == subject), context + "：跨对象引用。")
            need(all(q in ids for q in source["search_ids"]), context + "：来源不在本轮查询发现路径中。")
            quote = ref.get("quote")
            need(text(quote) and normalized(quote) in normalized(source["text"]), context + "：引文不在原文。")
            need(ref.get("kind") in KINDS and text(ref.get("review")), context + "：缺少证据类别或语义核对说明。")
            key = (sid, quote if isinstance(quote, str) else "", ref.get("kind") if isinstance(ref.get("kind"), str) else "")
            need(key not in seen, context + "：证据重复。")
            seen.add(key)
            found.append(ref)
        return found

    constraints = report.get("constraints", [])
    if not isinstance(constraints, list):
        errors.append("constraints 须为列表。")
        constraints = []
    constraint_ids = []
    for c in constraints:
        if isinstance(c, dict) and text(c.get("id")) and text(c.get("text")):
            constraint_ids.append(c["id"])
        else:
            errors.append("硬条件须有编号和用户要求。")
    need(len(constraint_ids) == len(set(constraint_ids)), "硬条件编号重复。")

    candidates = report.get("candidates", [])
    if not isinstance(candidates, list):
        errors.append("candidates 须为列表。")
        candidates = []
    candidate_ids, groups = [], {}
    known_urls = {url_key(s["url"]) for s in case["sources"]}
    known_urls.update(url_key(p.get("url")) for qid in ids if qid in queries for p in queries[qid]["results"] if p.get("url"))
    for c in candidates:
        if not isinstance(c, dict):
            errors.append("候选须为对象。")
            continue
        cid = c.get("id")
        for field in ("id", "name", "subject", "comparison_group", "approach", "rank_reason"):
            need(text(c.get(field)), f"候选{cid}缺少{field}。")
        if text(cid):
            candidate_ids.append(cid)
        need(c.get("role") in {"end_to_end", "component", "service"}, f"候选{cid}职责类别不明。")
        url = url_key(c.get("url"))
        github_root = url and urlsplit(url).hostname == "github.com" and len(urlsplit(url).path.strip('/').split('/')) == 2
        from_repo_file = bool(github_root) and any(s["subject"] == c.get("subject") and url_key(s["url"]) and url_key(s["url"]).startswith(url.rstrip('/') + '/') for s in case["sources"])
        need(bool(url) and (url in known_urls or from_repo_file), f"候选{cid}地址未在实际发现中出现。")
        refs(c.get("evidence"), f"候选{cid}实际做法", c.get("subject"), required=True)
        rationale = c.get("rationale")
        if not isinstance(rationale, dict):
            errors.append(f"候选{cid}缺少取舍说明。")
        else:
            need(rationale.get("kind") in {"documented", "inferred", "unknown"} and text(rationale.get("text")), f"候选{cid}取舍说明无效。")
            why = refs(rationale.get("evidence", []), f"候选{cid}作者取舍", c.get("subject"), rationale.get("kind") == "documented")
            if rationale.get("kind") == "documented":
                need(any(r.get("kind") == "rationale" for r in why), f"候选{cid}不能把实现推断冒充作者解释。")
        quality = c.get("quality")
        if not isinstance(quality, dict) or set(quality) != QUALITY:
            errors.append(f"候选{cid}须说明匹配、证据、维护、复用成本和许可五项质量依据。")
        else:
            for name, value in quality.items():
                if not isinstance(value, dict):
                    errors.append(f"候选{cid}质量{name}无效。")
                    continue
                need(text(value.get("assessment")) and value.get("status") in {"supported", "unknown"}, f"候选{cid}质量{name}须说明判断与未知。")
                refs(value.get("evidence", []), f"候选{cid}质量{name}", c.get("subject"), value.get("status") == "supported")
        checks = c.get("hard_checks")
        if not isinstance(checks, list):
            errors.append(f"候选{cid}缺少硬条件核对。")
            checks = []
        check_ids, verdicts = [], []
        for check in checks:
            if not isinstance(check, dict):
                errors.append(f"候选{cid}硬条件须为对象。")
                continue
            check_ids.append(check.get("constraint_id")); verdicts.append(check.get("status"))
            need(check.get("constraint_id") in constraint_ids and check.get("status") in {"supported", "conflict", "unknown"}, f"候选{cid}硬条件编号或判定无效。")
            refs(check.get("evidence", []), f"候选{cid}硬条件", c.get("subject"), check.get("status") in {"supported", "conflict"})
        need(len(check_ids) == len(constraint_ids) and set(check_ids) == set(constraint_ids), f"候选{cid}遗漏或重复硬条件。")
        eligibility, priority = c.get("eligibility"), c.get("priority")
        need(eligibility in {"eligible", "conditional", "excluded"}, f"候选{cid}推荐资格无效。")
        if "conflict" in verdicts:
            need(eligibility == "excluded", f"候选{cid}存在硬冲突却进入推荐序列。")
        elif "unknown" in verdicts:
            need(eligibility != "eligible", f"候选{cid}硬条件未知却声称已满足。")
        if eligibility == "excluded":
            need(priority is None, f"候选{cid}已排除却仍有推荐优先级。")
        else:
            need(type(priority) is int and priority > 0, f"候选{cid}缺少正整数优先级。")
            group = c.get("comparison_group")
            if text(group) and type(priority) is int:
                groups.setdefault(group, []).append(priority)
        need(isinstance(c.get("unknowns"), list) and all(isinstance(v, str) for v in c["unknowns"]), f"候选{cid}须列出未知。")
    need(len(candidate_ids) == len(set(candidate_ids)), "候选编号重复。")
    for name, priorities in groups.items():
        need(sorted(priorities) == list(range(1, len(priorities) + 1)), f"组{name}优先级重复或不连续。")
    if candidates:
        need(text(report.get("ranking_summary")), "缺少排序口径与理由。")
    if status == "not_needed":
        need(not candidates, "无搜索访谈不能列检索候选排名。")

    questions = report.get("questions")
    if not isinstance(questions, list) or not questions:
        return [*errors, "缺少本轮实际访谈问题。"]
    question_ids = []
    for q in questions:
        if not isinstance(q, dict):
            errors.append("问题须为对象。")
            continue
        need(text(q.get("id")) and text(q.get("question")), "问题缺少编号或内容。")
        if text(q.get("id")):
            question_ids.append(q["id"])
        origin = q.get("origin")
        need(origin in {"grill-me", "yuanbao"}, "必须区分开工侦探普通提问与经过元宝搜索后的提问。")
        refs(q.get("evidence", []), "提问来源", required=origin == "yuanbao")
        if origin == "yuanbao":
            need(bool(ids) and text(q.get("trigger")) and text(q.get("transfer_reason")), "开工侦探经过元宝搜索后提问缺少真实查询、触发发现或用户适配理由。")
        ids_for_question = q.get("candidate_ids", [])
        need(isinstance(ids_for_question, list) and all(x in candidate_ids for x in ids_for_question), "问题引用未知候选。")
        rec = q.get("recommendation")
        if not isinstance(rec, dict):
            errors.append("缺少推荐答案。")
            continue
        need(text(rec.get("answer")) and text(rec.get("reason")), "推荐缺少答案或理由。")
        basis = rec.get("basis")
        need(basis in {"judgment", "yuanbao"} and rec.get("status") in {"supported", "conditional", "unknown"}, "推荐依据标签或状态无效。")
        if origin == "yuanbao":
            need(basis == "yuanbao", "开工侦探经过元宝搜索后提问的推荐也必须给检索依据。")
        refs(rec.get("evidence", []), "推荐来源", required=basis == "yuanbao")
        preferred = rec.get("preferred_candidate_ids", [])
        need(isinstance(preferred, list) and all(isinstance(cid, str) and cid in candidate_ids for cid in preferred), "推荐引用未知候选。")
        if isinstance(preferred, list):
            excluded = {c["id"] for c in candidates if isinstance(c, dict) and c.get("eligibility") == "excluded" and isinstance(c.get("id"), str)}
            need(not any(isinstance(cid, str) and cid in excluded for cid in preferred), "推荐答案不能主推已排除的候选。")
    need(len(question_ids) == len(set(question_ids)), "访谈问题编号重复。")
    return errors


def question_search_trace(case, question):
    """Describe only the query chain supporting this question, not the whole case."""
    sources = {s["id"]: s for s in case["sources"]}
    queries = {q["id"]: q for q in case["searches"]}
    refs = question.get("evidence", []) + question.get("recommendation", {}).get("evidence", [])
    pending = [qid for ref in refs for qid in sources[ref["source_id"]]["search_ids"]]
    used = set()
    while pending:
        qid = pending.pop()
        if qid in used:
            continue
        used.add(qid)
        pending.extend(queries[qid]["follows"])
    chain = [q for q in case["searches"] if q["id"] in used]
    multiple = len({q["request_id"] for q in chain}) > 1
    dependent = multiple and any(q["follows"] for q in chain)
    if dependent:
        method = "多轮搜索"
        if any(q["purpose"] == "countercheck" for q in chain):
            method += "与反证核验"
        elif any(q["tier"] == "deep" for q in chain):
            method += "与深入追查"
    else:
        method = "多次独立搜索" if multiple else "普通搜索"
    features = []
    for query in chain:
        params = query.get("request_parameters", {})
        if not isinstance(params, dict):
            continue
        for key, label in [("Site", "站点限定搜索／过滤（Site）"),
                           ("Freshness", "时效过滤（Freshness）")]:
            if text(params.get(key)):
                features.append(label)
        if type(params.get("Mode")) is int and params["Mode"] in {0, 1, 2}:
            features.append("信源类型选择（Mode）")
        if params.get("Industry") in ("gov", "news", "acad", "finance"):
            features.append("垂直领域搜索（Industry）")
        if params.get("Deeplinks") is True:
            features.append("附件子链（Deeplinks）")
        if type(params.get("Cnt")) is int and params["Cnt"] in {10, 20, 30, 40, 50}:
            features.append("返回条数设置（Cnt）")
    return method, [q["id"] for q in chain], list(dict.fromkeys(features))


def advanced_parameters(query):
    """Requested semantic filters, subject to known account-version limits."""
    params = query.get("request_parameters", {})
    if not isinstance(params, dict):
        return set()
    features = {key for key in ("Site", "Freshness", "Industry") if text(params.get(key))}
    if type(params.get("Mode")) is int and params["Mode"] in {0, 1, 2}:
        features.add("Mode")
    if params.get("Deeplinks") is True:
        features.add("Deeplinks")
    if query.get("version") == "lite":
        features -= {"Mode", "Industry", "Deeplinks"}
    elif query.get("version") == "standard":
        features.discard("Deeplinks")
    return features


def validate_start(case, report):
    """A separate start check; ordinary interview validation cannot open this gate."""
    if not isinstance(report, dict) or report.get("schema_version") != 2:
        return ["开工须使用 v2 访谈及 start_gate；旧报告只作历史兼容。"]
    errors = validate_interview(case, report)
    def need(ok, message):
        if not ok:
            errors.append(message)
    gate = report.get("start_gate")
    if not isinstance(gate, dict):
        return [*errors, "缺少开工前搜索与共同理解确认记录 start_gate。"]
    need(gate.get("test_only") is False and case.get("test_only") is not True,
         "测试或未明确区分测试的会话不能转为真实开工授权。")
    need(report.get("search_status") == "complete", "开工前必须完成真实元宝搜索；not_needed/partial/unavailable 均不能放行。")
    ids = report.get("search_ids", [])
    ids = ids if isinstance(ids, list) else []
    queries = {q["id"]: q for q in case["searches"] if q.get("id") in ids}
    for query in queries.values():
        try:
            if not text(query.get("request_id")) or not text(query.get("retrieved_at")):
                raise ValueError()
            datetime.fromisoformat(query["retrieved_at"].replace("Z", "+00:00"))
            params = query.get("request_parameters", {"Query": query.get("query")})
            if not isinstance(params, dict) or params.get("Query") != query.get("query"):
                raise ValueError()
            request_parameters(query.get("query"), {k: v for k, v in params.items() if k != "Query"})
        except (TypeError, ValueError):
            errors.append("查询缺少有效 RequestId、时间、查询词或真实业务参数记录。")
    request_ids = [q.get("request_id") for q in queries.values() if text(q.get("request_id"))]
    need(len(request_ids) == len(queries) and len(queries) >= 2 and len(set(request_ids)) == len(request_ids),
         "开工至少需要两次不同 RequestId 的真实查询。")
    need(all(q.get("provider") == "腾讯云元宝搜索" for q in queries.values()), "开工证据必须来自元宝搜索，不能用其他引擎替代。")
    need(any(q.get("follows") and q.get("purpose") in {"follow_up", "countercheck", "crosscheck"}
             for q in queries.values()), "开工需要基于前次发现的追查依赖，独立查询次数不能冒充多轮。")

    answers = gate.get("answers", [])
    answers = answers if isinstance(answers, list) else []
    resolved = {a.get("question_id") for a in answers if isinstance(a, dict)
                and a.get("resolution") in {"accepted", "revised"} and text(a.get("answer"))}
    evidence_chains = set()
    for question in report.get("questions", []):
        if question.get("origin") != "yuanbao" or question.get("id") not in resolved:
            continue
        # Invalid references were reported above; never turn them into a start pass.
        if errors:
            continue
        _, chain, _ = question_search_trace(case, question)
        chain_queries = [queries[qid] for qid in chain if qid in queries]
        if len({q.get("request_id") for q in chain_queries}) >= 2 and any(q.get("follows") for q in chain_queries):
            evidence_chains.update(chain)
    need(bool(evidence_chains), "须有引用本轮多轮原文的搜索问题及用户已处理的回答；未回答或暂缓的问题不能充作开工依据。")

    advanced = gate.get("advanced")
    if not isinstance(advanced, dict) or not text(advanced.get("reason")):
        errors.append("缺少高阶接口功能的实际使用或受限原因记录。")
    elif advanced.get("status") == "used":
        used = advanced.get("search_ids", [])
        need(isinstance(used, list) and bool(used) and all(isinstance(qid, str) and qid in evidence_chains
             and advanced_parameters(queries.get(qid, {})) for qid in used),
             "高阶功能须来自已回答问题的真实查询链；只加 Cnt、其他问题的参数或已知不支持的版本不能充数。")
    elif advanced.get("status") == "unavailable":
        attempts = case.get("attempts", [])
        indexes = advanced.get("failure_attempts", [])
        valid = isinstance(indexes, list) and bool(indexes)
        for index in indexes if isinstance(indexes, list) else []:
            if type(index) is not int or not 1 <= index <= len(attempts):
                valid = False
                continue
            attempt = attempts[index - 1]
            valid = valid and attempt.get("status") == "failed" and attempt.get("error_kind") == "parameter" \
                and text(attempt.get("error")) and bool(advanced_parameters(attempt))
        need(valid, "高阶参数降级须有真实参数失败记录；认证、额度、网络错误或一句不适用不能代替。")
    else:
        errors.append("高阶接口功能必须实际使用，或凭真实参数失败记录明确降级。")

    confirmation = gate.get("confirmation", {})
    need(isinstance(confirmation, dict) and text(confirmation.get("answer"))
         and isinstance(confirmation.get("after_search_ids"), list)
         and set(ids) <= set(confirmation["after_search_ids"]),
         "须在搜索问题处理完后确认共同理解，搜索前的连续‘是’不能代替该确认。")
    return errors


def render_interview(case, report):
    errors = validate_start(case, report) if report.get("stage") == "ready_to_start" else validate_interview(case, report)
    if errors:
        raise ValueError("\n".join(errors))
    sources = {s["id"]: s for s in case["sources"]}
    def links(refs):
        seen, values = set(), []
        for ref in refs:
            sid = ref["source_id"]
            if sid not in seen:
                seen.add(sid); source = sources[sid]
                values.append(f"[{source['title']}]({source['url']})")
        return "、".join(values)
    lines = ["# " + report["topic"], "", "搜索状态：" + report["search_status"]]
    for q in report["questions"]:
        searched = q["origin"] == "yuanbao"
        label = "🔎" if searched else "❓"
        kind = "开工侦探经过元宝搜索后提问" if searched else "开工侦探提问"
        lines += ["", f"{label} **{q['id']} · {kind}**", ""]
        if searched:
            method, query_ids, features = question_search_trace(case, q)
            lines += [f"开工侦探经过元宝搜索的{method}查到：" + q["trigger"], "",
                      "搜索方式记录：" + "、".join(query_ids) + "（追查依赖见查询记录）", ""]
            if features:
                lines += ["本题查询链实际记录的接口功能：" + "、".join(features), ""]
            lines += ["提问来源：" + links(q["evidence"]), ""]
        lines.append(q["question"])
        rec = q["recommendation"]
        basis = "元宝搜索依据" if rec["basis"] == "yuanbao" else "设计判断"
        state = {"supported": "原文依据", "conditional": "条件性建议", "unknown": "待验证"}[rec["status"]]
        lines += ["", f"➡️ **推荐答案（{basis}，{state}）**：{rec['answer']}", "", "理由：" + rec["reason"]]
        if rec.get("evidence"):
            lines += ["", "推荐来源：" + links(rec["evidence"])]
        if searched:
            lines += ["", "对你的适配判断：" + q["transfer_reason"]]
    candidates = report.get("candidates", [])
    if candidates:
        lines += ["", "## 相似功能的选择优先级", "", report["ranking_summary"]]
        group_names = list(dict.fromkeys(c["comparison_group"] for c in candidates))
        def cell(s):
            return str(s).replace("|", "\\|").replace("\n", " ")
        for group in group_names:
            lines += ["", "### " + group, "", "| 优先级 | 项目及实际做法 | 排序理由 | 来源与未知 |", "|---|---|---|---|"]
            for c in sorted((c for c in candidates if c['comparison_group'] == group), key=lambda c: c['priority'] if c['priority'] is not None else 10**9):
                rank = "排除" if c["eligibility"] == "excluded" else "P" + str(c["priority"]) + ("（条件性）" if c["eligibility"] == "conditional" else "")
                lines.append("| " + " | ".join(map(cell, [rank, f"[{c['name']}]({c['url']})：{c['approach']}", c["rank_reason"], links(c["evidence"]) + "；未知：" + "；".join(c["unknowns"])])) + " |")
            for c in (c for c in candidates if c["comparison_group"] == group):
                rationale = c["rationale"]
                kind = {"documented": "作者有原文解释", "inferred": "Agent 适配推断", "unknown": "作者取舍原因未知"}[rationale["kind"]]
                lines += ["", f"**{c['name']} · {kind}**：{rationale['text']}", ""]
                for name, label in [("fit", "匹配"), ("evidence", "证据"), ("maintenance", "维护"), ("reuse_cost", "复用成本"), ("license", "许可")]:
                    quality = c["quality"][name]
                    lines.append(f"- {label}（{'有依据' if quality['status']=='supported' else '未知'}）：{quality['assessment']} " + links(quality["evidence"]))
    lines += ["", "请回答本轮问题；回答“是”接受相应推荐，也可以给自己的条件。我会据此更新下一轮。", "", "出处与引文检查不证明语义正确、作者动机或实际运行成功；排序针对当前条件，尚未替你作出决定。"]
    return "\n".join(lines) + "\n"
