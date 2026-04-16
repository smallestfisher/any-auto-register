# Any Auto Register

<a href="https://bestproxy.com/?keyword=l85nsbgw" target="_blank"><img src="assets/bestproxy.gif" alt="BestProxy - 高纯度住宅IP资源，支持一号一IP独享模式，全链路防关联，显著提升账号通过率与长期存活率" width="100%"></a>

> ⚠️ **免责声明**：本项目仅供学习和研究使用，不得用于任何商业用途。使用本项目所产生的一切后果由使用者自行承担。

多平台账号自动注册与管理系统，支持插件化扩展，当前以 CLI 和 API 方式使用。

## 功能特性

- **多平台支持**：ChatGPT、Cursor、Kiro、Trae.ai、Tavily、Grok、Blink、Cerebras、OpenBlockLabs，支持自定义插件扩展（Anything 通用适配器）
- **多邮箱服务**：MoeMail（自建）、Laoudo、DuckMail、Testmail、Cloudflare Worker 自建邮箱、Freemail、TempMail.lol、Temp-Mail Web
- **多执行模式**：API 协议（无浏览器）、无头浏览器、有头浏览器（各平台按需支持）
- **验证码服务**：YesCaptcha、2Captcha、本地 Solver（Camoufox）
- **接码服务**：SMS-Activate（支持全球手机号租用，用于需要手机验证的平台）
- **代理池管理**：静态代理轮询 + 动态代理 API 提取 + 旋转网关代理，成功率统计、自动禁用失效代理
- **账号生命周期**：定时有效性检测、token 自动续期、trial 过期预警
- **并发注册**：可配置并发数
- **任务与日志**：任务队列、实时任务日志、取消任务、状态跟踪
- **账号导出**：支持 JSON、CSV、CPA、Sub2API、Kiro-Go、Any2API 多种格式
- **Any2API 联动**：注册完成后自动推送账号到 Any2API 网关，注册即可用
- **平台扩展操作**：各平台可自定义操作（如 Kiro 账号切换、Trae Pro 升级链接生成）

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | FastAPI + SQLite（SQLModel） |
| CLI | Python argparse |
| HTTP | curl_cffi（浏览器指纹伪装） |
| 浏览器自动化 | Playwright / Camoufox |

## 快速开始

### 环境要求

- Python 3.11+

### 安装

#### macOS / Linux

```bash
# 克隆项目
git clone <repo_url>
cd account_manager

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

#### Windows

```bat
:: 克隆项目
git clone <repo_url>
cd account_manager

:: 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

:: 安装依赖
pip install -r requirements.txt
```

### 安装浏览器（可选，无头/有头浏览器模式需要）

```bash
python3 -m playwright install chromium
python3 -m camoufox fetch
```

### API 启动

#### macOS / Linux

```bash
.venv/bin/python3 -m uvicorn main:app --port 8000
```

#### Windows

```bat
.venv\Scripts\python -m uvicorn main:app --port 8000
```

访问 API 文档：`http://localhost:8000/docs`

说明：

- 启动入口统一为 `main:app`
- 后端接口统一位于 `/api/*`
- 根路径 `/` 返回 CLI/API 模式说明
- C 端 / 管理端独立 API 项目见 [customer_portal_api/README.md](customer_portal_api/README.md)

### CLI 使用

项目现在以 CLI 和 API 为主。CLI 入口为 `cli.py`，默认复用同一套数据库、平台注册表和后台运行时。

常用命令：

```bash
# 查看平台
python3 cli.py platforms list

# 常驻启动本地任务运行时
python3 cli.py serve

# 查看任务
python3 cli.py tasks list

# 跟踪任务日志
python3 cli.py tasks logs <task_id> -f

# 查看账号
python3 cli.py accounts list

# 导出账号
python3 cli.py accounts export --format csv --platform chatgpt --select-all

# 查看配置
python3 cli.py config get

# 查看 provider 配置
python3 cli.py providers settings mailbox
```

注册任务支持通过环境变量传参，命令行参数会覆盖环境变量。推荐把敏感值放在环境变量里，避免进入 shell 历史。

基础环境变量：

- `AAR_PLATFORM`
- `AAR_EMAIL`
- `AAR_PASSWORD`
- `AAR_COUNT`
- `AAR_CONCURRENCY`
- `AAR_PROXY`
- `AAR_EXECUTOR_TYPE`
- `AAR_CAPTCHA_SOLVER`
- `AAR_IDENTITY_PROVIDER`
- `AAR_OAUTH_PROVIDER`
- `AAR_OAUTH_EMAIL_HINT`
- `AAR_CHROME_USER_DATA_DIR`
- `AAR_CHROME_CDP_URL`
- `AAR_MAIL_PROVIDER`

额外字段可通过 `AAR_EXTRA_*` 传入，例如：

```bash
export AAR_EXTRA_FOO=bar
```

会映射到注册任务的 `extra.foo = "bar"`。

示例：

```bash
export AAR_PLATFORM=chatgpt
export AAR_COUNT=1
export AAR_EXECUTOR_TYPE=protocol
export AAR_IDENTITY_PROVIDER=mailbox
export AAR_MAIL_PROVIDER=moemail

python3 cli.py register create
```

如果希望当前 CLI 进程自己拉起运行时并等待任务结束：

```bash
python3 cli.py register create --wait
```

说明：

- `register create` 只负责创建任务，不带 `--wait` 时需要已有 API 服务或 `python3 cli.py serve` 在后台运行
- `--json` 模式只输出 JSON，适合脚本调用
- 某些平台在 `headed` 或 OAuth 流程下仍可能拉起浏览器，这一点 CLI 不会改变底层执行要求
- 推荐使用统一启动脚本：`./scripts/start.sh web`、`./scripts/start.sh serve`、`./scripts/start.sh cli <subcommand...>`

如果 ChatGPT 的 OAuth 登录过程中命中手机号验证页，CLI 现在支持通过 SMS-Activate 自动完成手机号和短信验证码输入。需要在任务 `extra` 或环境变量中提供：

- `sms_activate_api_key`
- `sms_activate_country`，例如 `us`、`ru`

用环境变量传参时可以写成：

```bash
export AAR_PLATFORM=chatgpt
export AAR_IDENTITY_PROVIDER=oauth_browser
export AAR_OAUTH_PROVIDER=google
export AAR_EMAIL=your_oauth_email@example.com
export AAR_EXTRA_SMS_ACTIVATE_API_KEY=your_sms_activate_key
export AAR_EXTRA_SMS_ACTIVATE_COUNTRY=us

./scripts/start.sh cli register create --wait
```

### Docker 部署

一键启动：

```bash
docker compose up -d
```

访问 API：`http://localhost:8000/docs`。数据库自动持久化到 `./data/` 目录。

如需使用有头浏览器模式（headed），可通过 noVNC 在浏览器中查看自动化过程：`http://localhost:6080`。

## 邮箱服务配置

注册时需要选择一种邮箱服务用于接收验证码。当前版本的邮箱和验证码配置由后端 provider catalog 驱动，建议使用 CLI 的 `providers` 子命令管理。

### MoeMail（推荐）

基于开源项目 [cloudflare_temp_email](https://github.com/dreamhunter2333/cloudflare_temp_email) 自建的临时邮箱服务，无需配置任何参数，系统自动注册临时账号并生成邮箱。

### Laoudo

使用固定的自有域名邮箱，稳定性最高，适合长期使用。

| 参数 | 说明 |
|------|------|
| 邮箱地址 | 完整邮箱地址，如 `user@example.com` |
| Account ID | 邮箱账号 ID（在 Laoudo 面板查看） |
| JWT Token | 登录后从浏览器 Cookie 或接口获取的认证 Token |

### Cloudflare Worker 自建邮箱

基于 [cloudflare_temp_email](https://github.com/dreamhunter2333/cloudflare_temp_email) 自行部署的邮箱服务，完全自主可控。

| 参数 | 说明 |
|------|------|
| API URL | Worker 的后端 API 地址，如 `https://api.your-domain.com` |
| Admin Token | 管理员密码，在 Worker 环境变量 `ADMIN_PASSWORDS` 中配置 |
| 域名 | 收件邮箱的域名，如 `your-domain.com` |
| Fingerprint | 可选，Worker 开启 fingerprint 验证时填写 |

### DuckMail

公共临时邮箱服务，无需配置，直接使用。部分地区需要代理。

### TempMail.lol

公共临时邮箱服务，无需配置，自动生成匿名邮箱。

### Temp-Mail Web

基于 web2.temp-mail.org 的临时邮箱服务，无需配置。

### Freemail

基于 Cloudflare Worker 自建的邮箱服务，支持管理员令牌和用户名密码两种认证方式。

| 参数 | 说明 |
|------|------|
| API URL | Freemail 服务地址 |
| 管理员令牌 | 管理员认证令牌 |
| 用户名 | 可选，用户名密码认证 |
| 密码 | 可选，用户名密码认证 |

### Testmail

`testmail.app` 的 namespace 邮箱模式。系统会自动生成地址：

- `{namespace}.{随机tag}@inbox.testmail.app`

| 参数 | 说明 |
|------|------|
| API URL | 默认 `https://api.testmail.app/api/json` |
| Namespace | 你的 namespace，例如 `3xw8n` |
| Tag Prefix | 可选，给随机 tag 增加前缀 |
| API Key | testmail.app 控制台里的 API Key |

## 验证码服务配置

| 服务 | 说明 |
|------|------|
| YesCaptcha | 需填写 Client Key |
| 2Captcha | 需填写 API Key |
| 本地 Solver | 使用 Camoufox 本地解码 |

## 项目结构

```text
.
├── api/                    # FastAPI 路由层
├── application/            # 应用服务层
├── core/                   # 核心模型与运行时
├── domain/                 # 领域对象
├── infrastructure/         # 仓储与基础设施
├── platforms/              # 各平台插件
├── resources/              # provider 模板与能力定义
├── scripts/                # 启动与辅助脚本
├── services/               # 后台服务
├── customer_portal_api/    # 独立 portal API
├── cli.py                  # CLI 入口
├── main.py                 # API 入口
└── bootstrap.py            # 共享启动逻辑
```
