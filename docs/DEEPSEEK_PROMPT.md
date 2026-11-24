# 交给 DeepSeek 的开发提示词

复制下面整段发给 DeepSeek（或任意本地 AI 编码助手），并附上 `docs/ARCHITECTURE.md` 全文。

---

你是这个仓库的开发者。请严格按照 `docs/ARCHITECTURE.md` 实现 Personal OS v2。

规则：
1. 按里程碑 M1 → M5 顺序做，**一次只做一个里程碑**；完成后告诉我怎么运行和验收，等我确认再继续。
2. 运行环境是 Windows 11 + Python 3.11；服务器 agent 运行在 Linux，只能用 Python 标准库。
3. 不要改动架构文档里的数据表和 API 设计；确实需要改时先说明原因。
4. 每个里程碑附带 pytest 测试，并更新 `requirements.txt` 和 README 的运行说明。
5. Token 等密钥只从 `config.toml` 读取，`config.toml`、`data/`、`screenshots/` 加入 `.gitignore`。
6. 解析 CLI 日志前，先让我在服务器上运行一条命令贴出一两行样例日志，再按真实格式写解析器。
7. 代码和注释用中文说明关键逻辑，界面文字用中文。

现在从 M1 开始。
