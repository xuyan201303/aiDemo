# SDK 1.8.0 / 220a9ee 受控替换 — 2026-10-14

部署及必要接入验证 PASS。现有 8765 Web 已使用指定新 Wheel，保持运行等待 Review；真实 Azure/OpenAI 新增调用各 0。没有执行收费业务演练、Commit、Push 或修改 SDK 源码仓库。

## 构件和实际容器身份

| 项目 | 本轮核验结果 |
| --- | --- |
| 唯一宿主 Wheel | `/Users/kyoen/Projects/aiDemo/sdk/document_processing_sdk-1.8.0-py3-none-any.whl` |
| SHA256 | `d1dee21ff4b03c555cc1884270062bebc7267f14dec0ade287f5133fb3081ba0` |
| 交付 Commit | `220a9ee228346d9b2cf9147966590808c10a4b39`，来自原字节 INSTALL.md；未读取 SDK 源码核对 |
| METADATA 版本 / Python | `1.8.0` / `>=3.12,<3.13` |
| 实际 Python | `3.12.13`，`/opt/sanzo-venv/bin/python` |
| 实际 SDK | `/opt/sanzo-venv/lib/python3.12/site-packages/document_sdk/__init__.py` |
| 容器 Wheel | `/opt/sanzo-wheel/document_processing_sdk-1.8.0-py3-none-any.whl` |
| 安装成员 | 全部 298 个 SDK 成员与新 Wheel 逐字节一致 |
| 安装来源 | `direct_url.json` 指向容器 Wheel，并记录同一新 SHA；无 editable 或 SDK 源码路径，已加载 SDK 模块全部来自应用 venv |
| 依赖 / 导入 | `pip check` PASS；`analyze_pdf_local_first`、既有 PDF/Excel 公开接口可导入 |
| Web 镜像 | `sanzo-cost-table-demo:d9c99a7b8c13ffb1b6ac` |
| 镜像 ID | `sha256:28edbb2edf69ad02e56460e86e96482f5b6481875e365e6bfcb6394741d97159` |
| Web | `sanzo-demo-86f1a16b19466ef8-web`，running |
| Web ID | `b6cd57bac5cf8ea95c789522ef25b59f90b1db55780b1fa38b924e04485d9ff6` |

`sdk/INSTALL.md`、`sdk/SHA256SUMS` 和 Wheel 均保持用户提供的原字节。两个声明哈希、校验清单自身哈希及 ZIP 完整性已核验。同名 1.8.0 的旧 SHA `781161e4...` 没有作为本轮安装身份。

## aiDemo 改动

- `tools/start_demo.py`：默认 Wheel 改为 `sdk/` 下指定包，更新 SHA；镜像存在性检查仅输出 ID，避免完整环境配置进入构建日志。保留原构建上下文、SHA 校验及生命周期逻辑。
- `backend/Dockerfile.demo`：更新安装前 SHA 校验；继续安装原锁定依赖和 `[pdf,excel]`，保留容器内 Wheel 路径。
- `backend/sanzo_demo/document_sdk_adapter.py`：仅更新默认路径和 SHA。PDF 仍调用 `ADVANCED_LOCAL`，Excel 接口及结果合同不变。
- `tools/probe_document_sdk.py`：同步身份，兼容容器指定 Wheel 路径，增加全部安装成员校验和新入口导入检查；用明确文件名映射读取第三份样例，封存 manifest 保持原字节。
- `tools/sdk_probe_requirements.lock`：同步路径、SHA 和 `[pdf,excel]`，其余依赖版本未变。
- `README.md`：更新当前构件和历史 owner 的安全运行说明，链接本报告；历史路径、SHA 和验收结果保留。
- 新增本报告。

`tools/start_real_demo.py`、业务规则、数据库迁移及主依赖锁定文件无需修改。起始 Git 状态为根目录旧 Wheel 删除、`sdk/` 未跟踪；这些用户变更已保留。

## 验证结果和范围

| 检查 | 结果 |
| --- | --- |
| 新镜像和实际 Web 的 Python、SHA、成员字节、venv、依赖和导入 | PASS |
| 既有 `test_demo_real_adapter`、`test_demo_real_configuration` | 21 项 PASS，0 FAIL，0 SKIP；其中 7 项执行真实 SDK，其余检查应用边界和配置机制 |
| 三份虚构原件的独立 SDK 公开 API probe | PASS，调用数分别 4 / 3 / 4；网络、子进程、凭据和源码仓库访问尝试均为 0 |
| 实际 Web 容器独立 Adapter 冒烟 | PASS，两份 Excel 和 PDF 各 5 条；网络禁用审计，AI 0，不使用 `/process` 或历史上传文件 |
| B PDF 原始结构 | 6×6 / 36 单元格，`LIMITED_EVIDENCE`；`table_id`、`source_identity` 及现有来源/学习检查保留 |
| 8765 `/cases`、`/api/status` | PASS，数据库 CONNECTED，`reader_mode=REAL_SDK`，`sdk.ready=true` |
| 既有案件、原文件及 PDF/Excel 预览 API | PASS，原件哈希匹配 |
| 实际 Safari / 现有 CUA 浏览器工具 | PASS，49 个案件、保存状态、5 行 / 10 明细比较表、学习标记、PDF 原页图和 Excel 单元格预览实际可见 |
| 既有虚构案件 Excel 导出 | PASS，5 行主表与保存投影一致，10 明细金额/币种逐项一致 |
| 新 Web 启动与 API 日志 | 启动完成，错误/500 行为 0，`/process` 请求为 0 |
| `git diff --check` | PASS，HEAD 与起始值一致 |
| 真实 Azure/OpenAI 收费业务验收 | NOT_RUN，本轮不授权；新增调用各 0 |

真实 SDK 成功来自实际公开 API 输出，未使用 Mock SDK 或预置读取答案。测试中的配置替身和合成表格边界检查仅是机制证据。没有把 SDK 的合法结构限制升级为可自动学习状态。

## 数据保护和安全切换

SANZO_TEST 已通过系统挂载信息核验：外置 Samsung SSD 990 PRO 1TB，APFS，UUID `719A3322-58E9-476A-B600-1258B5277549`。宿主构建上下文、测试输入、日志、导出和备份全部位于：

`/Volumes/SANZO_TEST/private/tmp/aiDemo-sdk220a9ee-deploy-20261014`

使用原 `Runtime.build` 构建，旧 Web 在镜像及相关测试完成前保持运行。切换前确认现有 9 个迁移名称/哈希一致、无进行中任务；短暂停止 Web 写入，完成 PostgreSQL custom-format dump、备份目录检查及全部上传文件复制校验，再替换历史命名空间中的 Web。备份数据库验证包含格式头和 `pg_restore --list`，本轮未执行数据库恢复演练。

保护结果：

- 全部 25 张数据库表的完整行哈希、schema 哈希前后一致；49 个案件、明细、学习、历史及预算记录未改动。
- 全部 118 个上传文件字节哈希不变，备份副本逐文件一致。
- 原 PG 容器 ID、镜像、启动时间和数据卷不变；未重启或升级 PG。
- 原上传卷、两条网络、owner 标签、安全选项、只读 rootfs、tmpfs 和 `127.0.0.1:8765` 绑定保留。
- 完整运行环境配置仅在内存中比较并转交子进程，前后完全一致；未打印或保存完整 `Config.Env`、`.env` 或密钥。
- AI 累计预算保持 **50/100 → 50/100**，没有扩额或重置。
- 私有备份目录 700，数据库和备份文件 600；数据备份未写入普通日志。

旧 Web 保留停止状态：`sanzo-demo-86f1a16b19466ef8-web-rollback-sdk220a9ee-20261014`。旧镜像 ID 为 `sha256:033a2897f952861b1b366db9e13fddf75f4d6d917a2760f730dbf84be4d5bf8e`，另有回滚标签 `sanzo-cost-table-demo:rollback-sdk220a9ee-20261014`。部署脚本在新 Web 健康、安装身份或数据保护检查失败时自动移除本轮新 Web、恢复原容器名并启动旧服务；本次未触发回滚。不自动覆盖部署后可能新增的数据。

部署前名称检查首次使用错误 Docker 格式字段 `Id`，在停服前失败；修正为 `ID` 后完成部署。原失败命令和原因保留于独立记录，旧服务在该失败期间未停止。Chrome 检查受到正在使用的 UI 切换保护而中止，随后使用 Safari 完成检查；没有操作其他业务页面。

## 证据和待 Review

外置证据包括 `actual_container_identity.json`、`actual_adapter_smoke.json`、`tests/adapter_configuration.log`、`probes/sample-*/MANIFEST.json`、`data_preservation.json`、`backup_verification.json`、`browser_checklist.json`、`saved_export_verification.json`、`web_log_summary.json`、`deployment_result.json`、Git 起止状态及本轮代码 diff。数据库、上传备份及内容基线仅在 `private/`。

没有发现本次换包引入的未解决启动或读取故障。B PDF 的既有 `LIMITED_EVIDENCE`、原文确认和学习限制继续有效；未扩大文档范围或验证真实收费 AI 质量。当前服务保持运行：<http://127.0.0.1:8765/cases>。工作树未提交，等待 Review。
