# v2：有出处的访谈和项目优先级

新访谈使用 `schema_version: 2`。不再要求 direct/multi/deep 三份结果。脚本仍保留旧报告检查与渲染，以读取历史记录。

## 证据引用

先由真实元宝调用记录查询，再实际读取正文并 capture。每条引用为：

```json
{
  "source_id": "S01",
  "subject": "github:owner/project",
  "quote": "原文中的逐字短引文",
  "kind": "implementation",
  "review": "这段原文支持了什么，是否覆盖用户条件"
}
```

kind 为 `implementation`（实际做法）、`rationale`（原文明说为何选择）、`limitation`、`status`、`license`、`cost`、`context`。review 是 Agent 的核对说明，不是新证据。source_id 须存在、对象归属匹配、引文可匹配正文、查询发现路径包含在本轮search_ids中。读取失败/blocked来源不允许作为事实引文。

## 顶层与问题

```json
{
  "schema_version": 2,
  "topic": "本轮功能与取舍",
  "search_status": "complete",
  "search_ids": ["Q01"],
  "constraints": [{"id": "C1", "text": "用户已明确的硬条件"}],
  "questions": [],
  "candidates": [],
  "ranking_summary": "按哪个功能和哪些已定条件排序"
}
```

search_status 可为 `complete`、`partial`、`unavailable`、`not_needed`。complete只表示本轮选定的搜索动作完成，不代表事实已实测。初轮基本澄清尚未搜索时可用not_needed，search_ids与candidates为空，只进行开工侦探普通提问；该状态不能通过开工检查。partial/unavailable不能虚构搜索问题来源。

每个问题含 `id`、`origin`（grill-me/yuanbao）、`question`、`recommendation`。yuanbao问题还须有非空 `trigger`、`evidence` 与 `candidate_ids`；后者可以为空但要说明并无具体项目而是查到相关约束。无需给纯偏好普通问题硬填来源。

`origin` 的内部枚举保留以兼容既有 v2 记录，不直接作为用户可见标签。渲染映射固定为：`grill-me` → “开工侦探提问”，`yuanbao` → “开工侦探经过元宝搜索后提问”。手写聊天回复与脚本输出使用相同名称；上游引用仅用于溯源。

搜索方式不由报告手填“高阶”标签。渲染器从本题及其推荐所引来源的 `search_ids`，递归追到查询的 `follows`，按实际查询链显示“普通搜索”“多次独立搜索”“多轮搜索”或“多轮搜索与反证核验／深入追查”。多轮须包含至少两个不同 RequestId 和真实依赖；不能因为本轮其他题使用多轮，就给本题也加多轮标签。显示句式为“开工侦探经过元宝搜索的〔实际方式〕查到：〔trigger〕”，并附查询编号。

已接入的 CLI 把实际发送的业务 JSON 保存为 `request_parameters`，不含密钥、认证头或用户私密资料；服务 Version 保存为 `version`。渲染器只从本题查询链读取参数名和值类型，说明实际请求的功能；这不证明过滤效果。旧 Query-only MCP 收据不补填参数，也不据此宣称使用高阶功能。原始收据、账号版本和执行结果仍需核对。

recommendation 含 `answer`、`reason`、`basis`（judgment/yuanbao）、`status`（supported/conditional/unknown）、`evidence`。搜索问题的推荐basis必须为yuanbao且有引用，原文不足以决定时status标unknown，answer解释保留哪项未知。普通问题若推荐由检索支持，也可basis=yuanbao并附证据。

若推荐明确优先某些候选，附 `preferred_candidate_ids`，只能引用本轮未排除的候选；它与问题里用于比较的candidate_ids不同，后者可以包含被排除项用于解释风险。

项目取舍迁移需加 `transfer_reason`：基于用户哪些已定条件，为什么参考这个做法，以及哪里不能直接照搬。建议可为“先做小样验证”，不能把这种建议说成已证明转换效果/运行成功。

## 候选与优先级

每个候选包含：

- `id`、`name`、`subject`、`url`：真实身份与原始地址。
- `comparison_group`、`role`（end_to_end/component/service）、`approach`：比较哪个功能，实际做法及职责。
- `evidence`：实际做法的原文引用，不能仅有搜索摘要。
- `rationale`：含 `kind`（documented/inferred/unknown）、`text` 和 `evidence`。documented须有kind=rationale的原始证据；inferred在输出明确标Agent适配推断。
- `quality`：恰好包含 `fit`、`evidence`、`maintenance`、`reuse_cost`、`license` 五项，每项 `assessment`、`status`（supported/unknown）、`evidence`；不懂的项保留unknown，supported必须有引用。
- `hard_checks`：每项 `constraint_id`、`status`（supported/conflict/unknown）、`evidence`。每个用户硬条件须有记录；supported/conflict必须有引用。
- `eligibility`：eligible/conditional/excluded；任何硬冲突须excluded，硬条件未知不能eligible。
- `priority`：组内1、2、3…的比较顺序；excluded为null。同层连续，互补组件分组。
- `rank_reason`、`unknowns`：为什么这样排，以及缺什么。非排除项若无原文不能列为推荐候选，可在普通对话里另列“摘要线索”。

排序规则是对用户任务的定性判断，程序只检查依据关联与硬条件约束，不自动证明这个排名“最优”。不得以stars代替quality依据，不编评分、测试数据或作者原因。

## 运行

```bash
python3 "<技能目录>/scripts/evidence.py" check --case "调查目录" --report "访谈.json"
python3 "<技能目录>/scripts/evidence.py" render --case "调查目录" --report "访谈.json" --output "本轮访谈.md"
```

输出问题用“开工侦探提问”和“开工侦探经过元宝搜索后提问”区分来源，推荐单独附引用，候选按组和优先级展示。机械检查不等于语义核验或实际运行。

## 开工记录 start_gate

普通初轮仍使用 v2，`stage` 默认为 `interview`。`search_status=not_needed` 只说明该轮尚未搜索，不免除实施前门槛。`check` 与 `render` 的通过不是开工许可；CLI 返回 `ready_to_start=false`。保留旧报告读取，不把历史报告自动升级成开工证据。

开始目标项目的制作前，汇总当前范围的真实搜索、已回答问题与最终理解，设置 `stage=ready_to_start` 并运行 `check-start`。示意结构如下，答案必须来自真实用户，不能照抄此示意或替测试样例标记真实授权：

```json
{
  "stage": "ready_to_start",
  "start_gate": {
    "test_only": false,
    "answers": [
      {"question_id": "Q9", "answer": "用户对该搜索问题的实际回答", "resolution": "accepted"}
    ],
    "advanced": {
      "status": "used",
      "search_ids": ["Q02"],
      "reason": "根据初查发现，用 Site 定位原始发布者的文档"
    },
    "confirmation": {
      "answer": "搜索问题处理完后，用户对共同理解的实际确认",
      "after_search_ids": ["Q01", "Q02"]
    }
  }
}
```

其他必需的 v2 字段仍照常提供。`answers.resolution` 为 accepted/revised 时表示已处理；deferred 不放行。修订后的方案还须纳入共同理解。初始化测试目录时用 `init --test-only`，case 与报告均保持测试标记。测试会话 `test_only=true`，`check-start` 应拒绝；测试技能的成功表现正是不会制作样例项目。

开工检查要求：

- 至少两次不同 RequestId 的真实元宝查询，并有追查依赖；失败尝试不充成功次数。
- 至少一个已回答的搜索问题，其原文证据链覆盖该发现—追查；其他问题的多轮不能借用。
- 高阶参数须出现在该问题实际查询链的 `request_parameters`。`Cnt`、本地 limit 或已知版本不支持的参数不能充作语义高阶功能。记录请求不等于服务过滤效果已实测。
- 搜索问题及推荐的原文引用通过已有校验，随后有覆盖本轮查询的共同理解确认。

若真实请求得到参数错误，`advanced` 可改为 `{"status":"unavailable","reason":"实际受限原因与降级做法","failure_attempts":[3]}`。索引从1开始，对应 case.attempts 中的失败请求；该记录须含非空安全错误、`error_kind=parameter` 和实际高阶业务参数。认证、额度、网络错误或自行填写“不适用”不能触发降级。降级仍须完成成功的多轮搜索、原文问答与确认。无法使用任何可发送高阶参数的客户端时，保留工具契约限制并继续访谈；门槛保持未通过，不伪造参数失败。

旧 MCP 规范化返回缺少 `request_parameters` 或 `version` 时保持缺失。字段核对不能证明请求、回答未被手工伪造，也不能证明提问确实重要；这些由实际工具执行、对话和语义审查保证。`check-start` 是 Agent 流程中的可执行检查，不是系统层对全部工具的拦截器。
