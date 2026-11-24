# Personal OS

一个以 **Windows 主力电脑** 为中心的个人工作系统，核心作用是**提醒我把工作做完**。

- **上下班打卡**：上班时写「今天三件事」，下班写总结，自动统计工时
- **任务 / 项目追踪**：到期提醒、项目进度、下一步做什么，切换项目不丢上下文
- **AI 用量汇总**：多个 AI 账号（Claude / ChatGPT / Gemini / Grok）的额度与用量
- **本地优先**：SQLite 是本机唯一真实数据源，截图只存本机、绝不上传

架构：数据层见 `app/db.py`（9 张表 + `PRAGMA user_version` 迁移），接口见 `app/api.py`，前端见 `app/web/`。

## 当前进度

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | SQLite 数据层 + REST API + 打卡/任务/项目 CRUD + 最简 Web UI | ✅ 已完成 |
| M2 | 调度器 + 自动截图 + 托盘 + Windows 通知 + 提醒 | ⬜ 待做 |
| M3 | AI 账号/额度手动管理 + 自动重置 + 今日页展示 | ⬜ 待做 |
| M4 | 服务器 agent（Claude Code 解析器）+ Gist 上报 + 主程序汇总 | ⬜ 待做 |
| M5 | Gist 备份、截图清理、PyInstaller 打包、开机自启 | ⬜ 待做 |

## 快速开始（M1）

环境：Windows 11 + Python 3.11 及以上（Linux / macOS 也能跑，只有截图和托盘是 Windows 专属）。

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 复制配置（可选，不配也能用默认值）
cp config.example.toml config.toml

# 3. 启动
python -m app.main
```

然后打开 <http://127.0.0.1:8765>，就能看到面板。

- 数据落在 `data/personal_os.db`（首次启动自动建表）
- 接口文档在 <http://127.0.0.1:8765/api/docs>
- 换端口：改 `config.toml` 里的 `[web] port`，或设环境变量 `PERSONAL_OS_CONFIG` 指向别的配置文件

### 换个数据位置

```bash
PERSONAL_OS_DATA_DIR=D:/personal-os-data python -m app.main    # Windows PowerShell 用 $env:PERSONAL_OS_DATA_DIR
```

## M1 验收清单

在浏览器里逐条试一遍，全部通过即 M1 达标：

1. 点左下角「上班打卡」→ 弹窗写三件事 → 左侧变成「上班中」，今日工时开始计时
2. 在「任务」页添加任务（标题 + 截止日期 + 时间 + 优先级 + 重复），回到「今日」能看到它落在「今天到期 / 逾期 / 高优先级」分组里
3. 勾选一个**重复任务**（如每天）→ 它不会消失，截止日期自动推到下一次
4. 「项目」页新建项目 → 拖动进度条、写一条进度日志 → 「今日」页显示进度和「下一步」
5. 「打卡记录」页选日期 → 能看到当天的打卡时间段、时长、计划与总结
6. **关掉服务再启动**（Ctrl+C 后重新 `python -m app.main`）→ 数据都还在

## 目录结构

```
app/                 # 主力电脑程序
├─ main.py           # 入口：启动 DB + Web 服务
├─ config.py         # 读取 config.toml
├─ db.py             # SQLite 连接 + 建表/迁移
├─ api.py            # FastAPI 路由
└─ web/              # 前端（原生 HTML/JS，无构建工具）
tests/               # pytest
agent/               # 服务器端用量上报（M4）
docs/                # 架构文档
```

## 接口一览（M1）

```
GET    /api/health                  健康检查
GET    /api/today                   今日汇总（打卡状态 + 任务分组 + 进行中项目）
POST   /api/clock/in   {plan}       上班打卡
POST   /api/clock/out  {summary}    下班打卡
GET    /api/sessions?date=          打卡记录
GET    /api/screenshots?date=       截图列表（M2 起有数据）
GET    /shots/<path>                本地截图文件
CRUD   /api/tasks                   任务（勾选完成时重复任务自动顺延）
CRUD   /api/projects                项目
CRUD   /api/projects/{id}/logs      项目进度日志
```

## 测试

```bash
python -m pytest
```

测试全部使用临时数据库，不会碰你的 `data/`。

## 数据安全

- 代码开源，**个人数据私密**：`config.toml`、`data/`、`screenshots/` 都在 `.gitignore` 里
- Gist Token 只存本机 `config.toml`
- 截图只存本地 `data/screenshots/`，永不上传云端

## 说明

根目录的 `index.html` 是纯前端 + Gist 的旧原型，M2 完成后删除。

## License

MIT
