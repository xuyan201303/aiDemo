# 三文件商谈 Demo：范围调整与接入边界

案件：No.202608310011。基线：实装框架 v1.1.0。项目已从 aiDemo 更名为 sanzo-cost-table；本轮只在用户指定项目实现。

本轮用户明确授权最简单本机网页的三文件 Demo，因此旧「仅W0」与「W1不做网页」限制在本轮指定范围内调整。
继续保留既有纠错、学习与 PostgreSQL 事务，不另建持久化，不开发登录／用户管理／云部署／完整本番能力，不 commit 或 push。
W0 SDK 契约仍 INCOMPLETE，不能因业务代码或打包成功改成 PASS。

起始实际检查：main 尚无首个提交；框架和W1文件均为未跟踪基线。没有通过git init/commit清理。
指定 demo_materials 指令和样例目录不存在，无法读取不存在的内容。缺失条件已一次性向用户询问；等待期间继续独立部分。
没有使用记忆中的SDK位置猜测接入路径。没有扫描其他项目或读取真实 .env／密钥。

已实现部分：本机网页、案件与文档登记、原件SHA校验和预览、明确人工录入、比较表、吹き出し纠错、费用与金额分离、跨案件精确学习复用、撤销／显式重新确认、DRAFT Excel下载。
所有写入仍在现有 sanzo_core schema。002仅扩展人工输入类型并增加原件关联和初次分类快照，001保持校验和不变。
人工确认原文允许建立有依据的学习；这是用户明确抄录并核对的输入，不是SDK读取，输入不带假AI建议，source locator明确UNAVAILABLE。

缺失的真实接入保持关闭：SDK实际公共API、PDF/Excel source位置/错误/释放契约，以及AI调用授权/模型/凭据方式。
网页 /process 返回409 INTEGRATION_BLOCKED，上传不生成报价答案，文件始终BLOCKED。
曾使用 OpenAI Docs 核对结构化输出官方说明，但没有据此执行API调用或暴露未授权调用入口。官方资料：
https://developers.openai.com/api/docs/guides/structured-outputs
SDK绑定仍须先以真实源码/文档核对，应用框架草案不代表SDK公共API。

独立彩排必须标明「合成文件／人工录入」，它检验网页和数据库流程，不抵消真实SDK/AI两轮NOT_RUN。
当前仅支持已去歧义的十进制单价，未知/包含/未报价金额不能强行变为0；未实现真实文件金额状态提取。
Excel预览仅真实单元格值，PDF原件由PDFium只读渲染为页面图片（最多500页），明确不是SDK解析；没有假定位高亮，没有Office完整样式承诺。
所有导出均DRAFT，携带未完成文件和行问题；不提供FINAL输出。

环境只使用项目.venv和本项目专用Docker资源。镜像／依赖下载用于本轮本机运行与测试，PostgreSQL 使用 internal 网络，网页另接专用 bridge 以提供 localhost 入口；不能将网页网络称为完全封闭。代码没有外部 AI 调用或密钥读取入口。
未改SDK、未运行SDK测试/批量任务、未干扰其他进程。启动命令只管理其owner标签匹配的项目容器，stop保留数据。

最终阶段状态需以报告实际结果为准。Review ZIP/外置SHA256/VERIFICATION必须单独验证，不得据打包成功改写PARTIAL或FAIL。

PDF预览按官方PDFium文档采用全局锁，显式关闭文档／页面／位图／PIL资源；不初始化表单脚本、不进行内容提取。https://pypdfium2.readthedocs.io/en/stable/python_api.html
