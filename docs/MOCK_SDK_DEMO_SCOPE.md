# MOCK_SDK 三文件 Demo：本轮范围与运行说明

本文件说明当前业务项目实现，不是 SDK 公共接口或两轮验收通过记录。本轮 **PARTIAL**；真实 AI／两轮 **NOT_RUN**，真实 SDK **NOT_RUN**，W0 **INCOMPLETE**。已接受的 F1 限定修复和封存证据保持原样。

## 模式与当前启动

最新指令仅允许模拟三份指定虚构样例的原始读取输出。暂停 SDK 契约核对、打包、安装和接入；本轮不读写、导入 SDK，也不改变其环境或测试。

内部接口仍为 reader.read(path, document) → ReadResult。显式 reader_mode=MOCK_SDK 装配 MockSDKReader，ai_mode=OPENAI_API 单独报告。ResponsesExtractor 只有明确授权后才装配；网页 Mock 模式不接受假 AI transport 或全局机制测试开关。默认 REAL_SDK 未连接，也不会回退 Mock。

在项目根目录执行：

~~~sh
.venv/bin/python tools/start_demo.py --reader-mode MOCK_SDK
~~~

地址：http://127.0.0.1:8765 。沿用现有网页、PostgreSQL、业务表、任务和上传卷，数据库不发布主机端口，不清空旧数据。缺少 AI 授权时仍可上传原件、核对 fixture 和预览实际原件，任务随后在 AI 前明确 BLOCKED，不生成虚构费用／学习。停止命令为 .venv/bin/python tools/start_demo.py stop，数据保留。

## 仅缺四项 AI 条件

1. 明确允许向 OpenAI 发送三份虚构样例所必需的提取内容。
2. 用户批准的实际可用模型。
3. 所有进程／重启累计请求上限，包括失败尝试。
4. 指定后端凭据来源：环境变量名或本机文件／安全来源，不提供 Key 值。

条件齐全后可传入非秘密授权 JSON：

~~~sh
.venv/bin/python tools/start_demo.py --reader-mode MOCK_SDK --ai-authorization /用户指定绝对路径/ai-authorization.json
~~~

路径是语法占位，不是已存在或批准读取的文件。实际接受恰好四个顶层键：

| 键 | 实际契约 |
| --- | --- |
| allow_sample_content_to_openai | 仅用户明确批准必要样例内容外发后才可为 true |
| model | 用户批准的实际可用模型名，程序不代选或自动切换 |
| max_requests | 用户批准的正整数累计上限，最多2147483647 |
| credential_source | 指定环境变量或文件来源，不能包含 Key 值 |

以下是**不能通过授权校验的填写模板**，不是授权或可运行配置：

~~~json
{
  "allow_sample_content_to_openai": false,
  "model": "",
  "max_requests": null,
  "credential_source": {"kind": "environment", "name": ""}
}
~~~

credential_source 二选一：

- {"kind":"environment","name":"用户指定的环境变量名"}：从启动后端的进程环境向 Docker 传递指定变量。日志只含变量名；值存在于本机 Docker 容器元数据中，不得将完整 Docker inspect／Config.Env 输出到证据。
- {"kind":"file","path":"/用户指定绝对路径/凭据文件"}：仅将指定文件只读挂载至后端 /run/secrets/sanzo_openai_key，再有界读取。文件应允许当前后端用户读取；程序不修改主机权限、不搜索其他来源、不解析真实 .env。

凭据不进入源码、浏览器、截图、日志或 Review Bundle。来源错误会明确失败，配置存在不等于已获授权或调用成功。启动器实际传入 SANZO_READER_MODE、SANZO_AI_AUTHORIZATION_JSON；原 SANZO_DATABASE_DSN／SANZO_UPLOAD_DIR 保持内部用途。

## 跨进程／重启累计预算

configuration.AUTHORIZATION_ID 固定为 sanzo-three-file-mock-sdk-v1，启动不产生新授权编号。原 PostgreSQL 保存模型、许可样例 SHA、上限及消耗记录；同编号配置不同会拒绝。没有清零、退款、自动扩额或替代授权接口。

提取、分类、失败尝试和所有进程／重启共用额度。每次准备调用前先提交消耗；为防止崩溃或提交结果不确定绕过上限，凭据失败／中断也不退回已消耗额度。budget_allowance_consumed 只表示消耗额度；实际请求尝试、HTTP 状态、请求／响应 ID、模型和返回用量分别记录，不等同 API 成功或费用。

无自动重试、重定向跟随、模型回退和额外 OCR。已确认名称可免该明细的分类调用；文档提取仍可能调用 AI，不能宣传整份文件零 AI。

## fixture、原文和 F1

demo_materials/fixtures/manifest.json 将三份原文件 SHA256 绑定原文 JSON 及其 SHA256。fixture 只有经核对的完整原始字段、本次金额／币种／单位／条件和来源位置，不含人工最终费目、fee_id、AI 回答、学习命中或目标比较表。上传的 document_id 在读取时绑定；不能凭文件名或近似哈希挑选数据。原件或 fixture 改变即拒绝。

Excel 保留完整单元格值、数字格式、Sheet／Cell、同明细和共享字段有限归属。PDF 保留完整字段／数字 token、原始组合字段和明细归属；只核对页码，不制造坐标或字段高亮。预览来自实际上传文件本体。

B 社 PDF 没有可核验的 transport_mode，条件保持缺失，因此该明细 REVIEW_REQUIRED，不进入名称分类 AI 或可复用学习，不补造 N/A。提取出的当前金额和原文字段仍可保存展示，缺口必须保留。

F1 完整字段、完整金额及同明细校验保留，未知条件不作任意匹配。fixture_integrity=VERIFIED 仅表示 fixture 字节匹配清单；Mock 字段验证仅表示 AI 与模拟原文一致，均不代表真实 SDK 核验。页面和 Excel 显示「デモ用解析データ／SDK未接続」。只有实际成功 API 结果才能显示「AI処理済」。

## 保存与来源隔离

仍用原有 PostgreSQL 事务同时保存当前修改、人工学习和历史，保留版本、幂等、冲突、撤销、重新确认和失败回滚。学习的是原始费目表记 → 人工最终 fee_id；金额-only 不学名称，当前价格／币种／来源每次从当前输入取得，新规则不修改旧案。

Mock 网页默认租户为 local-demo-mock-sdk，区别于既有 local-demo；学习键还带独立 Mock 来源范围，即使同租户也不能自动应用到 REAL_SDK。学习 slot、版本、事件和修正事件记录 origin=MOCK_SDK。历史行仅新增诚实的 LEGACY 默认来源字段，不重写原始字段、旧键或既有结论。

Excel 保留当前价格、人工名称／未确认提案、来源、fixture 版／SHA、学习来源和未处理事项。它是当前表快照，不是客户导入格式已验证的证明。

## 两轮及当前证据

第一轮预定：新案件上传 Excel①＋PDF②，Mock 原文进入真实 AI；预览原件，在吹き出し实际确认／修改 A 社「配送手数料」为「配送費」，保存该次8,000円及人工 fee_id／记忆版本。不故意制造 AI 错误。

第二轮预定：另建案件只上传 Excel③，自动抽取本次9,500円并实际匹配第一轮记录，显示「配送費／9,500 JPY」，旧案仍8,000円；来源指向新上传文件，下载 Excel 与当前页面核对。不能预写分类、学习命中或人工录入单价替代流程。

当前四项 AI 条件未提供，真实 AI、两轮人工确认／复用、两轮 Excel 一致性均 NOT_RUN。首次完整 Demo 回归在独立可丢弃 PostgreSQL 上173项通过、无跳过；本地机制注入不能作为真实模型证据。本机浏览器边界27项通过：第一轮两份原件上传／预览、fixture、AI 前实际阻塞及空表导出状态均已核对；真实 AI 调用0，仍不计为业务两轮验收。

第三份原始 fixture 准备已获本轮授权。早期一次 fixture 单元检查曾在内存构造该份数据的 Observation，未创建案件、写库、调用 AI 或学习；现已移除该检查，原日志保留，不计为第二轮执行。

历史 SDK／W0、PARTIAL／失败证据和 F1 接受结论保持原样。不扩展登录、用户管理、云部署、利润试算或独立 PoC。最终包按允许清单包含源码、必要虚构 fixture、哈希、起止基线、命令／退出码、失败／复测及未执行说明；排除原始报价文件、密钥、真实 .env、SDK 仓库、缓存、虚拟环境和数据库卷。打包通过不提升业务状态，交付后停止等待 ChatGPT レビュー，不 commit／push。
