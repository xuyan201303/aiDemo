# SDK 1.8.0 受控部署与实际 8765 验证 — 2026-10-12

**部署 PASS；真实 8765/Chrome、真实 SDK 三文件业务验证及 Excel 一致性 PASS。实际收费 AI 分类 1 次，处于用户额外授权的最多 2 次以内。未使用离线 AI。停止等待 ChatGPT 复审。**

## 部署前保护与回滚

以当前工作树和 HEAD `375291053bca0590e195f2a7ab0f97e22d25c1a6` 为基准，保留已验证 SDK/UI 适配。本轮唯一工具改动是 `tools/start_demo.py` 的构建资源后缀增加 `.svg`，否则原构建清单会遗漏 favicon。没有修改 SDK/wheel、冻结业务规则或业务代码；没有 commit/push。

已核验 SANZO_TEST 的 APFS 卷、UUID `719A3322-58E9-476A-B600-1258B5277549` 及外置 Samsung SSD 990 PRO。全部备份、构建临时文件、日志、下载和截图位于：

`/Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-deploy-20261012`

现有 8765 使用历史命名空间 `sanzo-demo-86f1a16b19466ef8-*`，而当前路径派生的默认 OWNER 不同。本轮没有直接运行会创建另一套数据卷的默认 start；使用原 `Runtime.build` 构建，并按既有生命周期命令在已核验命名空间中更新 Web，完整保留原标签、配置、安全选项、网络和上传卷。

保护措施：

- 短暂停止 Web 写入，备份 PostgreSQL `pg_dump -Fc`、上传资料、完整容器配置、AI 授权配置、指定配置源及可恢复旧应用 OCI 镜像；PostgreSQL 始终运行。
- 私有备份目录 `private/` 权限 700，配置/镜像/数据库文件权限 600；不输出凭据，不纳入审核 ZIP。
- 在独立 PostgreSQL 实际恢复备份，并比较 **全部 25 张表的所有行哈希**，结果一致。恢复验证容器和网络已清理。
- 旧应用 OCI 索引与所有 blob 哈希已核验；旧 Web 容器保留为停止状态 `sanzo-demo-86f1a16b19466ef8-web-rollback-sdk18-20261012`。
- 现有数据库迁移名称和 SHA-256 与代码完整一致，**无需新增或修改数据库结构**。原启动流程仅校验已应用迁移，没有应用新的迁移。

应用回滚方案：停止/移除本次新 Web，恢复保留旧容器的原名并启动；PG 和上传卷不变。数据库和上传资料另有完整备份，但不能无判断覆盖部署后新增数据。部署脚本若新 Web 健康检查失败，会自动恢复旧 Web。此次部署成功，未执行失败回滚。

## 实际部署与 SDK

新映像：`sanzo-cost-table-demo:da4269e2d9a87418f37a`。

实际 Web：`sanzo-demo-86f1a16b19466ef8-web`，running；PG：`sanzo-demo-86f1a16b19466ef8-pg`，running。PG 容器身份、启动时间不变，没有重启或升级。详细 ID、镜像、状态、启动时间、重启计数见 `containers_after.txt`。

SDK 唯一来源：

`/Users/kyoen/Projects/aiDemo/document_processing_sdk-1.8.0-py3-none-any.whl`

版本 **1.8.0**；SHA-256 **781161e4af3b0b45a1ca8a7745e774e1a2362eb01bcc30956e51619514fcc4e0**。新容器 Python **3.12.13**；SDK 安装于原应用 venv；容器内 wheel 位于 `/opt/sanzo-wheel/`。实际容器验证全部 **296** 个安装文件与指定 wheel 完全一致。宿主 wheel 未修改。

`http://127.0.0.1:8765/cases`、`/api/status`、`/favicon.ico` 均成功；reader_mode=REAL_SDK，SDK ready=true；favicon 返回有效 SANZO SVG，无 404。

关键实际命令（完整 argv、退出码和日志见保护、恢复、构建、部署及浏览器命令记录）：

```sh
docker stop --time 15 sanzo-demo-86f1a16b19466ef8-web
docker exec sanzo-demo-86f1a16b19466ef8-pg pg_dump -U sanzo_demo -d sanzo_demo -Fc
docker cp sanzo-demo-86f1a16b19466ef8-web:/data/uploads <private-backup>/uploads
docker image save sha256:d4157c507d464c01d28d22f2d6ed88376cfcc1c6b9bfbc649a3424069100b623
# 恢复演练：独立 PG 的 pg_restore --exit-on-error --no-owner，25 表全量行哈希一致
# 构建：项目 tools/start_demo.py Runtime.build；TMPDIR 指向外置盘
docker build --pull=false --tag sanzo-cost-table-demo:da4269e2d9a87418f37a <external-build-context>
TMPDIR=/Volumes/SANZO_TEST/private/tmp python3 -B /Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-deploy-20261012/DEPLOY.py
TMPDIR=/Volumes/SANZO_TEST/private/tmp .demo-runtime/sdk18-browser-acceptance-venv/bin/python -B /Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-deploy-20261012/BROWSER_8765.py
TMPDIR=/Volumes/SANZO_TEST/private/tmp .demo-runtime/sdk18-browser-acceptance-venv/bin/python -B /Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-deploy-20261012/READ_UI.py
TMPDIR=/Volumes/SANZO_TEST/private/tmp .demo-runtime/sdk18-browser-acceptance-venv/bin/python -B /Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-deploy-20261012/BROWSER_RESUME.py
```

模板路径的真实参数由 `protection_commands.json`、`restore_commands.json`、`build/*_commands.json`、`deploy_commands.json`、`excel-verification-command.json` 等保存。Docker create 只记录环境变量名称，凭据值通过进程环境转交，不写入日志。

## 实际浏览器业务结果

真实 Google Chrome，直接访问 **8765**，未做 Mock 页面、路由拦截或离线分类器装配。仅新建两个明确标记的测试案件；ID 见 `browser/test-case-ids.json`。

| 检查 | 结果 |
|---|---|
| A 社原件上传与真实 SDK 读取 | PASS；五项采用既有已确认名称，AI 0 |
| B 社原件读取 | PASS；6×6 / 36 单元格，5 条真实候选，原文 PAGE_ONLY / 1 页面 |
| A+B 原価単価一覧表 | PASS；5 行、10 明细，费用名称、文件/公司列、金额、币种和状态实际展示 |
| B 费用名称人工确认/修正 | PASS；通过原有“この明細を修正”逐项选用 A 的现有类别，没有重新确认 A 的共享学习记录或重命名全局费目 |
| B 原文预览与人工照合 | PASS；原 PDF 页面实际渲染，保存本次原文照合记录 |
| LIMITED_EVIDENCE 与资料级未确认 | PASS；日文候选提示、原始限制和详情保留，资料级仍 REVIEW_REQUIRED |
| B 不自动学习/不错误复用 | PASS；初次 B 未命中 MEMORY；人工确认后全部 B learning_eligible=false、memory_version=0，SDK 原始状态不升级 |
| 实际 03_C Excel | PASS；五项 MEMORY，分类 AI 0，原件内部公司仍 Demo A，不按 C 文件名推断公司 |
| 本次 9500 / 原 8000 | PASS；SDK 原文金额来源与本次文件哈希一致，A 原报价保持 8000 |
| 两份真实下载 Excel | PASS；主表逐格与比较投影和真实 DOM 对照，明细逐项核对金额/币种；首轮 5 行/10 明细，第三份 5 行/5 明细 |
| 浏览器 console、pageerror、实际 API | PASS；无实际应用错误；成功下载对应的 ERR_ABORTED 导航移交记录单独保留并核验 HTTP 200 与下载内容 |

没有通过本次测试改写 A 的既有学习。部署前使用新映像和真实 SDK 只读核对五项 A 都是 MEMORY_APPLIED；第三份验证复用该现有学习。B 的人工操作仅修改新测试案件的 B 明细，不会写入可复用学习。

## 真实 AI 与调用限制

用户额外明确授权最多 **2 次真实 AI 分类调用**。实际只调用 **1 次**，用于 B 的 5 条未知费用名称；purpose=fee_classification、execution_kind=OPENAI_API、HTTP 200。实际预算从 **48/100 → 49/100**，没有提高限额、没有自动重试，没有二次 B 分类。A 和第三份均为名称记忆优先，AI 0；金额和业务事实全部由真实 SDK 提供。

这次没有使用离线 AI，不能与前轮 OFFLINE_MECHANISM_TEST 混称。B 的 AI 结果仍是未确认建议，人工通过既有界面选择最终类别；此次一批调用成功也不代表普遍模型准确率。

## 过程问题与真实日志

- 只读预检工具首次调用 decide 签名错误；按实际应用签名修正，原失败日志保留，未修改业务代码。
- 旧镜像 Docker save 为 OCI 索引格式，首次按 config 摘要比较失败；改按真实 OCI 索引及所有 blob 校验通过。部署尚未开始时即阻止了错误校验，不伪报通过。
- 首轮浏览器在保存照合后抓到 SPA 重新加载中的空状态，候选提示断言中断。失败日志及空状态截图原样保留。后台实际 10 明细与 SDK 状态完整；真实 Chrome 重新打开同一案件，等待 5 行完成加载后验证通过。未为此修改应用、重复 B AI 调用或造假截图。后续浏览器操作由独立续验日志记录。
- 上述过程失败不被改写为 PASS。最终部署、业务检查、候选状态复核、Excel、原数据保护均有单独 PASS 证据；无 SKIP 代替 PASS。

## 截图绝对路径（均为真实 8765）

1. A+B 完整比较表：`/Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-deploy-20261012/browser/01_A_B_full_cost_table.png`
2. B 原件确认：`/Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-deploy-20261012/browser/02_B_original_page_confirmation.png`
3. B 候选状态与详细限制：`/Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-deploy-20261012/browser/02_B_candidate_status_details_verified.png`
4. 第三份读取结果：`/Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-deploy-20261012/browser/03_third_excel_comparison.png`

浏览器版本及 origin 元数据、实际请求日志、SDK 原始输出和两份下载一并留存。截图经实际查看，费用、列、金额和状态清晰可见。

## 数据保护与剩余问题

部署和测试后逐表比较原有行哈希：**所有既有案件、文件、明细、费目、学习记录及历史行均未变化/丢失**。仅新增测试数据；唯一原有控制记录变化为已授权的 AI 用量计数。原上传文件字节逐个核对不变。数据库 schema 与 PG 实例不变。详细证据见 `existing_data_preservation.json`、`upload_preservation.json`。

B 的 SDK 结构歧义和未自动绑定的合并条件列仍是已知安全限制，自动学习继续受阻，首轮 Excel 保持草稿状态。这不是本次部署故障，不应删除安全判定让其看起来“已完成”。没有其余未解决的部署故障；模型通用效果和更多文档范围没有在本次扩大验证。

恢复测试资源已清理，新的 8765 服务保持运行供用户测试；旧 Web 保留停止状态用于回滚，私有备份保留，未执行 git commit/push。审核 ZIP 排除数据库、客户原件、配置凭据、镜像和环境，仅包含安全日志、代码 diff、实际虚构测试证据、下载及截图。**提交后停止，等待 ChatGPT 复审。**
