# 爬虫系统安装部署文档

> 版本：v1.3 | 最后更新：2026-07-24

---

## 目录

1. [环境要求](#环境要求)
2. [快速安装（本地开发）](#快速安装本地开发)
3. [核心依赖说明](#核心依赖说明)
4. [可选依赖](#可选依赖)
5. [配置任务](#配置任务)
6. [启动系统](#启动系统)
7. [Dashboard UI 仪表盘](#dashboard-ui-仪表盘)
8. [API 服务](#api-服务)
9. [云服务器部署](#云服务器部署)
10. [常见问题](#常见问题)

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

```bash
pip install -r requirements.txt
```

**核心依赖**（`pip install -r requirements.txt` 一次性安装）：

| 包名 | 用途 |
|------|------|
| `aiohttp` | 异步 HTTP 请求 |
| `beautifulsoup4` | HTML 解析 |
| `lxml` | XML/HTML 解析引擎 |
| `apscheduler` | 定时任务调度 |
| `pyyaml` | YAML 配置文件解析 |
| `cachetools` | 内存 URL 去重缓存 |
| `fastapi` | HTTP API 框架 |
| `uvicorn` | ASGI 服务器 |
| `openpyxl` | Excel 文件读取（可选，type=excel 时需要） |
| `pymysql` | MySQL 数据库读取（可选，type=db 时需要） |

### 4. 验证安装

```bash
# 验证核心模块
python -c "from crawler.engine import CrawlerEngine; print('核心模块 OK')"

# 验证 API 模块
python -c "from api import create_app; app = create_app(); print(f'API OK, {len(app.routes)} routes')"

# 验证文件读取模块
python -c "from crawler.file_reader import FileReader; print('FileReader OK')"

# 验证数据库读取模块
python -c "from crawler.db_reader import DatabaseReader; print('DatabaseReader OK')"
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

### FastAPI + uvicorn（HTTP API 服务 + Dashboard）

提供 RESTful API 接口和 Dashboard UI。FastAPI 通过 `StaticFiles` 挂载前端静态资源。

- **API 文档**：`http://localhost:8000/docs`（Swagger UI）、`/redoc`（ReDoc）
- **Dashboard UI**：`http://localhost:8000/dashboard/`（Bootstrap 5 + ApexCharts）

### PyYAML（配置解析）

所有任务配置文件均为 YAML 格式。

### cachetools（内存去重）

使用 `TTLCache` 实现 5 分钟内的 URL 内存去重，防止短时间重复请求同一 URL。

---

## 可选依赖

以下依赖仅在特定任务类型时需要安装。

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

当任务使用 `type: sdk` 且 `provider.module: "akshare"` 时需要。

```bash
pip install akshare>=1.18.64
```

### openpyxl（Excel 文件读取）

当任务使用 `type: excel` 时需要。

```bash
pip install openpyxl>=3.1.0
```

### pymysql（MySQL 数据库读取）

当任务使用 `type: db` 连接 MySQL 时需要。

```bash
pip install pymysql>=1.1.0
```

### chardet（字符编码检测）

当网页编码不确定时可选安装。

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
│   ├── hk_stock_short_selling_daily.yaml
│   ├── steam_peak_players_hourly.yaml
│   └── ...
├── offline/                      # 离线/历史数据补录任务
│   ├── steam_peak_players_hourly_his_offline.yaml
│   ├── hk_stock_short_selling_his_offline.yaml
│   └── ...
└── data/                         # CSV/Excel 离线数据文件
    └── *.csv
```

### 创建第一个任务

```bash
# 以 Steam 玩家数据为例
cp config/tasks/steam_peak_players_hourly.yaml config/tasks/my_task.yaml
```

编辑 `my_task.yaml`，修改 `url`、`target_table`、`parser.fields` 等配置项。

完整配置说明请参考：[任务配置文档](task_config_guide.md)

### 支持的数据源类型

系统支持 7 种数据源类型，所有类型走统一的"解析→清洗→批量写入"流水线。

| type | 说明 | 数据获取方式 | 依赖 |
|------|------|-------------|------|
| `api` | JSON API 采集 | aiohttp GET/POST → 响应文本 | aiohttp |
| `web` | HTML 网页采集 | aiohttp → HTML 文本 | aiohttp + BeautifulSoup |
| `web` + `browser` | 浏览器动态页面采集 | Playwright Chromium → 渲染后 HTML | Playwright |
| `sdk` | 第三方 SDK 调用 | `importlib` 动态调用（如 akshare） | akshare 等 |
| `csv` | CSV 文件读取 | `crawler/file_reader.py` → `csv.DictReader` | 标准库（无额外依赖） |
| `excel` | Excel 文件读取 | `crawler/file_reader.py` → `openpyxl` | openpyxl |
| `db` | 外部数据库查询 | `crawler/db_reader.py` → SQLite/MySQL | pymysql（MySQL 时） |

### 数据类型对应的 parser 配置

| type | parser.type 推荐 | 说明 |
|------|-----------------|------|
| `api` | `json` | JSON 路径提取或 `root_path` 数组展开 |
| `web` | `html_table` / `css_selector` | HTML 表格/CSS 选择器提取 |
| `sdk` | `sdk_mapping` | 字段名映射（`source` → `name`） |
| `csv` | `sdk_mapping` | CSV 列名映射（`source` → `name`） |
| `excel` | `sdk_mapping` | Excel 表头映射 |
| `db` | `sdk_mapping` | SQL 列名映射 |

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

API 和调度器共享同一个 asyncio 事件循环，无需额外进程。

### 启动模式对比

| 模式 | 命令 | 定时采集 | API | Dashboard |
|------|------|---------|-----|-----------|
| 单次执行 | `python main.py --run-once "xx"` | ❌ | ❌ | ❌ |
| 定时调度 | `python main.py` | ✅ | ❌ | ❌ |
| 纯 API | `python -m api` | ❌ | ✅ | ✅ |
| API + 调度器 | `python main.py --api` | ✅ | ✅ | ✅ |

---

## Dashboard UI 仪表盘

启动 API 服务后，访问 `http://localhost:8000/dashboard/` 打开仪表盘。

### 页面导航

| 页面 | 路径 | 功能 |
|------|------|------|
| **仪表盘** | `/dashboard/` | 统计卡片（任务数/启用数/业务表/DB 大小） + 执行趋势柱状图 + 状态分布饼图 + 最近日志 |
| **任务管理** | `/dashboard/tasks.html` | 任务列表（名称/类型/目标表/调度）、详情弹窗、手动触发执行按钮 |
| **数据浏览** | `/dashboard/data.html` | 表格/折线图双视图切换、字段选择、图例分组、where 筛选、均值注释线 |
| **日志** | `/dashboard/logs.html` | 按任务名+状态筛选、分页表格 |

### 数据浏览页功能

**表格视图：**
- 选择业务表 → 自动加载列信息
- 支持 `fields`（返回字段）、`order_by`（排序）、`where`（JSON 条件筛选）、分页

**图表视图：**
- 折线图高度 750px，平滑曲线
- **X 轴字段**：从所有列中选择（含类型标注）
- **Y 轴字段**：仅数值列（INTEGER/REAL/FLOAT）
- **图例字段**：可选，按字段值分组生成多条折线
- **where 筛选**：JSON 格式过滤
- **均值注释**：每条线自动计算均值 Y 轴虚线
- **共享 tooltip**：悬停时所有系列数据同时显示
- **图例置顶**

### 技术栈

| 层 | 选型 | 说明 |
|----|------|------|
| 样式 | Bootstrap 5 + Bootstrap Icons | CDN 引入，响应式布局 |
| 图表 | ApexCharts | CDN 引入，折线/柱状/饼图 |
| 数据 | 调用本机 REST API | 同源 `fetch`，复用 `/api/*` 端点 |
| 逻辑 | Vanilla JS | 零框架依赖，模块化 `api.js` + `charts.js` |

---

## API 服务

### API 接口总览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/tasks` | 列出所有任务（名称、类型、调度、目标表、启用状态） |
| `GET` | `/api/tasks/{name}` | 获取单个任务完整配置详情 |
| `POST` | `/api/tasks/{name}/run` | 手动触发任务立即执行，返回新增/更新/跳过统计 |
| `GET` | `/api/logs` | 查询执行历史日志（`task_name` + `status` 筛选 + `limit`/`offset` 分页） |
| `GET` | `/api/logs/{id}` | 单条日志详情 |
| `GET` | `/api/data/tables` | 列出所有业务表名 |
| `GET` | `/api/data/{table}/columns` | 获取表列元数据（列名、类型、约束） |
| `GET` | `/api/data/{table}/query` | **通用查询**（支持 fields/where/group_by/aggregate/order_by/limit/offset） |
| `GET` | `/api/data/{table}/count` | 获取表行数（支持 where 条件计数） |
| `GET` | `/api/system/status` | 系统状态（任务总数/启用数/业务表数/DB 大小/路径） |
| `GET` | `/api/system/dashboard` | **仪表盘聚合统计**（近7天执行趋势 + 状态分布） |
| `GET` | `/api/system/health` | 健康检查 |

### `/api/data/{table}/query` 参数

| 参数 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `fields` | string | 返回字段（逗号分隔） | `steam_id,peak_players` |
| `where` | JSON | 筛选条件数组 | `[{"col":"steam_id","op":"=","value":1974050}]` |
| `group_by` | string | 分组字段 | `steam_id` |
| `aggregate` | string | 聚合表达式 | `COUNT(*) as cnt,AVG(peak_players) as avg` |
| `order_by` | string | 排序 | `peak_players DESC,steam_id ASC` |
| `limit` | int | 每页条数（1-1000） | `20` |
| `offset` | int | 偏移量 | `0` |

**where 支持的运算符**：`=` `!=` `<>` `>` `<` `>=` `<=` `IN` `NOT IN` `LIKE` `NOT LIKE` `IS NULL` `IS NOT NULL` `BETWEEN`

### 示例

#### 手动触发任务

```bash
# 列出所有任务
curl http://localhost:8000/api/tasks

# 触发执行
curl -X POST http://localhost:8000/api/tasks/Steam游戏每小时峰值玩家采集任务/run
```

#### 查询数据

```bash
# 列出所有业务表
curl http://localhost:8000/api/data/tables

# 查看表结构
curl http://localhost:8000/api/data/steam_game_peak_players_hourly/columns

# 筛选 + 排序 + 分页
curl "http://localhost:8000/api/data/steam_game_peak_players_hourly/query?\
fields=steam_id,peak_players,stat_ts&\
where=[{\"col\":\"peak_players\",\"op\":\">\",\"value\":5000}]&\
order_by=peak_players DESC&\
limit=20&offset=0"

# 分组聚合
curl "http://localhost:8000/api/data/steam_game_peak_players_hourly/query?\
aggregate=COUNT(*) as cnt,MAX(peak_players) as max&\
group_by=steam_id&\
order_by=max DESC"

# 条件计数
curl "http://localhost:8000/api/data/steam_game_peak_players_hourly/count?\
where=[{\"col\":\"steam_id\",\"op\":\"=\",\"value\":1974050}]"
```

#### 执行日志

```bash
# 查询最近10条成功日志
curl "http://localhost:8000/api/logs?status=success&limit=10"

# 按任务名过滤
curl "http://localhost:8000/api/logs?task_name=Steam游戏&limit=20"
```

### API 文档

启动 API 后访问自动生成的交互式文档：

```
http://localhost:8000/docs      # Swagger UI（在线调试）
http://localhost:8000/redoc     # ReDoc（文档阅读）
```

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

#### 仅 API 服务（含 Dashboard）

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

# 默认启动调度器（可通过环境变量切换模式）
CMD ["python", "main.py"]
```

```bash
docker build -t crawler .

# 调度器 + API
docker run -d --name crawler \
  -p 8000:8000 \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/data:/app/data \
  crawler python main.py --api --api-port 8000 --api-host 0.0.0.0

# 仅 API 模式
docker run -d --name crawler-api \
  -p 8000:8000 \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/data:/app/data \
  crawler python -m api --host 0.0.0.0 --port 8000
```

### 方案四：Nginx 反向代理（生产环境推荐）

```nginx
server {
    listen 80;
    server_name crawler.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }
}
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

### Q: 如何查看采集数据

**推荐方式**：启动 API 后访问 Dashboard UI：`http://localhost:8000/dashboard/data.html`

或通过命令行：

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

### Q: Dashboard UI 打不开 / 一片空白

确认 API 服务已启动且静态资源可访问：

```bash
# 启动 API
python -m api

# 验证静态资源
curl -I http://localhost:8000/static/css/style.css
curl -I http://localhost:8000/dashboard/
```

### Q: API 有哪些接口

启动 API 后访问：`http://localhost:8000/docs`（Swagger UI）或 `http://localhost:8000/redoc`（ReDoc）

### Q: API 端口被占用

```bash
python -m api --port 8080
python main.py --api --api-port 8080
```

### Q: MySQL 密码如何不写明文

在 YAML 配置中使用环境变量引用 `${MYSQL_PWD}`：

```yaml
db:
  type: mysql
  password: "${MYSQL_PWD}"
```

启动时设置环境变量：

```bash
export MYSQL_PWD=your_password
python main.py --run-once "MySQL数据迁移"
```

### Q: 如何确认任务配置是否正确

```bash
# 运行一次并查看详细日志
python main.py --run-once "任务名称" --log-level DEBUG
```

如果看到 `新增记录 #1: https://...` 表示配置正确，数据已成功写入。

### Q: 如何创建 CSV 离线任务

```yaml
name: "离线数据补录"
type: csv
file:
  format: csv
  path: "config/data/my_data.csv"
  encoding: utf-8
schedule: "0 2 * * *"
target_table: "my_table"
parser:
  type: sdk_mapping
  fields:
    - name: col1
      source: 列名1
```

### Q: Dashboard 图表的 X 轴 / Y 轴字段下拉框为空

切换到图表视图时会自动调用 `GET /api/data/{table}/columns` 加载列信息。如果没有数据，确认：
1. 已选择业务表
2. 表中有数据
3. API 服务正常（检查浏览器控制台网络请求）