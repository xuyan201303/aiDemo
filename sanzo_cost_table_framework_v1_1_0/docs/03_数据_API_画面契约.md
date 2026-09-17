# 03｜数据、API和画面契约草案

这些是本项目自有契约，不是已实现的HTTP接口，不是document_sdk公共API。精确的SDK映射须先完成W0。

## 1. 分开存三类核心数据

| 数据 | 示例 | 不变规则 |
|---|---|---|
| 原始证据 | 原PDF/Excel/邮件、SHA、页/单元格/source block | 原件不改，来源只能真实关联 |
| 本次报价 | 本次单价20、币种、费用条件、人工修正 | 新文件重新读，旧金额不套用 |
| 识别知识 | 送货钱→配送费，适用范围和确认事件 | 不放旧价格；可撤销、可追溯 |

## 2. 正式数据库的逻辑实体

| 实体 | 主要字段／关系 | 关键约束 |
|---|---|---|
| workspaces / users / memberships | tenant、用户ID、identity subject、角色、状态 | Actor由认证导出；用户停用使后续请求失效 |
| cases | tenant、case_id、名称、case_revision、状态、创建/更新时间 | 每次影响结果的变更推进版本 |
| documents | tenant、document_id、object_key、sha256、原文件名、MIME、size、case_id | 版本只追加；附件不可绕过案件授权 |
| quote_offers / offer_documents | forwarder_id、方案ID、币种/有效条件；多对多附件关系 | 不等同文件；未知公司显式标记 |
| ingestion_runs / jobs | case、输入版本、配置、各阶段状态、租约、attempt、fencing_token | 再处理不覆盖人工版本；失败不能从统计消失 |
| source_blocks | document_id+revision、block_id、raw_text、source_locator | locator可缺失但不能编造；一字段可对应多个block |
| observed_lines | run、offer、raw_label、raw_value、parsed_decimal、原币种/单位/条件、sources | 原始解析观察不改写；保留异常/缺失状态 |
| reviewed_lines / line_revisions | observed_line或人工追加、fee_id、金额/条件覆盖、item_version、review_status | overlay和历史；人工追加标记依据 |
| fee_concepts | tenant、fee_id、display_name、provisional/confirmed、origin | 系统创建/日常纠错确认，不要求客户维护费用清单 |
| learning_slots / learning_versions | scope_key、上下文签名、norm/policy version、target_fee_id、ACTIVE/CONFLICTED/REVOKED | 历史追加；精确键当前slot单一，不自动回退 |
| learning_evidence | learning_version、correction_event、observed_line、source_ids | 绑定原文与人工选择，不只保存AI答案 |
| correction_commands / audit_events | Actor、幂等key/hash、expected revisions、before/after、结果 | 事务写入；重复请求不重复修改 |
| export_snapshots / export_files | case_revision、数据快照hash、template_version、对象键、生成状态 | 从固定快照生成；旧文件不随新学习更改 |
| ai_call_attempts | run/item、目的、model/prompt/schema版本、token、attempt、结果/拒绝/超时 | 分开EXTRACTION/CLASSIFICATION；默认不记录完整内容 |
| data_policies | 外部AI许可、允许provider、保留/删除、大小/时限/预算配置 | 未授权默认拒绝外传；阈值可版本化配置 |

上表是逻辑结构而非承诺必须拆出相同数量SQL表。迁移、索引、复合外键/租户隔离、备份策略在W1确定并测试。

金额使用Decimal和PostgreSQL numeric，JSON传字符串；不得float计算或默默四舍五入。保留raw_value以处理逗号/点歧义。`0`、`null`、`INCLUDED`、`NOT_QUOTED`、`NOT_APPLICABLE`、`UNKNOWN`有区别；分类正确也不能把金额状态改成KNOWN。

## 3. 来源定位契约

```text
source_ref = {
  document_id, document_revision, document_sha256,
  source_block_ids[],
  kind: PDF_PAGE | EXCEL_RANGE | EMAIL_TEXT,
  locator_status: VERIFIED | PAGE_ONLY | UNAVAILABLE,
  locator:
    PDF  -> page_number(1起), optional bbox+coordinate_system+page_rotation+page_size
    Excel-> exact_sheet_name, A1_range, cell_value_kind, original/cached-value区分
    Email-> canonical_text_revision, character_start/end或block IDs
}
```

PDF坐标不可靠则只开真实页，不画“猜测高亮”；连页码都未确定则让客户查原件。Excel简易预览来自原工作表，不是分类后的单价表；公式缺缓存明确提示，不假计算。Email正文保存后固定规范化版本，偏移量相对这个版本，不相对后来重排的HTML。

source_ids由服务端分配。客户端只请求允许的source_id，不接受任意URL/路径。多文档依据显示可点开的列表，不随机挑一个。

## 4. API路由草案

同源HTTPS；会话/CSRF或受支持的身份组件；所有业务端点先授权。表内路径为计划，不代表当前包有这些端点。

| 方法／路径 | 用途 | 必需的行为 |
|---|---|---|
| POST /api/v1/cases | 创建案件 | actor scope；幂等；返回case_revision |
| GET /api/v1/cases | 案件一覧 | 分页/搜索；仅允许范围 |
| POST /api/v1/cases/{id}/documents | 上传附件或登记正文 | 尺寸/类型校验、原件hash、私有保存；不立即当解析成功 |
| POST /api/v1/cases/{id}/runs | 开始处理 | 输入版本固定、job_id、幂等；新任务不覆盖旧人工确认 |
| GET /api/v1/runs/{id} | 查询进度 | 每文件状态＋成功/失败/排除/待处理，不只百分比 |
| GET /api/v1/cases/{id}/cost-table | 获取当前原价表 | current saved overlay + 未分类/错误；case_revision |
| GET /api/v1/items/{id}/review-context | 打开吹き出し | 原文source、当前字段、候选fees、item/memory版本 |
| POST /api/v1/items/{id}/corrections | 修改并学习 | 明确选择费用；来源键服务端取；事务/409/幂等 |
| POST /api/v1/cases/{id}/manual-lines | 补充漏行 | 手工标记、原件依据、不得静默建立无源别名 |
| POST /api/v1/items/{id}/exclusions | 排除错误/非费用明细 | 理由+历史；原观察不删 |
| GET /api/v1/sources/{id}/preview | 对应位置预览 | 只读、权限、真实定位、原件fallback |
| GET /api/v1/documents/{id}/download | 原件下载 | 先授权再短时签名或同源代理；对象键不裸露 |
| POST /api/v1/cases/{id}/confirmation | 最终确认 | expected_case_revision；无未处置文件/阻断问题 |
| POST /api/v1/cases/{id}/exports | 原价Excel生成 | draft/final、case_revision、template_version、幂等 |
| GET /api/v1/exports/{id} | 生成状态/下载入口 | 固定快照版本，权限检查 |
| GET/POST/PATCH /api/v1/users | 基本用户管理 | 管理员；禁用生效；不能自由更改tenant |

## 5. 纠错请求和响应要点

请求包含：expected_case_revision、expected_item_version、expected_memory_version、Idempotency-Key、修改字段（选择已有fee_id或新名称，二选一）、必要时针对旧学习冲突的明确更新确认。

**不得包含可被信任的：**tenant_id、原文标签、原件路径、source_ready、condition_verified、任意全局学习scope。可提交用户纠正的业务条件，但其来源和适用性由服务端验证后形成新版本。

服务端返回最新行/表版本、fee_id、warning reasons和learning_effect：
- `ACTIVE`：当前已存，后续可在同条件复用。
- `NOT_ELIGIBLE`：当前已存，但条件/来源待确认，后续自动复用暂保留。
- `CONFLICTED`：当前已存，已有矛盾记录，后续复用暂停。
- `UNCHANGED_AMOUNT_ONLY`：只改本次金额，没有建立名称规则。

409版本冲突时不清空用户输入，显示最新值让其核对重提；网络失败时用同Idempotency-Key重试。分类和金额同时改，任一需回滚则整体不成功。学习冲突是明确的保存结果，不伪装成DB异常；DB异常必须回滚。

`contracts/reference_correction.schema.json`仅对应离线核心的较小请求类型，不含上述完整生产API字段。不能拿它直接生成一个不带case鉴权的公网API。

## 6. UI契约

**登录：**依客户认证条件，基线先预留成熟认证Adapter，不自行编密码系统。

**A 案件作成：**案件名；同案件多文件；邮件正文；选择后开始；处理状态；部分失败可以重试/有理由排除。无Master上传步骤。

**B 原価単価一覧：**公司/报价方案为列，费用+条件为行；原币种单价；未分类、Included和缺失状态；点击单元格开启吹き出し；来源位置点开侧边只读预览；现场改费用/条件/单价等；保存即刷新；显示学习是否生效；支持补充和排除，不只修改已有黄色格。

按钮初期为「一覧表を保存」「Excelを作成」「Excelダウンロード」。不默认有“新システム用に加工”。保存编辑和下载原件不修改源文件。

**C 案件一覧：**案件名/担当者/处理状态/待确认数/更新/Excel版本；点开继续。不要用“要確認0”替代全件处理完成。

**D 用户：**管理员追加/停用；一般用户日常工作。学习结果在本客户允许的业务范围内复用，不局限某个登录人。

## 7. 状态和输出

文件：RECEIVED → VALIDATING → READING → EXTRACTING → CLASSIFYING → AVAILABLE；异常是FAILED、REVIEW_REQUIRED，主动排除是EXCLUDED且有理由。详细阶段可以合并显示，但后端不可丢失。

案件分别保留ingestion_status（处理完整性）与review_status（业务确认），最终表不靠一个状态字段猜完整性。发布FINAL Excel要求：选定输入均已明确处置、无阻断缺失/冲突、当前revision已经最终确认；DRAFT可输出但文件名/内部页明确“未確認・草稿”，带问题清单，不能作为取込完成证明。

Excel由程序按明确模板生成，文本列写为字符串，不执行以`= + - @`等开头的原始文本；不嵌外部连接、宏或模型生成公式。保存文本含义同时避免公式注入，需应用端实测。输出保留当前单价和原币种；不做任何未约定的加价/数量/换汇。结构样例不是新系统已接受的规格。
