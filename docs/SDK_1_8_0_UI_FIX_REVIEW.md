# SDK 1.8.0 最小 UI 修复审核 — 2026-10-12

**结果：PASS。最终回归 517 PASS / 0 FAIL / 0 ERROR / 0 SKIP；原 19 项真实 Chrome 验收全部 PASS，额外 6 项 UI 检查全部 PASS。未部署，等待审核。**

## 基准与修改范围

以当前工作树及 HEAD `375291053bca0590e195f2a7ab0f97e22d25c1a6` 为基准，保留前两轮全部适配、测试和报告原字节。本轮只修改：

- `backend/sanzo_demo/static/app.js`：现有文件卡读取最新任务的真实 `read_evidence`，包括结果及已保留的读取事件；仅当 origin=REAL_SDK 且稳定 `analyze_pdf` 返回至少一个 LIMITED_EVIDENCE 表格时，显示「表の構造は推定結果です。原文との確認が必要です。」。没有按 B 社文件名、金额或样例身份判断。
- 同一文件将当前未解问题的技术代码移入现有「詳細を見る」，不再直接堆在文件卡正文。原有任务警告继续保留；详情增加真实 SDK 表状态、原件 SHA-256、页号和表 ID。没有重写 SDK 验证逻辑或修改可信度。
- `backend/sanzo_demo/static/favicon.svg`：复用现有 `.brand-mark` 的绿色 `#365b4a`、36×36 圆角、白色 S、字号与字重。原侧栏、布局、样式不变。
- `backend/sanzo_demo/app.py`：`/favicon.ico` 返回该 SVG，明确 media type `image/svg+xml`。
- `backend/sanzo_demo/static/index.html`：声明 favicon 类型和地址。
- `tests/test_demo_page_routes.py`：新增真实 HTTP 校验，确认 favicon 200、类型、与静态资源字节相同及页面引用。
- 本报告。

没有新增页面、按钮或确认操作，没有修改 SDK/wheel、Adapter、学习资格、人工确认、金额归属、原価単価一覧表及资料完整性规则。`preservation.json` 和起始文件哈希证明既有适配保持不变；审核包分别提供起始 diff、本轮增量 diff 和完整工作树 diff。

## SDK 与隔离环境

唯一 wheel：`/Users/kyoen/Projects/aiDemo/document_processing_sdk-1.8.0-py3-none-any.whl`。

版本 **1.8.0**；SHA-256 **781161e4af3b0b45a1ca8a7745e774e1a2362eb01bcc30956e51619514fcc4e0**。测试前后哈希一致。Python **3.12.13**，新建隔离容器 venv `/opt/sdk18-uifix-venv`；全部 **296** 个 SDK 安装文件与指定 wheel 原字节匹配，pip check 通过。未修改系统 Python 或已有 venv。

独立 PostgreSQL、专属内网、无正式库连接；独立浏览器数据库 `sdk18_browser_final` 和专用空升级数据库。最终 Chrome 入口为 **127.0.0.1:18820**；首次 UI 检查使用 18819 和另一测试数据库，成功日志保留。原有 8765 服务 ID、启动时间及重启计数前后完全一致。

数据、日志、截图、下载、测试数据库及临时文件都使用 `/Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-ui-fix-20261012`。已核验 SANZO_TEST APFS 卷 UUID `719A3322-58E9-476A-B600-1258B5277549` 和外置 Samsung SSD 990 PRO 设备。程序和环境仍在内部盘/隔离容器程序路径。

## 实际命令与回归

完整 argv、退出码、stdout/stderr 由 `host_commands.json`、`js_syntax_final.json`、`final_browser_start.json`、`final_regression_start.json`、`final_verification_commands.json`、`execution_commands.json` 和 `cleanup_commands.json` 保存。关键命令：

```sh
TMPDIR=/Volumes/SANZO_TEST/private/tmp python3 -B /Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-ui-fix-20261012/START_ISOLATED.py
TMPDIR=/Volumes/SANZO_TEST/private/tmp python3 -B /Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-ui-fix-20261012/RESUME_ISOLATED.py

# 新隔离 venv 内安装指定真实 SDK
/opt/sdk18-uifix-venv/bin/python -B -m pip --isolated install --no-cache-dir --no-index --no-deps --force-reinstall /workspace/document_processing_sdk-1.8.0-py3-none-any.whl
/opt/sdk18-uifix-venv/bin/python -B -m pip check

# 最终完整回归，41 个模块，按 unittest ID 去重
docker exec -e SANZO_MIGRATION_TEST_DSN=postgresql://sanzo_test@sdk18uifix-20261012-pg/confirmation_completion_upgrade_sdk18_final -e SDK18_EVIDENCE=/evidence/regression-final sdk18uifix-20261012-runner /opt/sdk18-uifix-venv/bin/python -B /evidence/regression-final/TEST_RUNNER.py --tests

# 真实 Chrome 主验收和独立 tracing 差分
TMPDIR=/Volumes/SANZO_TEST/private/tmp .demo-runtime/sdk18-browser-acceptance-venv/bin/python -B /Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-ui-fix-20261012/BROWSER.py
TMPDIR=/Volumes/SANZO_TEST/private/tmp .demo-runtime/sdk18-browser-acceptance-venv/bin/python -B /Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-ui-fix-20261012/BROWSER_CONSOLE_CONTROL.py

# Python 3.12 检查实际下载和最终测试数据库
docker exec -e SANZO_BROWSER_DSN=postgresql://sanzo_test@sdk18uifix-20261012-pg/sdk18_browser_final sdk18uifix-20261012-runner /opt/sdk18-uifix-venv/bin/python -B /evidence/VALIDATE_EXPORTS.py
docker exec -e SANZO_BROWSER_DSN=postgresql://sanzo_test@sdk18uifix-20261012-pg/sdk18_browser_final sdk18uifix-20261012-runner /opt/sdk18-uifix-venv/bin/python -B /evidence/DB_AUDIT_FINAL.py
git diff --check
```

最终回归、浏览器、导出验证、数据库审计、Node 语法检查和 diff 检查均退出 0。测试发现共 41 个模块：39 个 Demo 模块加 test_domain/test_postgres；按完整 ID 去除 11 个重复加载项，**517 个唯一测试全部 PASS，SKIP=0**。包含上轮指定六模块、真实 SDK 三文件、来源缺失/冲突、状态门禁、人工确认、学习、比较及导出。不能把全部 517 项称为真实 SDK 测试：其中包括真实 wheel 执行、纯业务单元、显式 Mock 边界及离线 AI 机制检查。

首次隔离服务启动遇到 PostgreSQL 初始化临时进程退出竞态：createdb 返回 “database system is shutting down”。该退出 1 和日志原样保留；等待最终 TCP 服务就绪后仅续建测试数据库。未改变应用代码或测试断言来掩盖该启动问题。初次 517 项回归和浏览器检查也成功保留；最终代码进一步补显 SDK 来源信息后，用全新浏览器数据库再次验收，并完整重跑回归。

## Chrome 原 19 项与新增检查

真实 **Google Chrome 154.0.8037.98，headless**。原 19 项全部 PASS，未删除断言；新的候选提示检查同时验证用户要求的日文、真实 LIMITED_EVIDENCE、实际 SDK 原件哈希及表 ID。额外 6 项检查单独统计，不充入原 19 项分母。

| 验收 | 最终结果 |
|---|---|
| A 实际上传、SDK 读取、UI 人工名称确认及学习 | PASS；独立库仅存在 A 的 5 条学习记录 |
| B 真实 SDK 6×6 / 36 单元格、5 条候选 | PASS；LIMITED_EVIDENCE 没有升级 |
| 文件卡日文提示、详情警告及来源保留 | PASS；折叠时不堆积 SDK 内部代码，展开后可查看 |
| B 金额、币种、1ページ / PAGE_ONLY 原件预览 | PASS；未伪造单元格坐标 |
| 人工名称修正与原文照合 | PASS；原始来源及 SDK 状态不变 |
| B 不自动学习、重复上传不自动复用 | PASS；全部 B 候选 memory_version=0、有效学习资格 false |
| 资料级未确认不能显示为已完成 | PASS；B 的资料级限制仍未解决 |
| 当前 03_C 社 Excel 名称复用、9500、旧 8000 不变 | PASS；按实际内容，未按文件名推断公司 |
| 网页与真实下载的 Excel 名称/金额/币种一致 | PASS；主表逐格和明细逐项核对 |
| favicon.ico | PASS；HTTP 200，SVG SANZO 标识，控制台无 404 |
| 实际应用控制台/页面/网络错误 | PASS；无 console error、无 pageerror、无业务失败请求 |

新增 6 项：favicon 标识响应、候选警告折叠、A AVAILABLE 无误提示、第三份 AVAILABLE 无误提示、改名原 PDF 的真实读取成功、改名后仍根据 SDK 状态显示候选提示。任意新文件名使用同一原 PDF 字节，SDK 真实执行；没有伪造表格或特殊解析规则。

分类器仍为明确的 **OFFLINE_MECHANISM_TEST**，仅处理价格无关的名称测试选择，读取及业务事实始终来自真实 wheel。真实外部 AI **NOT_RUN，付费调用 0**；不把离线名称对齐当作真实模型准确率。案件名称、状态和日志明确标记 REAL SDK / OFFLINE AI。

## 控制台记录的严格区分

- 主验收不启用 Playwright tracing，避免其脚本注入扰动原文 iframe。没有过滤任何实际 console error/pageerror；它们均为 0。
- 原始网络记录中两条 export.xlsx `net::ERR_ABORTED` 均对应 HTTP 200 和真实浏览器成功下载。只有同时满足成功响应、下载文件存在且后续 Python 3.12 逐格检查 PASS，才判定为导航移交下载；原始事件保留在 `console-and-page-errors.json` 及 `error-classification.json`，不伪装成未出现。
- 对同一实际 PDF 预览做独立 tracing=false/true 差分：false **0 错误**；true 出现 **2 条 sandbox 脚本拒绝信息**，预览页实际无 script 标签且图像正常。完整保留 `console-control.json` 和 trace ZIP，单独标记测试仪器信息。
- 没有增加 iframe allow-scripts、删除安全判定、过滤 favicon 404 或放宽学习规则。既有 iframe sandbox 原字节不变。

## 截图、日志与审核边界

修复前：`before/03-B-limitations.png`、`before/console-control-trace-False.png` 及旧控制台差分日志的原字节副本。修复后：`browser/03-B-collapsed-card.png`、`browser/03-B-limitations.png`、`browser/05-B-original-page.png`、`browser/06-B-correction.png`、`browser/07-B-source-confirmation.png`、`browser/10-third-export-web.png` 和重传/改名截图。

完整原 19 项结果见 `browser/checks.json`；额外 6 项见 `extra-checks.json`；回归逐项 ID 见 `regression-final/results.json`；真实 SDK 原始输出、安装核验、请求日志、截图、下载、数据库审计和全部过程日志在审核包中。ZIP CRC、成员路径安全及内外 SHA-256 已验证；不包含原件、wheel、数据库数据、环境或凭据。

所有专属临时服务、代理、PostgreSQL 容器和网络已经清理。没有正式数据修改、SDK 仓库修改、正式 8765 重建/重启/部署或 commit/push。

剩余边界：B 的 SDK 歧义与合并条件列限制保持原状，其自动学习/条件自动绑定仍 **BLOCKED**；真实 AI 分类质量未执行。本轮两项 UI FAIL 已修复，仅证明限定 UI 兼容性验收通过，不扩大为所有文档或真实模型的验收。**停止等待 ChatGPT 审核，不自行部署。**
