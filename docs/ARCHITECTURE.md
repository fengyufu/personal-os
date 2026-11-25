# Personal OS 架构设计（v2）

> 本文是交给 AI 编码助手（DeepSeek 等）的实现规格。按「实现顺序」逐个里程碑完成，每个里程碑都要能独立运行。

## 1. 目标

一个以 **Windows 主力电脑** 为中心的个人工作系统，主要作用是**提醒我完成工作**：

1. 上下班打卡，上班期间**每 30 分钟自动截图**留痕
2. 任务 / 项目进度追踪，到点用 Windows 系统通知提醒
3. 汇总多个 AI 账号（Claude / ChatGPT / Gemini / Grok）的使用进度；
   AI 主要在**几台服务器上以 CLI 方式运行**（Claude Code、Codex、Gemini CLI），服务器只负责上报用量
4. 数据云端备份（私密 GitHub Gist），截图只存本地

非目标（以后再说）：Google Calendar / Apple 提醒事项联动、手机 App。

## 2. 总体结构

```
┌──────────────────────── Windows 主力电脑 ────────────────────────┐
│  personal-os（Python 常驻进程，开机自启，托盘图标）               │
│   ├─ Web UI   http://127.0.0.1:8765   （FastAPI + 静态页面）      │
│   ├─ Scheduler（APScheduler）                                     │
│   │    ├─ 每 30 分钟截图（仅打卡上班期间）                        │
│   │    ├─ 每分钟检查提醒 → Windows Toast 通知                      │
│   │    └─ 每 10 分钟：拉取服务器用量 + 备份数据到 Gist            │
│   ├─ SQLite  data/personal_os.db   （唯一真实数据源）             │
│   └─ screenshots/YYYY-MM-DD/HHMM.png （只存本地）                 │
└──────────────────────────────▲───────────────────────────────────┘
                               │ 读取 usage-*.json
                      ┌────────┴────────┐
                      │  私密 GitHub Gist │  data.json（备份）
                      │                  │  usage-<host>.json（各服务器上报）
                      └────────▲────────┘
                               │ 每 15 分钟写入
        ┌──────────────────────┼──────────────────────┐
   服务器 A                  服务器 B                服务器 C
   usage_agent.py（cron）：解析本地 CLI 日志 → 汇总 → 上传
```

为什么经 Gist 中转：主力电脑在内网、没有公网 IP，服务器无法直接访问它；Gist 两边都能访问，且无需自建服务。

## 3. 目录结构

```
personal-os/
├─ app/                      # 主力电脑程序
│  ├─ main.py                # 入口：启动 DB、调度器、Web 服务、托盘
│  ├─ config.py              # 读取 config.toml
│  ├─ db.py                  # SQLite 连接 + 建表/迁移
│  ├─ api.py                 # FastAPI 路由（REST，见第 6 节）
│  ├─ scheduler.py           # 所有定时任务注册
│  ├─ screenshot.py          # 截图（mss），多显示器合并或分别保存
│  ├─ reminders.py           # 计算到期提醒
│  ├─ notify.py              # Windows Toast（win11toast / plyer）
│  ├─ tray.py                # 托盘图标（pystray）：打开面板/上班/下班/退出
│  ├─ usage.py               # 拉取各服务器 usage-*.json，汇总 AI 用量
│  ├─ gist_sync.py           # Gist 读写
│  └─ web/                   # 前端（单页，原生 JS 即可）
│     ├─ index.html
│     └─ app.js / app.css
├─ agent/                    # 服务器端用量上报（只依赖 Python 标准库）
│  ├─ usage_agent.py
│  └─ parsers/
│     ├─ claude_code.py
│     ├─ codex.py
│     └─ gemini_cli.py
├─ config.example.toml
├─ requirements.txt
└─ docs/
```

旧的根目录 `index.html`（纯前端 + Gist 原型）在 M2 完成后删除。

## 4. 数据模型（SQLite）

所有表带 `id INTEGER PK`、`created_at`、`updated_at`（ISO 时间字符串，本地时区）。

| 表 | 关键字段 | 说明 |
|---|---|---|
| `work_sessions` | date, clock_in, clock_out, plan, summary | 一天可多段；plan=上班时写的「今天三件事」，summary=下班总结 |
| `screenshots` | session_id, taken_at, path, note | 截图记录；note 预留给以后 AI 生成的描述 |
| `projects` | name, status(active/paused/done), progress(0-100), deadline, current_state, next_step | 项目面板 |
| `project_logs` | project_id, date, text, minutes | 进度日志 / 每日工作记录 |
| `tasks` | title, notes, project_id, due_date, due_time, priority(1-3), repeat(none/daily/weekdays/weekly/monthly), done, done_at | 任务；重复任务完成后把 due_date 推到下一次，done 复位 |
| `reminders_sent` | key, sent_at | 防止同一提醒重复弹出 |
| `ai_accounts` | provider(claude/chatgpt/gemini/grok), label, plan, source(manual/server), host, renew_day, notes | 一个服务商可多个账号 |
| `ai_limits` | account_id, name, period(5h/day/week/month), cap, unit(%/msgs/tokens/usd), used, reset_at | 手动维护的额度；到 reset_at 自动清零并顺延一个周期 |
| `ai_usage` | account_id, host, tool, period_start, input_tokens, output_tokens, cache_tokens, requests | 服务器上报后汇总写入 |

## 5. 核心功能规格

### 5.1 打卡 + 截图
- 上班：创建 work_session，弹窗填「今天三件事」，**启动截图任务**。
- 截图：每 30 分钟（可配置）用 `mss` 截全部屏幕，存 `screenshots/YYYY-MM-DD/HHMM.png`，压缩为 JPEG/WebP 质量 70。
  - 锁屏 / 屏幕无变化时跳过（与上一张做缩略图哈希比较）。
  - 配置项 `screenshot.exclude_apps`：前台窗口标题命中则跳过（隐私，如银行、聊天）。
  - 保留天数 `screenshot.keep_days`（默认 30），过期自动删除。
- 下班：填总结，结束 session，**停止截图**，Web 页面可按时间轴回看当天截图。
- 提醒：设定时间未打卡 → 通知；上班超过 N 小时未下班 → 通知。

### 5.2 任务 / 项目
- 「今日」页：逾期 + 今天到期 + 高优先级未完成任务；进行中项目的进度条和「下一步」。
- 任务有 due_time 的到点通知；没有 time 的在每日早报（上班打卡时）里列出。
- 项目卡片可写进度日志；长时间（默认 3 天）没有日志的 active 项目 → 提醒「这个项目停了」。

### 5.3 AI 用量
两种来源并存：
- **server**：服务器 agent 上报的真实 token 用量（Claude Code / Codex / Gemini CLI）。
- **manual**：网页订阅或 Grok 等无日志的，手动在 Web 上填「已用 %」+ 重置时间。

展示：每个账号显示当前 5 小时窗口 / 今日 / 本周用量、下次重置倒计时；
**推荐当前用哪个账号**（同服务商里用量占比最低的）。额度 > 80% 或刚重置 → 通知。

### 5.4 服务器 agent（`agent/usage_agent.py`）
- 纯标准库，`cron` 每 15 分钟运行一次：`python3 usage_agent.py --config agent.toml`
- 扫描本地 CLI 日志，按「账号 × 小时」聚合 token，输出：

```json
{
  "host": "server-a",
  "generated_at": "2026-11-24T10:15:00+08:00",
  "accounts": [
    {
      "account": "claude-work",
      "tool": "claude_code",
      "hourly": [
        {"hour": "2026-11-24T09:00", "input": 12000, "output": 3400, "cache_read": 80000, "cache_write": 5000, "requests": 21}
      ]
    }
  ]
}
```

- 只保留最近 8 天的小时数据，PATCH 到 Gist 的 `usage-<host>.json`（只改自己那个文件，互不冲突）。
- `agent.toml` 里配置：日志目录 → 账号名映射（同一台服务器可能登录不同账号）。
- 日志位置（**实现前需在服务器上实际核对格式**）：
  - Claude Code：`~/.claude/projects/**/*.jsonl`，每行 JSON，`type=="assistant"` 时 `message.usage` 含 `input_tokens / output_tokens / cache_creation_input_tokens / cache_read_input_tokens`，`timestamp` 为时间；按 `message.id` 去重。
  - Codex CLI：`~/.codex/sessions/**/*.jsonl`，找 token 计数事件。
  - Gemini CLI：`~/.gemini/` 下的会话/遥测文件。
- 增量解析：记录每个文件已读到的字节偏移，存在 `~/.personal-os-agent/state.json`。

### 5.5 云同步
- 主力电脑每 10 分钟把 SQLite 关键表导出为 `data.json` 写入 Gist（备份 + 以后手机查看用）。
- Gist Token 只存本机 `config.toml`，不进 Git、不进 Gist。

## 6. API（主程序，供前端用）

```
GET  /api/today                     今日汇总
POST /api/clock/in   {plan}         上班
POST /api/clock/out  {summary}      下班
GET  /api/sessions?date=            打卡记录
GET  /api/screenshots?date=         截图列表；GET /shots/<path> 取图
CRUD /api/tasks  /api/projects  /api/projects/{id}/logs
CRUD /api/ai/accounts  /api/ai/limits
GET  /api/ai/usage                  汇总后的用量 + 推荐账号
POST /api/sync                      立即同步
```

## 7. 配置（config.example.toml）

```toml
timezone = "Asia/Shanghai"

[work]
clock_in_remind = "09:30"
max_hours = 9

[screenshot]
enabled = true
interval_minutes = 30
keep_days = 30
quality = 70
exclude_apps = ["微信", "银行"]

[gist]
token = ""        # 仅 gist 权限
gist_id = ""

[web]
port = 8765
```

## 8. 技术选型

Python 3.11+；FastAPI + uvicorn；APScheduler；mss + Pillow（截图）；
pystray（托盘）；win11toast（通知）；sqlite3 标准库；httpx（Gist）。
打包：PyInstaller 打成单个 exe；开机自启用「启动」文件夹快捷方式。
前端：原生 HTML/JS，不引入构建工具。

## 9. 实现顺序（里程碑）

| # | 内容 | 验收 |
|---|---|---|
| M1 | db.py + api.py + 打卡/任务/项目 CRUD + 最简 Web UI | 浏览器能增删改，重启数据仍在 |
| M2 | scheduler + 截图 + 托盘 + Windows 通知 + 提醒 | 打卡后 30 分钟出现截图；到点弹通知 |
| M3 | AI 账号/额度手动管理 + 自动重置 + 今日页展示 | 能看到各账号剩余和重置倒计时 |
| M4 | agent（先做 Claude Code 解析器）+ Gist 上报 + 主程序拉取汇总 | 服务器跑完 agent，主程序看到 token 用量 |
| M5 | Gist 备份、截图清理、PyInstaller 打包、开机自启、README | 双击 exe 即可使用，发布 v1.0 Release |

## 10. 约束

- 截图绝不上传云端。
- 所有时间按 `timezone` 配置处理。
- 每个模块写最基本的单元测试（pytest），parsers 用样例 jsonl 测。
- 不要引入数据库服务器、Docker、前端框架。
