# SDK事实优先，OpenAI仅分类未知名称

本轮处理链：上传原件 → `reader.read(path, document)` → `structure_facts` → 原F1完整字段/金额/同明细校验 → 严格memory查询 → 仅未知名称批量分类 → 现有原子入库。当前reader为MOCK_SDK，真实SDK未执行，W0仍INCOMPLETE。

旧链路在每份文件读取后无条件调用`quotation_extraction`，由AI引用原文生成事实，再查询memory及分类。该调用已从`IntegrationRuntime.run`删除；现有Responses客户端的旧提取接口及F1反例仅保留兼容回归，默认业务管线不使用。金额、币种、raw label、原件位置不进入分类输出schema。

`facts.py`依赖应用的ReadResult和reader提供的完整字段/明细边界，不依赖样例文件名、金额答案、fee ID或SDK公共API。完整单元格/字段直接映射，共享字段必须有明确适用记录；缺少关键事实、字段边界或明细归属时明确未解析。未知条件保留，不补造N/A。原始字段文本及来源保留；合法逗号及币种前后缀继续使用原F1确定性转换。

每个新任务记录零quotation_extraction、memory查询结果、原因、版本及事件ID、分类请求/跳过的item ID。分类请求只有未命中且允许语义建议的item_id、raw_label、context，必要时带同案可核验名称候选；不发价格/币种/位置。冲突、撤销不会交给AI仲裁。分类响应仅item_id和suggested_fee_name，额外价格字段被strict JSON schema及应用校验拒绝。

新明细的fact_sources明确为MOCK_SDK（机制测试为MECHANISM_TEST），classification_source为MEMORY/AI/HUMAN/UNRESOLVED。F1照合通过不宣称真实SDK已验证。旧案件不重新分类。人工名称确认、memory资格、事务、价格、历史及Excel projection保持原实现。

全部命中时无需AI客户端、Key或可用额度；未知名称缺授权时仍明确BLOCKED。真实调用沿用已批准模型gpt-5.6-terra、固定授权编号sanzo-three-file-mock-sdk-v1、所有进程累计100次，不重置、不自动重试、不换模型。

## 本机启动

后端终端已具备用户授权的OPENAI_API_KEY时，在项目根目录执行：

```sh
.venv/bin/python tools/start_demo.py start --reader-mode MOCK_SDK --ai-authorization demo_materials/config/ai-authorization.json
```

地址：http://127.0.0.1:8765 。本轮已按用户授权从指定项目.env安全传递Key到后端，未记录值，未更改.env。现有启动工具本身不自动读取.env。

## 验证

```sh
.venv/bin/python -B tools/start_demo.py test --reader-mode MOCK_SDK --evidence-dir /private/tmp/sanzo-memory-first-test-new
.venv/bin/python -B tools/rehearse_unified_fee.py --execute-approved-ai --evidence /private/tmp/sanzo-memory-first-browser-new
```

第一项使用独立真实PostgreSQL及离线HTTP替身，不消耗真实额度。第二项明确消耗既有批准额度，不重复处理失败文件；断言/演示问题保留原证据。`--resume-first-evidence`只恢复已完成首轮上传，核对原件哈希，不重发已处理文件。

本轮：245项回归通过，无跳过。两轮实际上传、预览、人工保存、memory复用及Excel一致性通过。第一轮1次分类请求（B社5项），第二轮0次；两轮提取AI均0次。第二轮配送費9500来自新Mock事实与首轮确认memory v5；首轮仍8000。账本38→39/100。

保留缺口：已有A社DOC FEE的memory为CONFLICTED，未交AI覆盖、未替客户重新确认；因此页面保留6行，原“五行配对”探针NOT_MET。B社缺transport_mode继续待确认且不学习。按需调用与目标两轮路径通过，整体Demo仍PARTIAL；真实SDK NOT_RUN。详细失败、恢复和字段级证据见本轮Review Bundle。

Structured Outputs的strict schema及additionalProperties边界按[OpenAI官方文档](https://developers.openai.com/api/docs/guides/structured-outputs)核对（2026-09-12），实际模型可用性由本轮HTTP200记录证实。
