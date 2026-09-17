> 2026-09-14 SIMPLIFY_CORE_LEARNING_FLOW_R1：限定真实SDK两轮Demo通过。普通上传无公司／运输前置窗口；fee-name-v2按完整原词与可靠同行条件学习，保留旧REAL只读兼容和Mock隔离。首轮A/B为5组，B未知分类1次，重启后第三份5项记忆命中、AI0，新9500／旧8000／Excel一致。累计48/100，不重置额度。启动：`.demo-runtime/sdk-business-r1/.venv/bin/python tools/start_real_demo.py`。结果与边界见 [本轮报告](docs/SIMPLIFY_CORE_LEARNING_FLOW_R1_REPORT.md)。旧阶段状态和证据保持原样。

> 2026-09-13 REAL_ADAPTER_INTEGRATION_R1：固定 wheel 1.8.0 已接入现有网页，真实 SDK／AI 首轮和重启后第二轮通过。AI 请求首轮2次、第二轮0次，累计41/100；配送費复用、当次9500和旧8000已核对。整体仍PARTIAL：B社公司及部分条件待确认。启动命令与限定结果见 [真实SDK接入R1](docs/REAL_ADAPTER_INTEGRATION_R1.md)。以下各阶段原状态作为历史记录保留。

> 2026-09-12：默认管线已改为 **SDK/Mock确定性事实 → memory优先 → 仅未知名称AI分类**。两轮提取AI为0，分类分别1/0次，目标配送費9500复用通过；保留旧DOC FEE冲突，整体仍PARTIAL。最新运行说明见 [按需AI处理链](docs/MEMORY_FIRST.md)。以下旧阶段状态与证据保留。

> 確認パネルの最新UI・起動・検証範囲は [コンパクトな統一費目確認パネル](docs/COMPACT_FEE_PANEL.md) を参照してください。

> 現行UIの操作・保存契約は [行単位の統一費目確認](docs/ROW_CONFIRMATION_DEMO.md) を参照してください。以下の接続不足・試験数は過去段階の記録です。現在の実行結果は各段階Review Bundleに保存しています。

# SANZO 原価単価一覧表 — 本机三文件 Demo

> **本轮最新状态（2026-09-11）：PARTIAL。** 当前支持显式 MOCK_SDK 原文 fixture 与真实 Responses API 客户端授权装配；AI 外发许可、模型、累计上限和本机凭据来源仍未提供，所以真实 AI／业务两轮均 NOT_RUN。真实 SDK NOT_RUN，W0 INCOMPLETE，F1 已接受结论保留。本轮未读写、安装或导入 SDK。

在项目根目录启动当前 Mock 模式：

~~~sh
.venv/bin/python tools/start_demo.py --reader-mode MOCK_SDK
~~~

浏览器：http://127.0.0.1:8765 。用户仍上传三份原文件，预览来自上传本体。Mock 仅替代原文读取；缺少 AI 授权时任务在 fixture 核对后明确 BLOCKED，不生成费用或学习答案。页面／Excel 保留「デモ用解析データ／SDK未接続」。

现有 PostgreSQL、纠错事务、学习规则、F1 完整字段／金额／同明细校验和导出继续复用。Mock 学习记录显式保存 origin=MOCK_SDK，并与真实来源隔离；累计调用预算跨进程／重启保存，不自动扩额。B 社 PDF 缺少可核验 transport_mode，保留条件待确认，不补造 N/A 或分类／学习结果。

本轮独立 PostgreSQL 首次完整 Demo 回归 **173项通过、无跳过**；本机浏览器边界 **27项通过**，检查第一轮两份原件上传、预览、fixture、实际 AI 前阻塞及空表导出的真实状态。真实 AI 调用 **0**，业务两轮仍 **NOT_RUN**；这些检查不是两轮验收。

只需补齐上述四项 AI 条件，不再索取 SDK 路径；不要在聊天或证据提供 Key 值。实际支持的非秘密授权 JSON、可选 --ai-authorization 启动参数、凭据来源与预算限制，见 [本轮范围与运行说明](docs/MOCK_SDK_DEMO_SCOPE.md)。历史证据不改写，不 commit／push。

---

## 历史说明（原文保留）

以下是此前真实 SDK 未接通阶段的说明，作为历史记录保留；其中模式、启动和接入条件以本页上方及本轮范围文档为准。


项目：/Users/kyoen/Projects/sanzo-cost-table，案件 No.202608310011。
实装框架 v1.1.0 原文件和既有 PostgreSQL 纠错／学习核心保留。

当前阶段 **PARTIAL**。三份虚构样例已到位并校验哈希；真实 SDK 绑定和真实模型调用尚未接通。
W0 SDK 契约核对仍为 INCOMPLETE，历史 PARTIAL／失败证据保持原样。

## 启动

在项目根目录执行：

~~~sh
.venv/bin/python tools/start_demo.py
~~~

浏览器：http://127.0.0.1:8765 。

PDF 预览所需 fonts-noto-cjk 固定为 1:20240730+repack1-1，仅安装在应用 Docker 镜像。
使用现有项目独立环境及 Docker；仅管理带本项目 owner 标签的容器。
PostgreSQL 无主机端口，数据和上传原件保存在原有 named volume；重启不清空案件或学习记录。
网页仅绑定 127.0.0.1。启动不读取 .env 或 API Key，不预解析 demo_inputs，不扫描 SDK。
停止命令：.venv/bin/python tools/start_demo.py stop，保留数据。

## 当前实际实现

- 文件上传、原件哈希校验、持久处理任务、状态轮询与明确错误。
  /process 现在返回 202 和实际任务 ID；缺接入时记录 BLOCKED 事件及零调用，已不再固定返回 409。
- SDK 输出的应用内部契约与来源验证、AI 结构化提取、精确确认记录优先复用、未知项单独分类，以及原仓储的原子入库。
  SDKReaderPort 是业务内部接口，不能当成 SDK 公共 API；当前没有真实 SDK adapter。
- OpenAI Responses 客户端使用指定模型、严格结构化输出、样例哈希许可清单和显式调用次数上限。
  禁止自动重试、跳转和切换模型。机制测试不代表真实 OpenAI 调用；当前启动工厂未装配该客户端。
- AI 提案始终未确认。名称学习仍为“SDK 原始表记 → 人工最终 fee_id”；单改金额不学习名称。
  新案件只复用适用范围完整且无冲突的确认记录，价格始终使用本次文件。新学习不改写旧案。
- 同屏 PDF 页预览和 Excel Sheet／Cell 预览，支持有来源时定位；无来源明确标识未取得，不生成坐标。
- 草稿 Excel 保留本次精确金额、确认名称／未确认提案、原件与来源和未处理事项；未验证客户导入格式。

人工录入默认关闭，只有显式 SANZO_DEBUG_MANUAL=1 的内部调试应用可用，不能用于正式两轮验收。
旧 tools/rehearse_demo.py 是历史合成人工演练工具，本轮没有用它替代真实验收。

## 演示步骤（日语）

1. 「新しい案件」で案件を作成し、01 Excel と 02 PDF をアップロードします。
2. 「読み取りを開始」を選び、処理状態と原文を確認します。接続未完了の場合は不足条件が表示され、明細を捏造しません。
3. 実接続後は A 社の原文「配送手数料」を開き、費目を「配送費」として確認・保存します。AI が同名でも誤りを演出しません。
4. 別案件を作り、03 Excel だけをアップロードします。保存した対応の実一致、本次9,500円、旧案件8,000円を確認します。
5. 「過去の修正を適用」の記録 ID／版を確認し、一覧 Excel をダウンロードします。

手順3以降は実 SDK／AI 接続条件が未提供のため未実行です。
三份样例位于 demo_inputs/；只在用户上传相应轮次后处理，第三份未预解析。

## 测试与接入条件

~~~sh
.venv/bin/python tools/validate_local.py --evidence /private/tmp/sanzo-core-checks
.venv/bin/python tools/start_demo.py test --evidence-dir /private/tmp/sanzo-demo-checks
.venv/bin/python tools/verify_demo_boundary.py --evidence-dir /private/tmp/sanzo-browser-checks
~~~

前两项分别验证参考／领域契约、独立可丢弃 PostgreSQL 上的业务与接入机制。
第三项仅在缺接入的当前版本检查上传／原件预览／真实 BLOCKED 状态，不是两轮 SDK／AI 验收。
新机制测试显式 MECHANISM_TEST；真实 SDK／AI 检查单列 NOT_RUN。

还需要用户提供：
1. 真实 SDK 绝对路径及有限只读核对／三份样例调用授权，确认可用的固定构件与公开契约。
2. 仅向 OpenAI 提交三份虚构样例必要内容的明确授权、模型名和小额调用上限。
3. 本项目本机凭据的安全配置方式／变量名；不要在聊天或证据中提供 Key 值。

填写变量不等于完成接入。核对实际 SDK 后才能实现绑定、资源释放与实际来源转换，并执行真实两轮彩排。
细节见 docs/REAL_INTEGRATION_SCOPE.md。不 commit／push，不扩大生产化范围；交付后等待 ChatGPT レビュー。
