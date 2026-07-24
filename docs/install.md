# 爬虫系统安装部署文档

> 版本：v1.2 | 最后更新：2026-07-24

---

## 目录

1. [环境要求](#环境要求)
2. [快速安装（本地开发）](#快速安装本地开发)
3. [核心依赖说明](#核心依赖说明)
4. [可选依赖](#可选依赖)
5. [配置任务](#配置任务)
6. [启动系统](#启动系统)
7. [API 服务](#api-服务)
8. [云服务器部署](#云服务器部署)
9. [常见问题](#常见问题)

---

## 环境要求

| 组件 | 最低版本 | 说明 |
|------|----------|------|
| Python | 3.9+ | 推荐 3.11+ 以获得更好的异步性能 |
| pip | 21.0+ | Python 包管理器 |
| 操作系统 | Windows / Linux / macOS | 无特殊要求 |
| 磁盘空间 | ~500 MB | 含 Playwright Chromium 浏览器 |
| 内存 | 512 MB+ | 浏览器模式建议 1 GB+ |

---

## 快速安装（本地开发）

### 1. 获取项目

```bash
git clone https://github.com/horgrix/everything.git
cd everything
```

### 2. 创建虚拟环境（推荐）

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
```

### 3. 安装依赖

#### 最小安装（纯 HTTP + 定时调度）

```bash
pip install -r requirements.txt
```

系统会自动安装以下**核心依赖**：

| 包名 | 用途 |
|------|------|
| `aiohttp` | 异步 HTTP 请求 |
| `beautifulsoup4` | HTML 解析 |
| `lxml` | XML/HTML 解析引擎 |
| `apscheduler` | 定时任务调度 |
| `pyyaml` | YAML 配置文件解析 |
| `cachetools` | 内存去重缓存 |
| `fastapi` | HTTP API 框架 |
| `uvicorn` | ASGI 服务器 |

以下依赖按需安装（见[可选依赖](#可选依赖)）：

| 包名 | 用途 | 何时需要 |
|------|------|----------|
| `playwright` | 浏览器动态页面采集 | 使用 `browser` 配置 |
| `akshare` | A股/金融数据 SDK | 使用 `type: sdk` + akshare |
| `openpyxl` | Excel 文件读取 | 使用 `type: excel` |
| `pymysql` | MySQL 数据库读取 | 使用 `type: db` + MySQL |
| `chardet` | 字符编码检测 | 网页编码不确定时 |

### 4. 验证安装

```bash
# 验证核心模块
python -c "from crawler.engine import CrawlerEngine; print('核心模块 OK')"

# 验证 API 模块
python -c "from api import create_app; app = create_app(); print(f'API OK, {len(app.routes)} routes')"
```

---

## 核心依赖说明

### aiohttp（HTTP 请求引擎）

所有 `type: api`、`type: web`（非浏览器模式）任务都通过 aiohttp 发起异步 HTTP 请求。

**特点**：
- 异步 I/O，支持高并发
- 自动连接池复用
- 支持 HTTP/1.1

### BeautifulSoup4 + lxml（HTML 解析引擎）

HTML 页面解析和 CSS 选择器提取字段依赖这两个库。

**注意**：Windows 下安装 `lxml` 如果失败，可以从 [PyPI lxml 页面](https://pypi.org/project/lxml/#files) 下载预编译 `.whl` 安装。

### APScheduler（定时调度）

系统使用 `AsyncIOScheduler`，基于 asyncio 事件循环。支持标准 5 位 cron 表达式。

### FastAPI + uvicorn（HTTP API 服务）

提供 RESTful API 接口，支持 Swagger UI (`/docs`) 和 ReDoc (`/redoc`) 自动文档。

### PyYAML（配置解析）

所有任务配置文件均为 YAML 格式。

### cachetools（内存去重）

使用 `TTLCache` 实现 5 分钟内的 URL 内存去重。

---

## 可选依赖

### Playwright（浏览器动态页面采集）

当任务配置了 `browser` 块时需要。

```bash
pip install playwright
playwright install chromium    # 下载 Chromium 浏览器，约 182 MB
```

**云服务器安装**（Ubuntu/Debian）：

```bash
playwright install-deps chromium
# 或手动：
sudo apt install -y libnss3 libatk-bridge2.0-0 libcups2 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
  libgbm1 libpango-1.0-0 libcairo2 libasound2
```

验证：

```bash
python -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"
```

### akshare（A股/金融数据 SDK）

```bash
pip install akshare>=1.18.64
```

### openpyxl（Excel 文件读取）

```bash
pip install openpyxl>=3.1.0
```

### pymysql（MySQL 数据库读取）

```bash
pip install pymysql>=1.1.0
```

### chardet（字符编码检测）

```bash
pip install chardet>=7.4.3
```

---

## 配置任务

### 任务目录结构

```
config/
├── tasks/                        # 定时采集任务
│   ├── hk_exchange_short_selling_daily.yaml
│   ├── steam_peak_players_hourly.yaml
│   └── ...
├── offline/                      # 离线/历史数据补录任务
│   ├── steam_peak_players_hourly_his_offline.yaml
│   └── ...
└── data/                         # CSV 离线数据文件
    └── *.csv
```

### 创建第一个任务

```bash
# 复制示例文件修改
cp config/tasks/example_api.yaml config/tasks/my_task.yaml
```

编辑 `my_task.yaml`，修改 `url`、`target_table`、`parser.fields` 等配置项。

完整配置说明请参考：[任务配置文档](task_config_guide.md)

### 支持的数据源类型

| type | 说明 | 依赖 |
|------|------|------|
| `api` | JSON API 采集 | aiohttp |
| `web` | HTML 网页采集 | aiohttp + BeautifulSoup |
| `web` + `browser` | 浏览器动态页面采集 | Playwright |
| `sdk` | 第三方 SDK 调用 | akshare 等 |
| `csv` | CSV 文件读取 | 标准库 csv |
| `excel` | Excel 文件读取 | openpyxl |
| `db` | 外部数据库查询（SQLite/MySQL） | pymysql（MySQL 时） |

### 数据库配置

系统默认使用 SQLite，数据库文件路径可通过命令行指定：

```bash
python main.py --db /data/crawler.db
```

---

## 启动系统

系统支持三种启动模式：**单次执行**、**定时调度**、**API 服务**。

### 模式一：单次执行（`--run-once`）

用于测试配置、手动补数据：

```bash
# 运行指定任务一次，查看结果后退出
python main.py --run-once "任务名称"

# 开启 DEBUG 日志
python main.py --run-once "任务名称" --log-level DEBUG
```

### 模式二：定时调度（默认）

启动后自动加载所有任务配置，按 cron 表达式定时执行：

```bash
# 默认配置目录 config/tasks
python main.py

# 自定义配置目录和数据库路径
python main.py --config /path/to/tasks --db /data/crawler.db
```

### 模式三：API 服务

#### 纯 API 模式（不启动调度器）

```bash
# 默认端口 8000
python -m api

# 自定义端口和数据库
python -m api --port 8080 --db /data/crawler.db
```

#### API + 调度器模式

```bash
# 同时启动定时采集和 HTTP API
python main.py --api --api-port 8000
```

### API 接口概览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/tasks` | 列出所有任务 |
| `GET` | `/api/tasks/{name}` | 获取单个任务详情 |
| `POST` | `/api/tasks/{name}/run` | 手动触发任务执行 |
| `GET` | `/api/logs` | 查询执行历史日志（支持筛选和分页） |
| `GET` | `/api/logs/{id}` | 单条日志详情 |
| `GET` | `/api/data/tables` | 列出所有业务表 |
| `GET` | `/api/data/{table}/columns` | 获取表列元数据 |
| `GET` | `/api/data/{table}/query` | 查询业务表数据（支持筛选/分组/聚合/排序/分页） |
| `GET` | `/api/data/{table}/count` | 获取表行数 |
| `GET` | `/api/system/status` | 系统运行状态 |
| `GET` | `/api/system/health` | 健康检查 |

### API 文档

启动 API 后访问自动生成的交互式文档：

```
http://localhost:8000/docs      # Swagger UI（在线调试）
http://localhost:8000/redoc     # ReDoc（文档阅读）
```

---

## API 服务

### 通过 API 手动触发任务

```bash
# 列出所有任务
curl http://localhost:8000/api/tasks

# 查看任务详情
curl http://localhost:8000/api/tasks/Steam游戏每小时峰值玩家采集任务

# 手动触发执行
curl -X POST http://localhost:8000/api/tasks/Steam游戏每小时峰值玩家采集任务/run

# 查询执行历史
curl "http://localhost:8000/api/logs?task_name=Steam游戏&status=success&limit=10"
```

### 通过 API 查询业务数据

```bash
# 列出所有业务表
curl http://localhost:8000/api/data/tables

# 查看表结构
curl http://localhost:8000/api/data/steam_game_peak_players_hourly/columns

# 查询数据（筛选 + 排序 + 分页）
curl "http://localhost:8000/api/data/steam_game_peak_players_hourly/query?\
fields=steam_id,peak_players,stat_ts&\
where=[{\"col\":\"peak_players\",\"op\":\">\",\"value\":5000}]&\
order_by=peak_players DESC&\
limit=20&offset=0"

# 按 steam_id 分组聚合
curl "http://localhost:8000/api/data/steam_game_peak_players_hourly/query?\
aggregate=COUNT(*) as cnt,MAX(peak_players) as max&\
group_by=steam_id&\
order_by=max DESC"

# 条件计数
curl "http://localhost:8000/api/data/steam_game_peak_players_hourly/count?\
where=[{\"col\":\"steam_id\",\"op\":\"=\",\"value\":1974050}]"
```

### 数据查询参数说明

| 参数 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `fields` | string | 返回字段（逗号分隔） | `steam_id,peak_players` |
| `where` | JSON | 筛选条件数组 | `[{"col":"steam_id","op":"=","value":1974050}]` |
| `group_by` | string | 分组字段 | `steam_id,stat_ts` |
| `aggregate` | string | 聚合表达式 | `COUNT(*) as cnt,AVG(peak_players) as avg` |
| `order_by` | string | 排序 | `peak_players DESC,steam_id ASC` |
| `limit` | int | 每页条数（1-1000） | `20` |
| `offset` | int | 偏移量 | `0` |

**where 支持的运算符**：`=` `!=` `<>` `>` `<` `>=` `<=` `IN` `NOT IN` `LIKE` `NOT LIKE` `IS NULL` `IS NOT NULL` `BETWEEN`

---

## 云服务器部署

### 方案一：systemd 服务（推荐 Linux）

#### 仅调度器

```ini
[Unit]
Description=Crawler Scheduler
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/home/your-user/everything
ExecStart=/home/your-user/everything/.venv/bin/python main.py --db /data/crawler.db
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### 仅 API 服务

```ini
[Unit]
Description=Crawler API Service
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/home/your-user/everything
ExecStart=/home/your-user/everything/.venv/bin/python -m api --port 8000 --db /data/crawler.db
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### 调度器 + API 同时运行

```ini
[Unit]
Description=Crawler System (Scheduler + API)
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/home/your-user/everything
ExecStart=/home/your-user/everything/.venv/bin/python main.py --api --api-port 8000 --db /data/crawler.db
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable crawler
sudo systemctl start crawler
sudo systemctl status crawler
```

### 方案二：Crontab（简单定时）

```bash
# 每小时执行一次
0 * * * * cd /home/user/everything && .venv/bin/python main.py --run-once "任务名" >> logs/crawler.log 2>&1
```

### 方案三：Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# 默认启动调度器，设置环境变量切换模式
CMD ["python", "main.py"]
```

```bash
docker build -t crawler .
docker run -d --name crawler \
  -p 8000:8000 \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/data:/app/data \
  crawler

# 仅 API 模式
docker run -d --name crawler-api \
  -p 8000:8000 \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/data:/app/data \
  crawler python -m api --host 0.0.0.0 --port 8000
```

---

## 常见问题

### Q: Windows 下安装 lxml 失败

**解决**：从 [PyPI lxml](https://pypi.org/project/lxml/#files) 下载预编译 `.whl` 安装：

```bash
pip install lxml-5.3.0-cp311-cp311-win_amd64.whl
```

### Q: Playwright 下载太慢

```bash
# 设置镜像加速（国内）
set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/
playwright install chromium
```

### Q: 如何用命令行查看采集数据

```bash
python -c "
import sqlite3
conn = sqlite3.connect('crawler.db')
conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT * FROM your_table LIMIT 5').fetchall()
for r in rows: print(dict(r))
conn.close()
"
```

> 更推荐启动 API 后通过 Swagger UI (`http://localhost:8000/docs`) 在线查询。

### Q: 如何看 API 有哪些接口

启动 API 后访问：`http://localhost:8000/docs`（Swagger UI）或 `http://localhost:8000/redoc`（ReDoc）

### Q: API 端口被占用

```bash
# 指定其他端口
python -m api --port 8080
python main.py --api --api-port 8080
```

### Q: MySQL 密码如何不写明文

在 YAML 配置中使用环境变量引用：

```yaml
db:
  password: "${MYSQL_PWD}"
```

然后启动时设置环境变量：

```bash
export MYSQL_PWD=your_password
python main.py --run-once "MySQL数据迁移"