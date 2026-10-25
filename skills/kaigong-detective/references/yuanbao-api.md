# 元宝搜索接入与高阶参数

核对日期：2026-10-25。以下区分官方契约、代码已接入与现场实际验证。

## 官方依据

- [产品页](https://cloud.tencent.com/product/wsa)、[概述](https://cloud.tencent.com/document/product/1806/121794)、[优势](https://cloud.tencent.com/document/product/1806/121795)、[场景](https://cloud.tencent.com/document/product/1806/121796)：介绍搜索服务，不代表原生内置多轮调查 Agent。
- [服务 API KEY 方式](https://cloud.tencent.com/document/product/1806/130615#51c379b3-3766-4aa5-93fb-b60441fcb2fe)：`POST https://api.wsa.cloud.tencent.com/SearchPro`，Bearer 鉴权，明确出入参参考完整 API 文档。
- [SearchPro 输入/输出参数](https://cloud.tencent.com/document/product/1806/121811)：业务参数、版本限制与 Version 输出。此页的通用腾讯云签名示例不替代 API KEY 网关接入。

本轮通过网页正文阅读代理读取两份官方原文，核对 API KEY 页对完整业务参数的引用；随后用本机既有凭据实际发送 Site。公开文档经代理读取仍归原始发布者，读取方式应如实记录。

## 鉴权与最小请求

请求头为 `Authorization: Bearer <本机密钥>` 与 `Content-Type: application/json`。最小业务体为 `{"Query":"查询词"}`。不要把 KeyName 当请求参数，也不要混用 SecretId/SecretKey 签名流程。

客户端从 `TENCENTCLOUD_WSA_APIKEY` 读取；没有时读 `~/.config/yuanbao-search/.env` 的同名配置。配置文件建议权限 0600。沿用本机元宝凭据，不把项目 .env 或密钥复制进技能、查询、命令行、报告或分发包。

首次使用没有配置时，按[首次接入与排错](onboarding.md)友好引导。`scripts/connect.py status`只检查脱敏本机状态；`configure`在用户自己的终端隐藏输入保存，`test`实际调用一次服务。已有可用MCP或本机配置时复用，不重复索要Key；找到配置不等于连通成功，保存成功也不等于搜索已执行。

## 已接入的业务参数

自带 `scripts/yuanbao_search.py` 与 `evidence.py search` 只接受下表的白名单业务参数。实际发出的 JSON 保存为 `request_parameters`，服务实际返回的 `Version` 保存为 `version`；缺失时保持未知，不猜测套餐。import 的旧 MCP 收据没有该字段时不能补写成已用高阶参数。

| API 参数 | CLI | 官方约束与使用边界 |
|---|---|---|
| `Site` | `--site example.org` | 每次指定一个域名，或 `exclude:a.org\|b.org` 排除至多五个域名；只对自然网页生效，不限制 VR 卡。CLI 中含 `\|` 的实参应整体引号包裹，不写反斜杠。 |
| `Freshness` | `--freshness m3` | 最近 d1–d30、m1–m12、y1–y5；d/m/y 单字符按文档默认为1。也接入有效单日 yyyy-mm-dd。官方区间描述与示例方向不一致，日期区间暂不接入；不自行猜测。 |
| `Mode` | `--mode 0/1/2` | 公开网页/优质权威垂直信源/混合。极速版不支持；不是深度调查模式。 |
| `Industry` | `--industry acad` | gov/news/acad/finance，极速版不支持；技能仍只调查允许范围内的公开非个人资料。 |
| `Cnt` | `--cnt 20` | 10/20/30/40/50，极速版不支持；增加服务端条数，单独使用不满足高阶语义参数门槛。 |
| `Deeplinks` | `--deeplinks` | 仅旗舰版，返回附件子链；单条结果至多十条。子链不是已读附件。 |

本地 `--limit` 只截取展示列表，不改变服务端条数与请求次数。代码接入及离线测试不代表每项都在当前账号现场验证过。

### 现场验证范围

2026-10-25，本次技能维护发起2次真实 SearchPro 请求。第二次以第一次混杂转载的发现为依据，实际发送 `Site=cloud.tencent.com`；服务返回 `Version=standard`，本地保留的5条结果均属于该域名。不同 RequestId、依赖、请求参数和结果保存于本次调查目录。这说明该次 API KEY 请求接受了 Site，不能据此概括所有查询的过滤效果。

本次没有现场调用 Freshness、Mode、Industry、Cnt 或 Deeplinks；它们只有官方契约与代码/离线参数测试，后续使用时仍须核对版本和结果。没有购买、升级或替换凭据。若当前元宝 MCP 契约只有 query/limit，使用自带 CLI 发送高阶参数，而非将参数塞进查询词。

## 搜索结果与失败

`Pages` 通常是 JSON 字符串数组，可能有 title/url/passage/content/date 等字段，不能假定齐全。passage/content 都是摘要，必须另读原文；score 是相关性，RequestId 是追踪，均非真实性证明。空结果保留 Msg，不能判定目标不存在。

客户端不自动重试。真实请求前校验参数，参数无效不计 API 尝试；请求开始即记录 pending，失败也计入预算，保留安全错误类型与实际业务参数。认证/权限/配额错误停止相同重试；临时网络故障最多再试一次。服务返回参数错误可记录高阶参数受限，降级为多轮原文调查；其他故障不能冒称参数不支持，也不能跳过基础搜索门槛。

`direct/multi/deep` 是 Agent 的编排记录，不是接口 Mode，也不是腾讯云提供的三个调查档次。功能声明分别说明实际发送了什么、返回了什么、哪些仍未知。每次请求可能消耗免费次数或按量费用；默认每轮最多18次尝试，用户预算优先，不自动购买或循环调用。
