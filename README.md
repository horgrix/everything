# 轻量级 Python 爬虫系统

> 一个零代码扩展的轻量级 Python 爬虫框架，支持 HTTP API、动态浏览器页面、SDK 数据源、本地文件、外部数据库等多种采集场景。

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## 目录

1. [架构概览](#架构概览)
2. [快速开始](#快速开始)
3. [项目结构](#项目结构)
4. [核心组件](#核心组件)
5. [数据源体系](#数据源体系)
6. [数据流水线](#数据流水线)
7. [配置体系](#配置体系)
8. [API 接口](#api-接口)
9. [前端面板](#前端面板)
10. [配置速查](#配置速查)
11. [开发与测试](#开发与测试)
12. [配置文档](#配置文档)

---

## 架构概览

```
┌──────────────────────────────────────────────────────────────┐
│                       main.py                                 │
│              (CLI 入口 / 调度启动 / run-once)                  │
└──────────────────────┬───────────────────────────────────────┘
                       │
              ┌────────▼────────┐
              │    app.py       │  create_app() 依赖注入工厂
              │   App 组装层    │  统一组件装配
              └────────┬────────┘
                       │
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ CrawlSched-  │ │  TaskLoader  │ │   Database   │
│ uler         │ │  YAML→DB 注册 │ │   SQLite+WAL │
│ APScheduler  │ │              │ │              │
└──────┬───────┘ └──────────────┘ └──────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│                     CrawlerEngine                              │
│           编排层 — 展开 → 获取 → 输出 → 写入                    │
├──────────────────────────────────────────────────────────────┤
│  ┌──────────────────────┐    ┌────────────────────────────┐  │
│  │    SourceRegistry    │    │       DataPipeline         │  │
│  │  (数据源注册表)      │    │ parse → clean → inject → DB│  │
│  │                      │    │                            │  │
│  │  HttpSource (api/web)│    │  Parser  │  Cleaner        │  │
│  │  BrowserSource       │    │  (4模式) │  (20+清洗规则)  │  │
│  │  SdkSource           │    │          │                 │  │
│  │  FileSource (csv/xls)│    │  URLTemplate (动态变量)    │  │
│  │  DbSource (sqlite/my)│    └────────────────────────────┘  │
│  └──────────────────────┘                                     │
└──────────────────────────────────────────────────────────────┘
                              │
                     ┌────────▼────────┐
                     │   SQLite DB     │
                     │  ├ crawl_tasks  │
                     │  ├ crawl_logs   │
                     │  ├ dedup_log    │
                     │  └ 业务表(N)    │
                     └─────────────────┘
```

### 设计原则

| 原则 | 实现 |
|------|------|
| **零代码扩展** | 新增采集目标只需写一个 YAML 文件 |
| **数据源可插拔** | 统一 `DataSource` 接口，通过 `SourceRegistry` 注册 |
| **强类型配置** | `TaskConfig` dataclass 提供类型安全 + dict 兼容 |
| **依赖注入** | `create_app()` 工厂手写装配，无运行时隐蔽依赖 |
| **异步优先** | aiohttp / Playwright 原生 async；同步 SDK 调用通过 `asyncio.to_thread()` 包装 |

---

## 快速开始

### 安装

```bash
# 基础依赖
pip install -r requirements.txt

# 如需浏览器模式（动态页面采集）
playwright install chromium
```

### 第一个任务

1. 在 `config/tasks/user_trigger/` 下创建 `my_first_task.yaml`：

```yaml
name: "我的第一个采集任务"
type: api
trigger_type: user
method: GET
url: "https://jsonplaceholder.typicode.com/posts/1"
outputs:
  - target_table: "my_first_table"
    table_schema:
      columns:
        - name: id
          type: INTEGER
          constraint: PRIMARY KEY AUTOINCREMENT
        - name: post_id
          type: INTEGER NOT NULL
        - name: title
          type: TEXT
        - name: source_url
          type: TEXT NOT NULL
        - name: crawled_at
          type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
      indexes:
        - name: idx_post_id
          columns: [post_id]
          unique: true
    parser:
      type: json
      fields:
        - name: post_id
          path: "id"
        - name: title
          path: "title"
        - name: source_url
          value: "{url}"
```

2. 测试运行：

```bash
python main.py --run-once "我的第一个采集任务"
```

3. 启动定时调度 + Web 面板：

```bash
python main.py --api
```

4. 仅启动 API 服务（无调度器）：

```bash
python -m api --port 8000
```

---

## 项目结构

```
everything/
├── main.py                          # CLI 入口（--run-once / --api / 调度器）
├── app.py                           # 依赖注入工厂 (create_app)
├── requirements.txt                 # Python 依赖
├── README.md                        # 本文件
│
├── crawler/                         # 核心爬虫模块
│   ├── engine.py                    # 引擎编排（展开→获取→输出→写入）
│   ├── pipeline.py                  # 数据流水线（DataPipeline + PipelineResult）
│   ├── parser.py                    # 数据解析（JSON / HTML table / CSS selector / SDK mapping）
│   ├── cleaner.py                   # 数据清洗（20+ 清洗规则 + where 过滤）
│   ├── template.py                  # URL 模板变量解析（{today} / {days_ago:N} 等）
│   ├── dedup.py                     # URL 内存去重（TTLCache）
│   └── sources/                     # 数据源抽象层
│       ├── __init__.py              # 公开 API + create_default_registry()
│       ├── base.py                  # DataSource ABC + SourceRegistry
│       ├── http_source.py           # HTTP 数据源（aiohttp + 重试 + 反反爬）
│       ├── browser_source.py        # 浏览器数据源（Playwright 无头 Chromium）
│       ├── sdk_source.py            # SDK 数据源（动态 import + asyncio.to_thread）
│       ├── file_source.py           # 文件数据源（CSV / Excel）
│       └── db_source.py             # 外部数据库数据源（SQLite / MySQL）
│
├── storage/                         # 数据存储模块
│   ├── database.py                  # SQLite 连接管理 + CRUD（WAL 模式）
│   └── schema.sql                   # 系统表 DDL（crawl_tasks / dedup_log / crawl_logs）
│
├── scheduler/                       # 任务调度模块
│   └── scheduler.py                 # APScheduler 集成 + 热加载支持
│
├── task_manager/                    # 任务管理模块
│   ├── loader.py                    # YAML 配置加载 + 任务注册
│   └── schema.py                    # 强类型配置 dataclass（TaskConfig / OutputConfig 等）
│
├── api/                             # FastAPI HTTP 接口
│   ├── __init__.py                  # create_app() 应用工厂
│   ├── __main__.py                  # python -m api 入口
│   ├── deps.py                      # 共享依赖（get_db / get_scheduler / 验证逻辑）
│   └── routes/
│       ├── tasks.py                 # 任务 CRUD + 手动触发
│       ├── data.py                  # 数据查询 / 写入 / 清空
│       ├── logs.py                  # 执行日志查询
│       └── system.py                # 系统状态 / Dashboard 统计 / 健康检查
│
├── config/
│   ├── data/                        # 离线 CSV 源数据
│   └── tasks/
│       ├── _example_all_features.yaml     # 完整功能示例
│       ├── system_trigger/          # 定时任务（schedule 必填，APScheduler 自动注册）
│       └── user_trigger/            # 手动任务（不允许 schedule，仅 API / --run-once 触发）
│
├── static/                          # Web Dashboard 前端
│   ├── index.html                   # 仪表盘
│   ├── tasks.html                   # 任务管理（YAML 编辑器 / 手动触发 / 热重载）
│   ├── data.html                    # 数据查询（表格 / 筛选 / 清空 / 删除行）
│   ├── logs.html                    # 执行日志
│   ├── css/style.css                # 样式
│   └── js/
│       ├── api.js                   # 前端 API 请求封装
│       └── charts.js               # Chart.js 图表
│
├── tests/                           # 测试
│   ├── conftest.py                  # 共享 fixtures（in-memory DB）
│   ├── test_cleaner.py              # Cleaner 单元测试（21 个）
│   ├── test_template.py             # URLTemplate 测试（11 个）
│   ├── test_sources.py              # SourceRegistry + SDK 标准化测试（8 个）
│   └── test_pipeline.py             # Pipeline 集成测试（6 个）
│
└── docs/                            # 文档
    ├── task_config_guide.md         # 任务配置完整参考（~1400 行）
    ├── install.md                   # 安装指南
    └── changelog.md                 # 变更记录
```

---

## 核心组件

### Engine（引擎编排层）

`crawler/engine.py` — 纯粹的任务编排器，不包含任何解析/清洗/获取逻辑：

```python
class CrawlerEngine:
    def __init__(self, sources: SourceRegistry, pipeline: DataPipeline):
        ...

    async def run(self, task_config, db, url_context=None):
        """
        流水线：iterate 展开 → SourceRegistry.fetch → DataPipeline.process
        """
```

职责：
- **上下文展开**：处理 `iterate` 多值迭代（支持多变量笛卡尔积）
- **数据源路由**：通过 `SourceRegistry` 查找数据源（无 if-else）
- **URL 去重**：内存 TTLCache 去重
- **模板解析**：递归解析 context 中所有 `{变量}` 占位符

### Pipeline（数据转换层）

`crawler/pipeline.py` — 解析 → 清洗 → 过滤 → 注入 → 写入的独立流水线：

```python
class DataPipeline:
    def process(self, raw_data, output_config, db, context) -> PipelineResult:
        """6 步处理：建表 → element_selector → 解析 → 清洗过滤 → source_url → UPSERT"""
```

`PipelineResult` 是不可变 dataclass，支持 `+` 运算符聚合多次输出结果。

### Parser（数据解析器）

`crawler/parser.py` — 统一解析入口，4 种解析模式：

| 模式 | 类型标识 | 用途 | 核心配置 |
|------|----------|------|----------|
| JSON 对象 | `json` | REST API 响应 | `root_path` + `path` 点号路径 |
| JSON 二维数组 | `json` + `array_index_mapping` | `[[val1, val2], ...]` | `position` 索引 |
| HTML 表格 | `html_table` | `<table>` 多行提取 | `row_selector` + `column` |
| CSS 选择器 | `css_selector` | 单元素提取 | `selector` + `attr` |
| SDK/文件映射 | `sdk_mapping` | list[dict] 字段重命名 | `source` → `name` |

特色功能：
- `element_selector` 提取页面级变量注入 context
- `filters` 支持 `tail` / `head` / `skip_lines` / `where` 多条件组合
- 支持 `value` 静态赋值和 `"{url}"` / `"{region}"` 等占位符

### Cleaner（数据清洗器）

`crawler/cleaner.py` — 20+ 清洗规则 + where 条件过滤：

| 类别 | 规则 | 说明 |
|------|------|------|
| 文本 | `strip` / `trim_whitespace` / `remove_html` / `truncate_left` / `truncate_right` | 空白、HTML 标签、截断 |
| 提取 | `regex_extract` / `regex_replace` | 正则捕获/替换 |
| 转换 | `to_number` / `to_datetime` / `number_expr_to_int` | 类型与格式转换 |
| 时间戳 | `ts_floor_to_hour` / `ts_floor_to_day` | 毫秒时间戳取整 |
| 中文数字 | `number_expr_to_int` | "1234.56万" → 12345600、"5.67亿" → 567000000 |
| 日期 | `to_datetime` + `date_format` + `date_output_format` | 自动尝试 12 种常见格式 |
| 过滤 | `where` 条件（`>` / `<` / `>=` / `<=` / `==` / `!=` / `in` / `not_in` / `contains`） | AND 组合过滤 |

### URLTemplate（模板变量）

`crawler/template.py` — 运行时动态变量替换：

| 变量 | 示例输出 | 说明 |
|------|----------|------|
| `{today}` | `2026-08-02` | 当天日期 |
| `{today:%Y%m%d}` | `20260802` | 自定义格式 |
| `{yesterday}` | `2026-08-01` | 昨天 |
| `{now}` | `2026-08-02 15:30:00` | 当前日期时间 |
| `{days_ago:N}` | N 天前的日期 | `{days_ago:7}` |
| `{weeks_ago:N}` | N 周前的日期 | `{weeks_ago:2}` |
| `{this_week:N}` | 本周一~日(N=1~7) | `{this_week:1}` |
| `{last_week:N}` | 上周一~日(N=1~7) | `{last_week:5}` |
| `{timestamp}` | `1753766400` | Unix 秒时间戳 |
| `{timestamp_ms}` | `1753766400000` | Unix 毫秒时间戳 |
| `{task_name}` | 当前任务名 | |
| `{xxx}` | 自定义 | 从 context / params / iterate 变量中查找 |

---

## 数据源体系

所有数据源实现统一 `DataSource` 抽象基类：

```python
class DataSource(ABC):
    @abstractmethod
    async def fetch(self, task_config: dict, context: dict) -> Any:
        """返回原始数据，None 表示跳过"""
        ...
```

通过 `SourceRegistry` 按类型字符串路由，Engine 无需知道具体实现：

```python
registry = SourceRegistry()
registry.register("api", HttpSource())
registry.register("web", HttpSource(browser_source=BrowserSource()))
registry.register("sdk", SdkSource())
registry.register("csv", FileSource())
registry.register("excel", FileSource())
registry.register("db", DbSource())
```

| 数据源 | 类型字符串 | 引擎 | 适用场景 |
|--------|-----------|------|----------|
| `HttpSource` | `api` / `web` | aiohttp 异步 | RESTful API、静态 HTML 页面 |
| `BrowserSource` | `web` + browser 配置 | Playwright 无头 Chromium | JS 渲染页面、需点击/滚动加载 |
| `SdkSource` | `sdk` | importlib + asyncio.to_thread | akshare、tushare 等第三方库 |
| `FileSource` | `csv` / `excel` | csv.DictReader / openpyxl | 本地 CSV/Excel 文件补录 |
| `DbSource` | `db` | sqlite3 / pymysql | 外部 SQLite/MySQL 数据迁移 |

**关键设计决策**：

- `HttpSource` 内置反反爬策略（随机延迟、UA 轮换、代理轮换），消除了独立的 `AntiSpider` 类
- `HttpSource` 持有可选的 `BrowserSource` 引用 —— 当 `task_config.browser` 存在时自动委托
- `SdkSource` / `FileSource` / `DbSource` 的同步调用通过 `asyncio.to_thread()` 包装，不阻塞事件循环
- 可通过 `registy.register()` 注册自定义数据源，无需修改 Engine

---

## 数据流水线

```
                       ┌─────────────────┐
                       │   原始数据       │
                       │ (str / list/dict) │
                       └────────┬────────┘
                                │
                       ┌────────▼────────┐
                       │  DataPipeline   │
                       │  .process()     │
                       └────────┬────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│ 1. 动态建表      │   │ 2. element      │   │ 3. Parser       │
│ ensure_business  │   │    _selector    │   │ parse_rows()    │
│ _table()         │   │ 提取页面级变量   │   │ → list[dict]    │
└─────────────────┘   └─────────────────┘   └────────┬────────┘
                                                     │
                                            ┌────────▼────────┐
                                            │ 4. Cleaner      │
                                            │ clean_batch()    │
                                            │ 清洗 + where过滤  │
                                            └────────┬────────┘
                                                     │
                                            ┌────────▼────────┐
                                            │ 5. source_url   │
                                            │ 注入采集来源URL   │
                                            └────────┬────────┘
                                                     │
                                            ┌────────▼────────┐
                                            │ 6. Database     │
                                            │ insert_business │
                                            │ _records_batch  │
                                            │ ON CONFLICT UPSERT│
                                            └─────────────────┘
```

---

## 配置体系

### 触发类型

任务文件按目录区分触发方式：

```
config/tasks/
├── system_trigger/      ← 定时任务（必须有 schedule，APScheduler 自动注册）
│   └── *.yaml           trigger_type: system
│
└── user_trigger/        ← 手动任务（不允许 schedule）
    └── *.yaml           trigger_type: user  （仅 API POST /{name}/run 或 --run-once 触发）
```

### 类型化配置对象

`task_manager/schema.py` 提供从 YAML dict 到强类型 dataclass 的映射：

```python
from task_manager.schema import TaskConfig

config = TaskConfig.from_dict(yaml_dict)

# 类型安全的属性访问
config.name           # str
config.type           # Literal["api", "web", "sdk", "csv", "excel", "db"]
config.trigger_type   # Literal["system", "user"]
config.schedule       # str | None
config.outputs        # list[OutputConfig]
config.iterate        # list[IterateVar]

# 向后兼容的 dict 访问
config["name"]        # 等价于 config.name
config.get("url")     # 等价于 config.url
```

配置层级：`TaskConfig` → `OutputConfig` → `TableSchema` / `ParserConfig` → `ColumnDef` / `IndexDef` / `FieldConfig`

---

## API 接口

### 端点速查

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/tasks` | 任务列表 |
| `GET` | `/api/tasks/{name}` | 任务详情（含原始 YAML） |
| `POST` | `/api/tasks` | 创建任务（写入文件 + 入数据库 + 热加载） |
| `PUT` | `/api/tasks/{name}` | 更新任务（覆写 YAML + 重注册 + 热重载） |
| `DELETE` | `/api/tasks/{name}` | 删除任务（调度器 + 文件 + 数据库） |
| `POST` | `/api/tasks/{name}/run` | 手动触发单次执行 |
| `GET` | `/api/data/tables` | 业务表列表 |
| `GET` | `/api/data/{table}/columns` | 表结构 |
| `GET` | `/api/data/{table}/query` | 数据查询（where/group_by/order_by/aggregate/filter） |
| `GET` | `/api/data/{table}/count` | 行数统计 |
| `POST` | `/api/data/{table}/rows/batch` | 批量 UPSERT |
| `PUT` | `/api/data/{table}/rows/{col}/{val}` | 更新单行 |
| `DELETE` | `/api/data/{table}/rows/{col}/{val}` | 删除单行 |
| `DELETE` | `/api/data/{table}/rows` | 清空表 |
| `GET` | `/api/logs` | 执行日志（按 task/status 过滤，分页） |
| `GET` | `/api/logs/{id}` | 单条日志详情 |
| `GET` | `/api/system/status` | 系统状态（任务数、表数、DB 大小） |
| `GET` | `/api/system/dashboard` | Dashboard 聚合统计 |
| `GET` | `/api/system/health` | 健康检查 |

### 请求/响应格式

所有响应统一格式：

```json
{"code": 0, "message": "success", "data": {...}, "total": 100}
```

错误响应：

```json
{"code": -1, "message": "错误描述", "data": null}
```

### 启动方式

```bash
# 纯 API 模式（无调度器）
python -m api --port 8080

# API + 调度器
python main.py --api

# API + 调度器 + 自定义端口
python main.py --api --api-port 8888

# API + 调度器 + 自定义 DB
python main.py --api --db my_data.db --config my_tasks/
```

---

## 前端面板

启动 `python main.py --api` 后访问 http://localhost:8000/dashboard/：

| 页面 | 路径 | 功能 |
|------|------|------|
| 仪表盘 | `/dashboard/` | 任务数 / 启用数 / 表数 / 执行趋势图 / 状态分布 |
| 任务管理 | `/dashboard/tasks.html` | 任务列表 / YAML 编辑器 / 创建 / 更新 / 手动触发 |
| 数据查询 | `/dashboard/data.html` | 表选择 / 自定义 SQL 筛选 / 分页 / 行编辑 / 删除 / 清空 |
| 执行日志 | `/dashboard/logs.html` | 日志列表 / 按任务/状态筛选 / 耗时 / 新增更新统计 |

技术栈：Bootstrap 5.3 + Chart.js + 原生 JS（零构建工具）。

---

## 配置速查

| 功能 | 章节 | 核心配置项 |
|------|------|-----------|
| 基础信息 | 见文档 | `name`, `type`, `trigger_type`, `method`, `url`, `schedule` |
| 浏览器模式 | 见文档 | `browser: {headless, wait_selector, actions: [click, scroll, wait], screenshot}` |
| 动态参数 | 见文档 | `{today}`, `{yesterday}`, `{days_ago:N}`, `{timestamp}`, `{timestamp_ms}` |
| 多值迭代 | 见文档 | `iterate: [{var_name, values}]` 支持多变量笛卡尔积 |
| 多表输出 | 见文档 | `outputs: [{target_table, table_schema, parser}]` |
| 表结构 | 见文档 | `table_schema: {columns: [{name, type, constraint}], indexes: [{name, columns, unique}]}` |
| JSON 解析 | 见文档 | `parser.type: json` + `root_path` + `fields[].path` |
| JSON 二维数组 | 见文档 | `array_index_mapping: true` + `fields[].position` |
| HTML 表格 | 见文档 | `parser.type: html_table` + `row_selector` + `fields[].column` / `selector` |
| CSS 选择器 | 见文档 | `parser.type: css_selector` + `fields[].selector` + `attr` + `multiple` |
| SDK 映射 | 见文档 | `parser.type: sdk_mapping` + `fields[].source` |
| 数据过滤 | 见文档 | `parser.filters: {tail, head, skip_lines, where}` |
| 字段清洗 | 见文档 | `strip` / `to_number` / `to_datetime` / `regex_extract` / `regex_replace` / `truncate_*` / `number_expr_to_int` |
| 反反爬 | 见文档 | `anti_spider: {enabled, delay, rotate_user_agent, proxies}` |
| 重试 | 见文档 | `retry: {max_attempts, backoff_base}` 指数退避 |
| SDK 调用 | 见文档 | `type: sdk` + `provider: {module, function, params}` |
| CSV/Excel | 见文档 | `type: csv/excel` + `file: {format, path, encoding, sheet_name}` |
| 外部数据库 | 见文档 | `type: db` + `db: {type, query, path/host/port/database}` |

完整配置说明请参考：**[docs/task_config_guide.md](docs/task_config_guide.md)**

---

## 开发与测试

### 运行测试

```bash
# 安装测试依赖
pip install pytest pytest-asyncio pytest-mock

# 运行全部测试（46 个）
pytest tests/ -v

# 运行特定模块
pytest tests/test_cleaner.py -v
pytest tests/test_pipeline.py -v
```

### 测试覆盖

| 测试文件 | 测试数 | 覆盖模块 |
|----------|--------|----------|
| `test_cleaner.py` | 21 | Cleaner 全部清洗规则 + 过滤条件 + 批量处理 |
| `test_template.py` | 11 | URLTemplate 全部 12 种变量类型 |
| `test_sources.py` | 8 | SourceRegistry 注册/查找 + SDK 数据标准化 |
| `test_pipeline.py` | 6 | PipelineResult 运算 + Pipeline JSON/SDK mapping/source_url 集成 |

### 核心原则

| 原则 | 说明 |
|------|------|
| **Pipeline 无状态** | 不使用类属性 / 全局变量 |
| **同步 I/O 走线程** | SDK/文件/DB 读取通过 `asyncio.to_thread()` 包装 |
| **配置不可变** | `TaskConfig` 等 dataclass 只读，修改产生新副本 |
| **Database 连接管理** | WAL 模式 + busy_timeout 5s |
| **不修改 YAML 配置格式** | 新增属性保持默认值，旧配置始终可用 |
| **所有数据源异步返回** | `async def fetch()` 统一接口 |

### 代码统计

| 模块 | 文件数 | 总行数 |
|------|--------|--------|
| `crawler/` 核心 | 11 | ~1200 |
| `crawler/sources/` 数据源 | 7 | ~700 |
| `api/` 接口层 | 6 | ~550 |
| `storage/` 存储 | 2 | ~320 |
| `scheduler/` + `task_manager/` | 3 | ~350 |
| `app.py` 组装 | 1 | ~90 |
| `tests/` 测试 | 5 | ~250 |
| `static/` 前端 | 6 | ~800 |
| **合计** | **~40** | **~4200** |

---

## License

MIT
