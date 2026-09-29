**Current comparison projection 修复报告 — 2026-09-29 / 等待 review**

本设计适用于正式开发：稳定业务身份、当前名称、采用时名称和不可变历史产物分别承担不同职责。本次只修复当前比较投影及其确认框消费者，不修改持久化语义，也不代表完成整个系统的生产上线验收。

**ROOT CAUSE**

`backend/sanzo_demo/comparison.py` 的 seed key 包含 `current_fee_name_version`；随后兼容判断又明确拒绝同 fee_id、不同 adopted version 的成员合并。显示名称也优先采用成员保存的旧名称。因此同一业务费目、相同适用条件被错误拆成两行。这一复现不需要清库或重新训练。

修复后的分组使用 stable fee_id 和当前契约的兼容条件：transport_mode、equipment、billing_unit、charge_scope、extra_terms。已存在的来源证据筛选、未知条件防歧义、不同 identity 隔离和重复报价保留规则继续生效。没有根据名称后缀、名称相似度或同名来合并不同 fee_id。

显示名称与名称并发版本优先成对取当前 catalog。无 catalog 的兼容调用可取显式 effective version 对应的当前名称；不会从 `_v1` 等文字推断版本。只有缺少所有当前元数据的旧输入才保留 adopted 名称兜底。实际 HTTP / XLSX / 行确认入口均传入当前 catalog。

`row_id` 继续表示成员集合的并发校验 token，不与业务分组身份混用。

**BEFORE / AFTER**

使用上轮保存的案件 `9f1f0d56-75b9-42f1-85b0-cd5710acbd44` 快照离线验证，本轮未重新调用正式数据库。fee_id 为 `ac4edd3c-cea6-4319-ada3-219d952c2549`。

| 状态 | 比较行 | A初回 | B初回 | A次回 |
| --- | --- | --- | --- | --- |
| Before | 海上運賃_v1（20FT） | 1200 | 1150.00 | 1350 |
| Before | 海上運賃（40FT） | 1900 | 1850.00 | — |
| Before | 海上運賃_v1（40FT） | — | — | 2050 |
| After | 海上運賃_v1（20FT） | 1200 | 1150.00 | 1350 |
| After | 海上運賃_v1（40FT） | 1900 | 1850.00 | 2050 |

币种均为 USD。全表从 6 行变为 5 行，其余报价保留。输入对象和已保存快照字节均未变化。

快照 SHA-256：`557cb2ed0ee089cfecf7e3a173825e0a0db2187d60a26d5ded9e9b98d32e7840`。

**本轮修改文件**

- `backend/sanzo_demo/comparison.py`：移除 adopted name version 的分组/兼容限制，读取当前统一名称与对应版本。
- `backend/sanzo_demo/row_confirmation.py`：只读 catalog 查询增加 name_version，保持名称与并发版本一致；写入、锁、幂等和成员校验不变。
- `backend/sanzo_demo/static/app.js`：确认框按 fee_id 判断 identity 冲突，以 catalog / canonical row label 初始化；历史名称继续展示在明细详情中。
- `tests/test_demo_comparison_projection.py`：新增 6 项纯投影测试。
- `tests/test_demo_fee_rename_r3.py`：新增 R3_21，更新 R3_18 和历史保护 helper 的过时预期。
- 本报告。

既有未提交的 index.html、style.css 和 app.js 界面设计改动已保留；本轮没有重做设计。未修改 document-processing-sdk、原始文件、数据库 schema 或数据迁移。

**新增回归与 A–J 对应**

| 要求 | 验证 |
| --- | --- |
| A / B / C | 同 fee_id 混合 adopted v1/v2 的 40FT 合并，1900/1850/2050 均保留；20FT 独立，1200/1150/1350 保留 |
| D | 两个不同 fee_id 即使最新 display_name 相同也保持两行 |
| E | 五个实际条件分别构造明确冲突，均不合并；原有未知条件桥接测试继续通过 |
| F | catalog 当前名称/版本覆盖 stale effective 元数据；无 catalog 的 effective fallback 与输入顺序无关；后缀 v999 不代表较新 |
| G | pure projection 和 workbook_bytes 不修改输入；旧 adopted snapshot/version 保留 |
| H | R3_21 对读取前后整个测试租户的数据库表做快照等值比较，涵盖 items、corrections、memory、fee-name history |
| I | 实际 HTTP GET comparison 和 GET export.xlsx 的行、列、价格、币种逐项一致；明细 sheet 仍使用 adopted 名称 |
| J | rename、reclassify、identity repair、stale-name、并发、回滚和幂等所在的现有测试模块全部通过 |

R3_21 通过服务操作复现“先确认 20FT/40FT → 只 rename 20FT → 旧 40FT 仍 adopted v1 → 新报价采用 v2 → GET / XLSX 合并”。导出前已生成的历史 XLSX 文件字节保持不变。

旧测试 R3_18 原先要求按 adopted version 拆行，已改为按本次契约验证一行当前名称，同时保留历史不变检查。历史保护 helper 不再将“现在重新生成的比较 sheet”当作冻结历史文件；它分别验证既有 XLSX、明细审计和新比较名称。

**完整测试结果与执行边界**

最终隔离回归：206 tests，0 failures，0 errors，0 skipped，8.386 秒。

| 模块 | 数量 |
| --- | ---: |
| domain / postgres | 15 / 20 |
| store / row_confirmation | 13 / 13 |
| fee_rename_r3 / fee_identity_repair / stale_name_confirmation | 25 / 12 / 15 |
| canonical_projection / comparison_projection / canonical_fees | 4 / 27 / 26 |
| source_preview_export / HTTP | 10 / 7 |
| HumanReconfirmTransitionTests / HumanReconfirmPostgresTests 合计 | 19 |

另外 `node --check backend/sanzo_demo/static/app.js` 和 `git diff --check` 通过。Chrome 的实际页面验证见下文。

本次没有执行依赖真实 SDK 的测试：test_demo_real_sdk_pipeline、test_demo_fee_policy、RealFixedWheelTests、HumanReconfirmRealSDKTests，也没有宣称整个仓库的 SDK / 外部集成 gate 通过。

使用独立 UUID Docker internal network、512 MB tmpfs PostgreSQL，无发布端口、无正式持久卷或正式网络；源码只读挂载。真实 SDK / OpenAI 导入及调用和外部网络有拒绝保护。保护自检通过，实际测试禁止操作尝试为 0。隔离容器、数据库和网络均已清理。

早期失败证据保留：第一次 runner 的 PATH / contracts 挂载配置错误；第二次新增 HTTP fixture 使用了缺少 lifespan 接口的 DemoStore。修正为 IngestionStore 后完整重跑通过，没有删掉失败测试或修改实现绕过它。

最终证据（本机忽略目录，未提交）：

- `.demo-runtime/projection-fix/isolated-20260929T080851Z-1b741629/result.json`
- `.demo-runtime/projection-fix/isolated-20260929T080851Z-1b741629/12-tests.log`
- `.demo-runtime/projection-fix/snapshot-before-after.json`
- `.demo-runtime/projection-fix/current-comparison.xlsx`

**网页与 Excel**

两者继续调用同一 `comparison_projection`；无需复制 Excel 分组逻辑。快照导出的新 XLSX 中同样只有一条 40FT 行，价格与网页一致。

Chrome 已验证 `http://127.0.0.1:18767` 的本地只读快照预览：5 行；20FT / 40FT 的六个报价正确；40FT 确认面板包含三位成员，名称为海上運賃_v1，无历史名称冲突警告；详情中前两位成员仍为海上運賃，第三位为海上運賃_v1。未点击保存、上传或解析。截图已在会话中输出。

此预览拒绝所有 POST，不连接 DB / SDK / AI。它使用冻结快照，不能作为正式运行实例已部署的证据。

**RENAME_FEE 与历史保护**

`backend/sanzo_core/fee_names.py` 的 rename_row 仍只对 command 中的 locked_items 执行 refresh_name=True，并为这些明确参与操作的成员写新 correction event。该行为记录本次参与者采用新名称的事实，应保留。未参与者的 adopted 历史无需刷新。无需扩大为同 case 全量更新，也无需改变持久化语义。

只读投影不写 observation、raw_label、source_ref、current_price、currency、原始文件、旧 correction、fee_name_versions、adopted name snapshot/version 或 memory。

**当前 Demo 数据库是否需要修复**

就本次复现而言，不需要清库、数据回填、迁移、重导文件或重跑学习。现有记录已经共享同一 fee_id 且业务条件兼容，部署后端 projection 修复并刷新案件后，重复 40FT 会自动消失；新生成的 Excel 同步合并。已经生成的历史 Excel 不会自动改写。

本轮未替换/重启正式后端，未改正式 Demo 数据库。正式 web 既有配置把 static 目录只读绑定到工作区，因此本轮 app.js 的兼容性调整在页面重新加载时可见，但正式后端的旧投影仍需后续部署。核心合并效果的验证来自隔离环境。

无 commit、无 push。完成后停止，等待 ChatGPT review。
