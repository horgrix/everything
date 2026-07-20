# 爬虫系统安装部署文档

> 版本：v1.1 | 最后更新：2026-07-20

---

## 目录

1. [环境要求](#环境要求)
2. [快速安装（本地开发）](#快速安装本地开发)
3. [核心依赖说明](#核心依赖说明)
4. [可选依赖](#可选依赖)
5. [配置任务](#配置任务)
6. [启动系统](#启动系统)
7. [云服务器部署](#云服务器部署)
8. [常见问题](#常见问题)

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

### 3. 安装核心依赖

```bash
pip install -r requirements.txt
```

核心依赖包含：

| 包名 | 用途 |
|------|------|
| `aiohttp` | 异步 HTTP 请求 |
| `beautifulsoup4` | HTML 解析 |
| `lxml` | XML/HTML 解析引擎 |
| `apscheduler` | 定时任务调度 |
| `pyyaml` | YAML 配置文件解析 |
| `cachetools` | 内存去重缓存 |

### 4. 验证安装

```bash
python -c "from crawler.engine import CrawlerEngine; print('安装成功')"
```

---

## 核心依赖说明

### aiohttp（HTTP 请求引擎）

所有 `type: api` 和 `type: web`（非浏览器模式）任务都通过 aiohttp 发起异步 HTTP 请求。

```bash
pip install aiohttp>=3.9.0
```

**特点**：
- 异步 I/O，支持高并发
- 自动连接池复用
- 支持 HTTP/1.1 和 HTTP/2（需额外安装 `aiohttp[speedups]`）

### BeautifulSoup4 + lxml（HTML 解析引擎）

HTML 页面解析和 CSS 选择器提取字段依赖这两个库。

```bash
pip install beautifulsoup4>=4.12.0 lxml>=5.0.0
```

**注意**：Windows 下安装 `lxml` 如果失败，可以从 [PyPI lxml 页面](https://pypi.org/project/lxml/#files) 下载预编译的 `.whl` 文件安装。

### APScheduler（定时调度）

```bash
pip install apscheduler>=3.10.0
```

系统使用 `AsyncIOScheduler`，基于 asyncio 事件循环。支持标准 5 位 cron 表达式。

### PyYAML（配置解析）

```bash
pip install pyyaml>=6.0
```

所有任务配置文件均为 YAML 格式。`pyyaml` 同时用于配置加载和 Web UI 的 YAML 导出。

### cachetools（内存去重）

```bash
pip install cachetools>=5.3.0
```

系统使用 `TTLCache` 实现 5 分钟内的 URL 内存去重，避免对同一 URL 的短时间重复请求。

---

## 可选依赖

### Playwright（浏览器动态页面采集）

当任务配置了 `browser` 块时需要的浏览器引擎。

```bash
# 1. 安装 Playwright Python 包
pip install playwright

# 2. 下载 Chromium 浏览器（约 182 MB）
playwright install chromium
```

**系统要求**：

| 操作系统 | 额外依赖 |
|----------|----------|
| **Ubuntu / Debian** | `sudo apt install libnspr4 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libgbm1 libasound2` |
| **CentOS / RHEL** | `sudo yum install nspr at-spi2-atk cups-libs libdrm libxkbcommon mesa-libgbm alsa-lib` |
| **Windows** | 无需额外依赖 |
| **macOS** | 无需额外依赖 |

**验证**：

```bash
python -c "from playwright.sync_api import sync_playwright; print('Playwright 就绪')"
```

**云服务器安装注意事项**：

云服务器通常缺少图形库。运行以下命令后重试 `playwright install chromium`：

```bash
# Ubuntu/Debian
playwright install-deps chromium

# 或手动安装
sudo apt update
sudo apt install -y libnss3 libatk-bridge2.0-0 libcups2 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
  libgbm1 libpango-1.0-0 libcairo2 libasound2
```

### akshare（A股/金融数据 SDK）

当任务配置了 `type: sdk` 且 `provider.module: "akshare"` 时需要。

```bash
pip install akshare>=1.18.64
```

---

## 配置任务

### 任务目录结构

```
config/tasks/
├── example_news.yaml          # 网页采集示例
├── example_api.yaml           # JSON API 示例
├── stocks_daily_kline.yaml    # SDK 采集示例
└── my_task.yaml               # 你的自定义任务
```

### 创建第一个任务

复制示例文件修改：

```bash
cp config/tasks/example_api.yaml config/tasks/my_first_task.yaml
```

编辑 `my_first_task.yaml`，修改 `url`、`target_table`、`parser.fields` 等配置。

完整配置说明请参考：[任务配置文档](task_config_guide.md)

### 数据库配置

系统默认使用 SQLite，数据库文件路径可通过命令行指定：

```bash
# 默认路径（项目根目录）
python main.py

# 指定路径
python main.py --db /data/crawler.db
```

---

## 启动系统

### 测试单个任务

```bash
# 运行指定任务一次，查看结果后退出
python main.py --run-once "任务名称"

# 开启 DEBUG 日志
python main.py --run-once "任务名称" --log-level DEBUG
```

### 启动定时调度

```bash
# 使用默认配置（config/tasks/*.yaml）
python main.py

# 指定配置目录
python main.py --config /path/to/tasks

# 自定义数据库路径
python main.py --db /data/crawler.db
```

### 查看运行日志

```
2026-07-20 13:00:00 [INFO] main: 数据库已就绪: crawler.db
2026-07-20 13:00:00 [INFO] task_manager.loader: 业务表 'my_table' 已就绪
2026-07-20 13:00:00 [INFO] task_manager.loader: 任务注册成功: 我的任务 (id=1)
2026-07-20 13:00:00 [INFO] scheduler.scheduler: 调度器已启动，共 3 个任务
```

---

## 云服务器部署

### 方案一：systemd 服务（推荐 Linux）

创建服务文件 `/etc/systemd/system/crawler.service`：

```ini
[Unit]
Description=Python Crawler System
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

启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable crawler
sudo systemctl start crawler
sudo systemctl status crawler
```

### 方案二：Crontab（简单定时）

如果不想用 APScheduler 的常驻进程，也可以用系统 crontab 配合 `--run-once`：

```bash
# 每小时执行一次新闻采集
0 * * * * cd /home/user/everything && .venv/bin/python main.py --run-once "新闻采集" >> logs/crawler.log 2>&1

# 每天凌晨2点执行股票数据采集
0 2 * * * cd /home/user/everything && .venv/bin/python main.py --run-once "A股日线数据" >> logs/crawler.log 2>&1
```

### 方案三：Docker（容器化部署）

```dockerfile
FROM python:3.11-slim

RUN pip install --no-cache-dir \
    aiohttp beautifulsoup4 lxml apscheduler pyyaml cachetools

WORKDIR /app
COPY . .

CMD ["python", "main.py"]
```

```bash
docker build -t crawler .
docker run -d --name crawler \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/data:/app/data \
  crawler
```

---

## 常见问题

### Q: Windows 下安装 lxml 失败

**错误**：`error: Microsoft Visual C++ 14.0 is required`

**解决**：从 [PyPI lxml](https://pypi.org/project/lxml/#files) 下载对应 Python 版本的 `.whl` 文件，然后手动安装：

```bash
pip install lxml-6.1.1-cp311-cp311-win_amd64.whl
```

### Q: `playwright install chromium` 下载速度太慢

**解决**：手动下载 Chromium 并指定路径：

```bash
# 设置环境变量跳过下载
set PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1  # Windows
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1  # Linux/macOS

pip install playwright

# 手动下载浏览器到指定目录
python -m playwright install chromium
```

### Q: 云服务器上 `playwright install chromium` 失败

**错误**：缺少系统依赖库

**解决**：

```bash
# Ubuntu/Debian
playwright install-deps chromium

# 或手动
sudo apt update && sudo apt install -y \
  libnss3 libnspr4 libatk-bridge2.0-0 libcups2 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
  libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0
```

### Q: 如何查看数据库中的采集数据？

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

### Q: 如何更改日志级别？

```bash
# 运行时指定
python main.py --log-level DEBUG

# 参数选项: DEBUG, INFO, WARNING, ERROR
```

### Q: 如何确认任务配置是否正确？

```bash
# 运行一次并查看详细日志
python main.py --run-once "任务名称" --log-level DEBUG
```

如果看到 `新增记录 #1: https://...` 表示配置正确，数据已成功写入。