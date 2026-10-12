# SDK 1.8.0 补充验收 — 2026-10-12

**完整 Demo 回归 PASS；浏览器验收 FAIL（功能操作通过，但提示与资源检查未全通过）。不具备无保留部署结论，停止等待审核。**

## 基准与改动

项目 `/Users/kyoen/Projects/aiDemo`，HEAD `375291053bca0590e195f2a7ab0f97e22d25c1a6`。本轮开始时已有上轮五个生产代码文件、四个测试文件及报告的未提交修改；全部原字节保留，未回退或恢复旧临时修改。

唯一 SDK 来源：`/Users/kyoen/Projects/aiDemo/document_processing_sdk-1.8.0-py3-none-any.whl`；版本 **1.8.0**；SHA-256 **781161e4af3b0b45a1ca8a7745e774e1a2362eb01bcc30956e51619514fcc4e0**。隔离 Python **3.12.13** 的新 venv `/opt/sdk18-acceptance-venv` 验证了全部 **296** 个安装文件与 wheel 一致。

本轮仅新增本报告，并修改 `tests/test_demo_mock_reader.py`：按 manifest 已批准的原件 SHA-256 唯一定位当前样例，替代失效的旧文件名定位。未改 fixture、原件、预期字段或生产代码。第三份实际为 `03_C社_次回見積.xlsx`，内容哈希仍与 fixture 的批准哈希一致；真实 A3 为 `SANZO Demo Forwarder A（架空）`，F16 为 9500。旧 A 文件 F16 为 8000。测试没有复制/改名样例制造 PASS，也没有按 C 文件名推断公司。

外置卷身份：SANZO_TEST / APFS / UUID `719A3322-58E9-476A-B600-1258B5277549` / Samsung SSD 990 PRO；临时、日志、数据库和截图全部位于 `/Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-acceptance-20261012`。浏览器工具另建内部隔离 venv，未修改原有 Python/venv。

## 去重回归

优先运行以下六个模块；随后运行项目全部 **39 个 `test_demo*.py` 模块**。按完整 unittest ID 去重，移除 **11 个重复加载项**，Demo 回归 **481 个唯一测试全部 PASS**。再补测同一 source/memory gate 影响的 `test_domain`、`test_postgres` 两个核心模块 **35 PASS**；跨两次最终运行按 ID 合并，无重复，合计 **41 个模块、516 个唯一测试：516 PASS / 0 FAIL / 0 ERROR / 0 SKIP**。不能将此数字称为 516 个真实 SDK 测试：其中包括真实 SDK、纯业务逻辑、显式 Mock 隔离及离线 AI 机制测试。

| 优先补测模块 | PASS | FAIL | SKIP |
|---|---:|---:|---:|
| test_demo_memory_first | 5 | 0 | 0 |
| test_demo_source_preview_export | 10 | 0 | 0 |
| test_demo_condition_corrections | 12 | 0 | 0 |
| test_demo_row_confirmation | 14 | 0 | 0 |
| test_demo_real_configuration | 4 | 0 | 0 |
| test_demo_f2_current_review | 1 | 0 | 0 |
| 六模块小计（包含在总数中，不额外累加） | 46 | 0 | 0 |

证据：`regression-final/discovery.json` 记录模块及去重，`regression-final/results.json` 逐项记录状态，`regression-final/tests.log` 保存实际断言日志及退出结果；`core/results.json` 和 `core/tests.log` 保存额外 35 个核心检查；合并去重统计见 `combined_results.json`。真实 wheel 三文件测试、来源缺失/冲突、LIMITED_EVIDENCE、人工确认、学习阻止、9500/8000 和输出测试均包含在内。Mock 测试仅验证独立边界，没有替代真实 SDK 读取。

首次完整回归为 **481 run / 4 FAIL / 2 ERROR / 0 SKIP**，保持原日志不覆盖：四个子进程因隔离 venv 缺少参考核心导入路径失败；两个错误来自旧 Mock 测试查找已不存在的 `03_A`。仅在新测试 venv 添加 reference_core 的 `.pth` 并修正上述测试原件定位后，使用新的独立升级数据库重跑。没有调整任何业务断言或冻结规则。

## 实际命令

完整 Docker argv 与退出码分别见 `host_commands.json`、`network_fix_commands.json`、`regression_environment_fix.json`、`followup_commands.json`、`cleanup_commands.json`。所有下列路径均为本轮任务路径，不涉及正式 8765：

```sh
TMPDIR=/Volumes/SANZO_TEST/private/tmp python3 -B /Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-acceptance-20261012/START_ISOLATED.py
# 新容器内创建 venv、安装唯一 wheel，pip check；源码只读挂载。
/opt/sdk18-acceptance-venv/bin/python -B -m pip --isolated install --no-cache-dir --no-index --no-deps --force-reinstall /workspace/document_processing_sdk-1.8.0-py3-none-any.whl
/opt/sdk18-acceptance-venv/bin/python -B -m pip check

# 修正测试环境后完整回归，独立数据库 sanzo_test / 专用空升级数据库。
docker exec -e SANZO_MIGRATION_TEST_DSN=postgresql://sanzo_test@sdk18accept-20261012-pg/confirmation_completion_upgrade_sdk18_final -e SDK18_EVIDENCE=/evidence/regression-final sdk18accept-20261012-runner /opt/sdk18-acceptance-venv/bin/python -B /evidence/regression-final/TEST_RUNNER.py --tests

# 真实 Chrome，现有界面操作；browser-run2.log 保存 stdout/stderr。
TMPDIR=/Volumes/SANZO_TEST/private/tmp .demo-runtime/sdk18-browser-acceptance-venv/bin/python -B /Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-acceptance-20261012/BROWSER.py
TMPDIR=/Volumes/SANZO_TEST/private/tmp .demo-runtime/sdk18-browser-acceptance-venv/bin/python -B /Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-acceptance-20261012/BROWSER_CONSOLE_CONTROL.py

# Python 3.12 核对浏览器真实下载与测试数据库。
docker exec sdk18accept-20261012-runner /opt/sdk18-acceptance-venv/bin/python -B /evidence/VALIDATE_EXPORTS.py
docker exec sdk18accept-20261012-runner /opt/sdk18-acceptance-venv/bin/python -B /evidence/DB_AUDIT.py
TMPDIR=/Volumes/SANZO_TEST/private/tmp python3 -B /Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-acceptance-20261012/core/RUN_ISOLATED.py
git diff --check
```

最终回归、导出验证、数据库审计、diff 检查退出 0。浏览器驱动退出 0 仅表示完成了证据采集，**其 `browser/result.json` 状态为 FAIL**，不能以进程退出码当作验收 PASS。

## 真实浏览器验收

Google Chrome **154.0.8037.98**，真实 headless 浏览器，1440×1000；独立会话访问 `http://127.0.0.1:18818`。SDK 服务使用 Python 3.12 和指定 wheel；服务与 PostgreSQL 处于专属内网。由于 Docker 内网未实际发布端口，首次浏览器连接拒绝；增加仅用于本轮的 localhost TCP 转发后重新验收，原失败日志/截图保留。

使用真实文件选择与上传控件、既有确认/修正/原文照合控件及下载按钮；未通过 API 写入报价或预置提取明细。GET 快照仅用于证据核验。分类器明确为 **OFFLINE_MECHANISM_TEST**，使用既有人工测试名称选择；真实外部 AI 为 **NOT_RUN，实际付费调用 0**。页面案件名和服务状态均注明 REAL SDK / OFFLINE AI，不能把名称匹配结果当作真实模型准确率。

主流程共 **19 项检查，17 PASS、2 FAIL、0 SKIP**；另有独立的 Excel 一致性与数据库审计 PASS。逐项原始结果在 `browser/checks.json`。

| 检查 | 结果 |
|---|---|
| A 上传、真实 SDK 读取、五次 UI 人工名称确认 | PASS；A 产生 5 条真实来源学习记录 |
| B 上传、6×6 / 36 单元格、5 条真实候选 | PASS；原始状态仍为 LIMITED_EVIDENCE |
| B 金额、币种、页级来源与原件预览 | PASS；1ページ / PAGE_ONLY，没有伪造行框 |
| A/B 初始五行比较 | PASS；B 仅为未确认名称候选 |
| B 歧义限制和“今回の確認が必要”提示 | PASS；资料级状态保留 REVIEW_REQUIRED |
| **显式 LIMITED_EVIDENCE 状态提示** | **FAIL：SDK 输出和 source_ref 保留该状态，但文档卡没有显示该字面状态/候选状态说明** |
| B 通过既有 UI 修改名称、记录原文人工照合 | PASS；来源和 SDK 限制保持不变 |
| B 人工确认后禁止学习、再次上传禁止自动复用 | PASS；全部 B 的 memory_version=0，有效 learning_eligible=false；仅 A 的 5 个 memory_slots |
| 当前第三份上传、既有名称复用、AI 分类 0 | PASS；全部五项 MEMORY；9500 来自本次原件，旧 A 8000 不变 |
| 文件名未推断公司身份 | PASS；原件内容独立核对，业务 observation 的未绑定公司仍未知 |
| 页面与导出名称/金额/币种 | PASS；主表逐格匹配比较投影和真实 DOM；明细逐项核对当前报价 |
| **无浏览器控制台/网络错误** | **FAIL：存在 favicon.ico 404；其他记录已按下述差分归因** |

B 单项修正采用了新的测试名称 `Delivery — HUMAN REVIEW TEST`，因此首轮导出与网页均变为 **6 行**：A 原名称与人工改为不同名称的 B 分开。这是本次明确人工修正的结果；修正前真实 A/B 比较为五行，并非重现原来的候选丢失/名称路径关闭问题。第三份继承 A 的已确认 `Delivery` 名称及本次 9500。

截图包括 `03-B-limitations.png`、`04-B-confirm-panel.png`、`05-B-original-page.png`、`06-B-correction.png`、`07-B-source-confirmation.png`、`08-first-export-web.png`、`10-third-export-web.png`、`11-B-repeat-table.png`。浏览器主验收 15 张截图，控制台差分另有 2 张；另保留首次连接失败截图。实际下载为 `08-first-export.xlsx` 和 `10-third-export.xlsx`。

## 错误归因与最小建议

1. **缺少显式候选状态提示（验收 FAIL）**：`renderDocuments` 目前显示 reader issues，却没有显示 SDK table availability。建议仅在现有文档卡/详情中显示真实 `LIMITED_EVIDENCE` 与“候选、需原文确认”的说明，保留全部限制。无需新增按钮、页面或学习规则。本轮未自行修改生产 UI。
2. **favicon.ico 404（资源检查 FAIL，非报价逻辑错误）**：真实 Chrome 在未启用 tracing 时仍复现，日志定位到 `/favicon.ico`。建议复用现有图标资源或明确提供 favicon；本轮未添加新界面资源。
3. **沙箱脚本拒绝记录（测试 instrumentation）**：同一 B 预览做 tracing=false/true 差分；false 不出现沙箱脚本记录，true 出现。预览页实际 script_tags=0，原件图像正常展示。因此证据支持归因为 tracing 注入与既有 iframe `sandbox="allow-same-origin"` 的冲突，不能通过增加 allow-scripts 消除。原始控制台记录保留。
4. **两个 export.xlsx net::ERR_ABORTED（下载导航）**：均对应 expect_download 成功，下载文件已保存并由 Python 3.12 逐格核验 PASS；属于浏览器将导航移交下载，不是导出失败。
5. 无未捕获页面 JavaScript 异常、无业务 API 4xx/5xx、无人工操作保存错误。上述 favicon 404 是独立浏览器资源请求，保留在服务日志中。

## 边界与审核结论

临时代理、SDK 服务、PostgreSQL 容器及专属网络已按标签全部清理。原有 8765 服务的 ID、启动时间和重启计数前后一致。未接入正式数据库、未处理正式业务数据、未修改 SDK 仓库或 wheel、未加载真实 AI 凭据、未 commit/push，未部署。

剩余 BLOCKED：B 的候选结构与合并条件列仍不足以支持自动学习/自动绑定独立范围和追加条件；资料级未确认仍正确保留。真实 AI 分类质量 NOT_RUN，不能承诺与离线测试相同的名称对齐。

**当前可以审核 SDK/业务安全回归，但不能宣布浏览器要求全部通过或直接部署。** 需要审核上述最小 UI 状态提示与 favicon 方案；若批准修改，修改后重跑针对性浏览器检查。不得为消除 B 的限制更改冻结规则、删除 SDK 安全判定或升级候选可信度。

审核包提供起始差异、本轮增量差异、完整差异、最终代码、命令/退出码、去重测试状态、截图、下载验证、原始 SDK 输出与失败记录。排除原件、wheel、数据库数据、凭据和环境。停止等待 ChatGPT 审核与用户授权。
