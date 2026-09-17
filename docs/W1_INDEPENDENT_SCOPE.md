# W1-INDEPENDENT 范围授权记录
本轮用户明确将原 W0-only 限制仅在独立业务核心范围内调整。框架 AGENTS.md 的 W0-only/先暂停要求不阻止这次已授权实现。
原 W0 证据不删除、不改写；真实 SDK 契约核对仍 INCOMPLETE，不标 PASS。
框架位于项目根的 sanzo_cost_table_framework_v1_1_0 子目录，导入的44个未跟踪文件是起始基线。
main 没有首次提交不是阻塞，不通过 git init/commit 清理。

允许：领域/服务、PostgreSQL 仓储及迁移、事务纠错、学习复用与撤销、版本和幂等、合成结构化输入、对应测试、CLI。
允许：已有 Python3.11+、项目独立 .venv、必要依赖安装锁定，以及已有 Docker 的项目专用可丢弃 PostgreSQL。
允许：修复本轮新增代码的范围内问题并复测，保留失败和复测日志；这覆盖框架旧文档中“失败不自动修复”的默认阶段规则。
仍禁止：SDK 访问或修改、SDK/100K测试、真实 AI/OCR、真实业务文件、真实密钥、W2/W3、网页/完整 Demo、系统软件安装、其他项目 DB、commit/push/发布。

## 保留的核心契约
直接复用 quote_framework.domain 的 NFC+空白、原始表记、5维条件、tenant/company 精确键及十进制规则。
没有改核心契约、归一化版本或未知条件策略。Correction 增加文档要求的 expected_case_revision，不改原参考 schema。
保存当前行、案件版本、纠错历史、学习版本及幂等响应在同一 PostgreSQL 事务；失败整体回滚。
同键不同人工目标进入 CONFLICTED；普通确认不恢复 CONFLICTED/REVOKED，显式替换＋最新版本才恢复。
撤销新增事件与版本，不删历史、不回退、不重写旧案件。
原文 → 人工fee_id；AI建议不自动成为确认学习。旧价格/币种/日期/来源不参与未来分类事实。
同名新建要求显式选择已有 fee_id，不把显示名当作全局语义合并。
不要求客户另行维护 Master。

## 本轮与后续边界
PostgreSQL 是实际目标；无数据库时不能用 SQLite/mock 代替集成验收。
原 SQLite 仅作为保持不变的离线语义基线。纯域函数从原包导入，新的 SQL 与事务服务独立实现。
仅接受可靠的已去歧义结构化金额；文件解析、来源可靠性计算与全部金额状态提取属于未实施的 W2。
未来 HTTP 认证/来源验证、SDK和AI、上传/预览/Excel输出均未实现；CLI不是客户商谈Web Demo。
所有测试/CLI是明确标记的合成数据，故障注入使用 INJECTED_TEST_CASE，不冒充自然数据库事故。
