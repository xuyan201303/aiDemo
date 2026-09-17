# 离线学习核心参考

这是可运行的机制参考，不是完整Web应用。只依赖Python标准库。SQLite用于小型离线验证；正式持久化目标是PostgreSQL，必须另做并发、迁移、授权与恢复测试。

```bash
cd reference_core
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m quote_framework.demo
```

模块：domain.py定义项目自有类型；repository.py实现学习决策、原子纠错、幂等/版本和撤销；ports.py定义待接入的边界并明确fail-closed；demo.py用合成数据演示。

每次demo使用系统临时目录，演示过程关闭并重新打开SQLite来验证记录持久化，完成后删除演示目录。真实SDK调用0、真实AI调用0；第一次“配送料”候选是假实现，不是模型实测。

生产还需：真实认证、source/condition可靠性校验、AI抽取和历史检索、规则适用完整业务范围、任务与HTTP、PostgreSQL、UI、真实文件安全/读取、预览、Excel、运维。参考实现中Actor/ready由可信测试注入，不能在公网接受客户端随意提交。

`classify`是新的分类尝试，不用于已保存历史表的每次展示；展示读取saved data，防止学习变化影响旧案。金额是已去歧义的十进制字符串，原始Excel文本需要独立提取与验证。
