> 2026-10-12 SDK 构件更新：当前默认包为 `/Users/kyoen/Projects/aiDemo/document_processing_sdk-1.8.0-py3-none-any.whl`，元数据版本仍为 1.8.0，SHA256 为 `781161e4af3b0b45a1ca8a7745e774e1a2362eb01bcc30956e51619514fcc4e0`。启动、Adapter、Docker 构建和 probe 引用已同步；本次未重建或重启服务，新包运行尚未验证。下列历史验收仍对应当时的旧构件。

# REAL_ADAPTER_INTEGRATION_R1 — 固定 wheel 真实接入

案件 No.202608310011；2026-09-13。真实接入和限定两轮通过，项目整体仍 **PARTIAL**。B社公司及部分适用条件未决；没有把它们伪装成已确认，也没有强制五行横向合并。旧 W0 INCOMPLETE、Mock/PARTIAL 和历史失败证据不改写。

## 一个启动命令

在 `/Users/kyoen/Projects/sanzo-cost-table` 执行：

```sh
.demo-runtime/sdk-business-r1/.venv/bin/python tools/start_real_demo.py
```

浏览器：<http://127.0.0.1:8765>。

脚本复用 `tools/start_demo.py`，只管理该项目 owner 标签的网页/PG容器。使用原数据库和上传卷，不清空、不重建业务数据。后端只从用户指定的 `sanzo_cost_table_framework_v1_1_0/.env` 取 `OPENAI_API_KEY` 赋值到子进程环境；不执行文件中的shell表达式，不打印/打包密钥。非秘密授权仍是 `demo_materials/config/ai-authorization.json`：gpt-5.6-terra、固定授权编号、所有进程累计100次。凭据缺失时本地全记忆命中路径仍可运行；未知项不能凭空生成AI结果。

应用业务环境：独立Python3.12.13，项目 `.demo-runtime/sdk-business-r1/.venv`；网页镜像内 `/opt/sanzo-venv` 同为3.12.13。固定wheel安装 `[pdf,excel]`；PDF依赖按固定SDK要求锁定pypdfium2 5.12.1。原项目 `.venv` 和SDK开发环境未升级。

## 实际接入边界

- 固定构件 `/Users/kyoen/Projects/sdk/document_processing_sdk-1.8.0-py3-none-any.whl`。
- SHA256 `d791e0602ef138f50a065cd4cc5d3dca801226822b74328230f2faa2c0c75666`；保留原文件名，启动前核对wheel与265个已安装SDK文件字节。
- 未访问 `/Users/kyoen/Projects/document-processing-sdk`，未安装/修改其环境、未执行其测试或批量语料。
- `DocumentSdkAdapter.read(path,document)` 是应用接口。调用安装wheel的公开 `inspect_workbook`、`inspect_workbook_semantic_details`、`read_sheet`、`analyze_pdf(ADVANCED_LOCAL)`。
- Excel实际完整表头绑定角色，跨行/跨列移动有真实SDK测试。金额用物理单元格词法原值，不依赖样例地址、文件名、文件SHA或期望价格。公式/合并歧义/未知字段保留未决。
- PDF使用AVAILABLE表格/单元格、完整row/column/span归属，保留整个SDK输出及PARTIAL限制；无公开cell bbox时仅页级预览，不伪造高亮。
- 每次结果同时保存SDK原始序列化JSON及其SHA、解析对象、调用/来源/限制。ReadResult增加兼容的issues；没有可靠字段时允许空blocks+明确issues，不捏造一行。
- 真实模式不会回退Mock；三份SHA只约束允许外发的语义内容，不选择解析答案。原Mock fixture逐字不变。

## 处理顺序和人工补确认

`实际上传原件 → 真实SDK → 完整表头/字段适配 → structure_facts/F1 → strict memory → 仅unknown费用名称AI → 既有行级确认事务`。

没有默认quotation_extraction AI。AI输入不含已命中项和金额事实；分类schema没有amount/currency/source_ref。F1金额、完整字段、同明细归属函数AST与起始基线相同；核心memory规则、comparison projection、人工费用确认事务和AI预算实现未重写。

A社文件的公司文本完整，但SDK没有认定其为报价主体。上传流程因此让操作者从真实SDK完整单元格中分别确认“公司／输送范围”，保留HUMAN角色来源、actor、document/SHA和有限适用明细；不输入价格/费目答案，不建立客户Master。新文件需单独确认角色，不继承旧文件的人工声明。选择空白时保持未知、不学习。

B社公司位于混合页文本，运输模式缺失，scope/terms在同一表格格内；未从页字符串随意截取或拆分。5条报价均保留，名称可以AI提议，但没有可复用B社memory。本次首轮主表实际10行，并未宣称5行配对成功。

本地已学习文件不受AI暂停/额度不足的界面总开关阻挡。显式pause_ai限制进入任务幂等摘要，只在memory miss准备调用AI时保留处理；不重置预算、自动重试或切换模型。非法HUMAN绑定导致失败时仍保存已经完成的真实SDK输出。

## 实际两轮

1. 第一案件 `71e500a7-250d-45b8-a256-8076fde30c7c`：真实上传01 Excel＋02 PDF，各5条。A社公司与范围经原文角色确认；真实AI批量分类2次、HTTP200、模型均gpt-5.6-terra。打开真实Excel定位与PDF页预览。A社5条在现有统一费用弹窗逐条人工确认，价格只读，其中`配送手数料 → 配送費`；创建5条REAL_SDK学习记录，B社0条。
2. 确认没有运行中的任务后仅重启本项目网页；原PG、上传卷及账本保留。
3. 第二案件 `fa1944a1-fe1a-424a-9a21-aab2216eb4c8`：只上传03 Excel，再独立确认SDK公司/范围单元格角色。实际5条全memory命中，整份文件0AI、目标配送费0AI、quotation_extraction0次。配送費来自第一轮真实memory，9500来自新文件SDK物理值；第一轮仍8000。新原件定位及下载Excel与页面一致。

| 证据 | 值 |
|---|---|
| 第一轮配送 item | eafedf6e-de16-488b-aeed-fb37b46551a4 |
| 第二轮配送 item | fddace4d-c495-420d-9b10-97458fddff99 |
| 人工确认 fee_id | 8561aeb3-2f15-4eb9-b8ee-0b0e70a46466 |
| learning_event_id / version | f0f4f989-23c5-4685-9152-7b714d86e8db / 1 |
| 对应人工修正事件 | c5f7aea7-dc78-4739-9a02-d290a22d692b |
| 第二轮金额原文 | 9500，SDK实际单元格 `見積明細!F16`（观察结果，不是解析常量） |
| 记忆来源 | REAL_SDK；company/transport字段角色为HUMAN |
| AI预算 | 39 → 41 / 100；失败/重启共同账本，无新增授权ID |

两次分类调用：`req_9a2d835a78444c47a45e8b42e65f3736`、`req_52ae4b7c3c824a9caf0de231d4134743`。用量合计input1863/output411/total2274 tokens；未取得账单金额，不虚构货币费用。第一轮每份未知5项各一个请求；第二轮未知项为空，0请求。浏览器正式处理公开SDK调用7次，角色预检另外7次，分别记录；测试的SDK执行另计。

## 验证和交付边界

- 最终隔离PostgreSQL回归294项通过、0跳过，含真实固定wheel三文件/移动表头、来源边界、旧Mock隔离、事务/幂等、全学习无AI/无Key/暂停AI、失败SDK证据和导出测试。机制分类器测试明确标注，不当成真实AI证据。
- 原框架46项、独立领域15项、契约8项通过。
- 浏览器第一阶段111项、重启续跑131项检查通过。计数有重复复核，不能加总称242个独立业务场景。截图、真实调用、原文预览和导出检查均附包。
- 初始adapter测试两项小数位期待错误与第一次PG真实来源标记失败保留；修正和复测记录均保留，不改历史日志。
- 当前首轮前置/后置截图、第二轮截图、源码全文/精确patch、基线、五条learning链、2条请求账本、原始SDK JSON与哈希均在本轮Review Bundle。
- SDK整体PDF输出PARTIAL、B社公司/条件未决、首轮10行；客户任意模板/正式导入格式/扫描件OCR/SDK全量语料NOT_RUN。无登录、云部署、生产化、模型/额度扩充、commit/push。
- 旧W0历史状态保持INCOMPLETE。本轮固定wheel公开契约及真实接入按上述实际范围核实，不将它扩展为整个SDK或生产系统验收。
