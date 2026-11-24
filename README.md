# Personal OS

一个开源的个人工作与生活管理系统。

解决工作和生活时间混在一起、杂事容易忘、多个 AI 订阅额度浪费、多项目切换后上下文丢失等问题。

## 核心功能

- **上下班打卡**：上班启动仪式 + 下班总结，自动统计当天工作时长
- **每日工作记录**：记录工作内容、时长、进程
- **AI 订阅追踪**：记录各 AI 服务的额度与使用情况，估算还能用多久，接近重置时提醒
- **项目面板**：每个项目保存当前状态、上次进度、下一步，方便快速切换和接续
- **任务收件箱**：杂事统一收集，避免遗忘

## 数据安全设计

- **代码开源**（本仓库）
- **个人数据私密**：所有真实数据存在一个**私密 GitHub Gist** 中
- 网页通过你自己的 Personal Access Token 读写私密 Gist
- 别人可以看到代码，但看不到你的打卡记录、AI 用量和项目内容

## 快速开始

### 1. 创建私密 Gist

1. 打开 [https://gist.github.com](https://gist.github.com)
2. 创建一个新的 Gist，文件名建议为 `data.json`
3. 内容先填一个空对象：`{}`
4. 选择 **Secret**（私密）
5. 创建后，复制 Gist ID（URL 最后一串字符，例如 `abc123def456`）

### 2. 创建 Personal Access Token

1. 打开 GitHub Settings → Developer settings → Personal access tokens → Tokens (classic)
2. 生成新 Token，勾选权限：**gist**
3. 复制生成的 Token（只显示一次）

### 3. 使用

1. 打开本仓库的网页（或本地打开 `index.html`）
2. 首次使用时填入：
   - GitHub Personal Access Token
   - 私密 Gist ID
3. 数据会自动同步到你的私密 Gist

## 部署方式

- 直接用浏览器打开 `index.html`（本地文件）
- 或者用任意静态托管（GitHub Pages、Vercel、Netlify 等）

## 技术说明

纯前端实现（HTML + JavaScript），数据通过 GitHub API 同步到私密 Gist。

## License

MIT