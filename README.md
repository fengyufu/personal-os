# Personal OS

一个以 **Windows 主力电脑** 为中心的个人工作系统，核心作用是**提醒我把工作做完**。

- **上下班打卡**：上班时写「今天三件事」，下班写总结，自动统计工时
- **任务 / 项目追踪**：到期提醒、项目进度、下一步做什么，切换项目不丢上下文
- **自动截图 + 提醒**：上班期间定时截图留痕；任务到点、该上班、工作太久、项目停滞都会弹通知
- **AI 用量汇总**：多个 AI 账号（Claude / ChatGPT / Gemini / Grok）的额度与用量（M3）
- **本地优先**：SQLite 是本机唯一真实数据源，截图只存本机、绝不上传

架构：数据层见 `app/db.py`（9 张表 + `PRAGMA user_version` 迁移），接口见 `app/api.py`，业务逻辑见 `app/services.py`，前端见 `app/web/`。

## 当前进度

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | SQLite 数据层 + REST API + 打卡/任务/项目 CRUD + Web UI + 日历 | ✅ 已完成 |
| M2 | 调度器 + 自动截图 + 提醒通知 + 托盘 + 启动器 | ✅ 已完成 |
| M3 | AI 账号/额度手动管理 + 自动重置 + 今日页展示 | ⬜ 待做 |
| M4 | 服务器 agent（Claude Code 解析器）+ Gist 上报 + 主程序汇总 | ⬜ 待做 |
| M5 | Gist 备份、PyInstaller 打包、正式开机自启 | ⬜ 待做 |

## 快速开始（M1 + M2）

环境：Windows 11 + Python 3.11 及以上（Linux / macOS 也能跑，截图、托盘、通知是 Windows 专属）。

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 复制配置（可选，不配也能用默认值）
cp config.example.toml config.toml

# 3. 启动（托盘 + 自动打开浏览器）
python -m app.main
```

启动参数：

```bash
python -m app.main                # 默认：后台服务 + 托盘 + 自动开浏览器
python -m app.main --no-tray      # 不显示托盘（调试 / 服务器上用）
python -m app.main --no-browser   # 不自动打开浏览器
```

- 数据落在 `data/personal_os.db`（首次启动自动建表），截图在 `data/screenshots/YYYY-MM-DD/`
- 接口文档在 <http://127.0.0.1:8765/api/docs>
- **单实例**：端口已被占用时不会重复启动，只把面板打开
- 换端口：改 `config.toml` 里的 `[web] port`，或用环境变量 `PERSONAL_OS_CONFIG` 指向别的配置文件
- 日志直接打在控制台（截图、提醒都会记一行），排查问题先看它

### Windows 开机自启（M2 阶段先这样，打包留到 M5）

**方式一：启动文件夹（最简单）**

1. 记下 Python 路径：`python -c "import sys; print(sys.executable)"`
2. `Win + R` 输入 `shell:startup` 回车，打开「启动」文件夹
3. 右键 → 新建 → 快捷方式
   - 目标：`"<python.exe 的路径>" -m app.main`
   - 起始位置：`<personal-os 目录>`
4. 想开机静默运行（不弹黑框）就把 `python.exe` 换成同目录的 `pythonw.exe`，代价是看不到控制台日志

**方式二：任务计划程序（更可控）**

1. `Win + R` → `taskschd.msc` → 创建基本任务
2. 触发器选「当我登录时」，操作选「启动程序」
   - 程序：`<pythonw.exe 的路径>`
   - 添加参数：`-m app.main`
   - 起始于：`<personal-os 目录>`
3. 勾选「如果任务失败，每隔 1 分钟重启一次」

### 换个数据位置

```bash
PERSONAL_OS_DATA_DIR=D:/personal-os-data python -m app.main    # PowerShell 用 $env:PERSONAL_OS_DATA_DIR
```

## M2 做了什么

| 模块 | 说明 |
|---|---|
| `app/services.py` | 上下班打卡逻辑，api 和托盘共用一份，进行中的 session 不按日期过滤（支持跨零点下班） |
| `app/screenshot.py` | mss 抓所有显示器合并的整屏 → Pillow 存 JPEG；五种情况跳过；平均哈希去重；过期清理 |
| `app/reminders.py` | 五条提醒规则（任务到点 / 该上班 / 工作太久 / 上班早报 / 项目停滞），纯函数，`now` 由外部传入 |
| `app/notify.py` | Windows 用 win11toast，点通知打开面板；非 Windows 或异常时退化为日志 |
| `app/scheduler.py` | APScheduler 每分钟 tick：到点截图 + 发提醒（写 `reminders_sent` 去重）；每天 03:00 清理 |
| `app/tray.py` | pystray 托盘：打开面板 / 上下班打卡 / 立即截图 / 暂停截图 1 小时 / 退出，图标随打卡状态变色 |
| `app/main.py` | 单实例检测、uvicorn 跑后台线程、托盘占主线程、退出时先停调度器再停服务 |

### 截图什么时候会跳过

| 情况 | 是否跳过 | 前端提示 |
|---|---|---|
| 没打卡（不在上班状态） | 跳过 | 上班后才自动截图 |
| 手动暂停期间 | 跳过（「立即截图」可越过） | 截图已暂停 · HH:MM 恢复 |
| 锁屏 / 屏保（Windows `OpenInputDesktop` 失败或前台是 LockApp.exe） | 跳过（「立即截图」也不越过） | 锁屏或屏保中，不截图 |
| 前台窗口标题命中 `screenshot.exclude_apps` | 跳过（「立即截图」也不越过） | 当前窗口在隐私排除列表里 |
| 和上一张几乎一样（32×32 平均哈希汉明距离 ≤ 3） | 跳过（「立即截图」可越过） | 画面和上一张几乎一样 |
| 配置里 `screenshot.enabled = false` | 跳过 | — |

### 提醒规则

| 规则 | 触发条件 | 去重 key |
|---|---|---|
| 任务到点 | 未完成任务 `due_date + due_time` 落在 `(now-2min, now]` | `task:<id>:<到期时刻>` |
| 该上班了 | 工作日、到点（默认 09:30）还没打卡、且没到 12:00 | `clockin:<日期>` |
| 工作太久 | 进行中的 session 超过 `work.max_hours` 小时 | `overtime:<session_id>` |
| 上班早报 | 打卡后 1 分钟内：逾期 / 今天到期的任务数和前 3 个标题 | `morning:<session_id>` |
| 项目停滞 | 进行中项目超过 3 天没日志，每天 10:00 后提醒一次 | `stale:<project_id>:<日期>` |

去重记在 `reminders_sent` 表里，重启不会重复打扰；通知发失败也照样记账，避免每分钟重试刷屏。

## M2 验收清单

1. **早报**：点「上班打卡」写下三件事 → 1 分钟内弹「早上好，先看这三件」通知
2. **自动截图**：把 `config.toml` 里 `[screenshot] interval_minutes` 改成 `1`，重启 → 每分钟出一张，侧边栏显示「下一张截图：xx 分钟后」
3. **锁屏不截**：`Win + L` 锁屏，等两分钟 → 记录里没有新截图，日志写「锁屏或屏保中」
4. **隐私窗口不截**：把 `exclude_apps` 设成 `["记事本"]`，打开一个记事本窗口等一轮 → 不截图
5. **任务到点只弹一次**：新建一个 2 分钟后到期的任务 → 到点弹通知，之后不再重复弹
6. **暂停 / 恢复**：点侧边栏「暂停 1 小时」→ 提示变成「截图已暂停」；再点「恢复截图」→ 恢复正常
7. **大图**：打卡记录页点任意缩略图 → 弹出大图，Esc 或「关闭」退出
8. **托盘**：托盘图标上班中为绿色、下班后灰色；菜单的打卡 / 立即截图 / 暂停 / 退出都能用
9. **单实例**：已经运行时再 `python -m app.main` → 只打开面板，不会起第二个进程
10. **关掉重启**：截图、提醒记录、打卡都还在

## 目录结构

```
app/                 # 主力电脑程序
├─ main.py           # 入口：单实例 + 调度器 + Web 服务 + 托盘
├─ config.py         # 读取 config.toml
├─ db.py             # SQLite 连接 + 建表/迁移
├─ services.py       # 共享业务逻辑（打卡）
├─ api.py            # FastAPI 路由
├─ scheduler.py      # 每分钟 tick：截图 + 提醒
├─ screenshot.py     # 抓屏、去重、清理
├─ reminders.py      # 提醒规则
├─ notify.py         # 系统通知
├─ tray.py           # 托盘图标
└─ web/              # 前端（原生 HTML/JS，无构建工具）
tests/               # pytest（96 个用例，全部用临时库）
agent/               # 服务器端用量上报（M4）
docs/                # 架构文档与里程碑提示词
```

## 接口一览（M1 + M2）

```
GET    /api/health                  健康检查
GET    /api/today                   今日汇总（打卡状态 + 任务分组 + 进行中项目 + 截图数）
POST   /api/clock/in   {plan}       上班打卡
POST   /api/clock/out  {summary}    下班打卡
GET    /api/sessions?date=          打卡记录
GET    /api/screenshots?date=       截图列表
POST   /api/screenshots/capture     立即截图（返回记录或跳过原因）
POST   /api/screenshots/pause       {minutes}，0 表示恢复
GET    /api/scheduler/status        调度器状态 / 下次截图倒计时
GET    /shots/<path>                本地截图文件
GET    /api/calendar?start=&end=    日历汇总（任务、截止日、工时、截图数）
CRUD   /api/tasks                   任务（勾选完成时重复任务自动顺延）
CRUD   /api/projects                项目
CRUD   /api/projects/{id}/logs      项目进度日志
```

## 测试

```bash
python -m pytest
```

96 个用例，覆盖打卡跨零点、重复任务顺延、五条提醒规则、截图跳过与去重、调度间隔、新接口。
全部使用临时数据库和假的抓屏函数，不会碰你的 `data/`，也不会截你的屏。

## 数据安全

- 代码开源，**个人数据私密**：`config.toml`、`data/` 都在 `.gitignore` 里
- Gist Token 只存本机 `config.toml`
- 截图只存本地 `data/screenshots/`，永不上传云端；超过 `keep_days` 天自动删除
- 服务只监听 `127.0.0.1`，局域网访问不到

## 说明

根目录的 `index.html` 是纯前端 + Gist 的旧原型，M5 完成后删除。

## License

MIT
