# 轻量级 Python 爬虫系统

> 一个零代码扩展的轻量级 Python 爬虫框架，支持 HTTP API、动态浏览器页面、SDK 数据源等多种采集场景。

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## 架构概览

```
┌──────────────────────────────────────────────────────────────┐
│                       main.py                                │
│             (命令行入口 / 调度器启动)                         │
└──────────────────────┬───────────────────────────────────────┘
                       │
              ┌────────▼────────┐
              │  Scheduler      │    根据 cron 表达式定时触发
              │  (APScheduler)  │
              └────────┬────────┘
                       │
              ┌────────▼────────┐
              │  TaskLoader     │    扫描 config/tasks/*.yaml
              │  (YAML → Dict)  │    自动建表 / 注册任务
              └────────┬────────┘
                       │
              ┌────────▼───────────────────────────────────────┐
              │              CrawlerEngine                      │
              │  ┌─────────┐  ┌──────────┐  ┌──────────────┐  │
              │  │ Fetcher  │  │  Parser   │  │   Cleaner    │  │
              │  │ (aiohttp)│  │ (多模式)  │  │ (数据清洗)   │  │
              │  └─────────┘  └──────────┘  └──────────────┘  │
              │  ┌─────────┐  ┌──────────┐  ┌──────────────┐  │
              │  │ Browser │  │  Filters  │  │   AntiSpider │  │
              │  │(Playwrt)│  │ (去重/过滤)│  │  (反反爬)    │  │
              │  └─────────┘  └──────────┘  └──────────────┘  │
              └──────────────────────┬────────────────────────┘
                                     │
              ┌──────────────────────▼────────────────────────┐
              │              SQLite Database                   │
              │  ┌─────────────┐  ┌──────────────────────────┐│
              │  │ crawl_tasks │  │  动态创建的业务表         ││
              │  │ crawl_logs  │  │  (自动建表 / 建索引)      ││
              │  │ dedup_log   │  │                           ││
              │  └─────────────┘  └──────────────────────────┘│
              └───────────────────────────────────────────────┘
```

## 核心概念

### 零代码扩展

新增一个采集目标只需写一个 YAML 文件。系统自动完成：建表、建索引、任务注册、定时调度。

### 数据流水线

```
URL / SDK → Fetcher → Parser → Cleaner → Filters → Database
```

| 阶段 | 职责 | 组件 |
|------|------|------|
| 请求 | HTTP / 浏览器 / SDK 调用 | `fetcher.py` / `fetcher_browser.py` / `sdk_provider.py` |
| 解析 | JSON / HTML / SDK 字段映射 | `parser.py` |
| 清洗 | 去空白、正则提取、类型转换 | `cleaner.py` |
| 过滤 | tail / head / where | `parser.py` (filters) |
| 存储 | 批量 UPSERT | `database.py` |

### 支持的数据源

| 类型 | YAML type | 引擎 | 适用场景 |
|------|-----------|------|----------|
| JSON API | `api` | aiohttp | RESTful 接口、公开 API |
| 静态网页 | `web` | aiohttp | 简单 HTML 页面 |
| 动态网页 | `web` + `browser` | Playwright | JS 渲染页面、需点击加载 |
| SDK 调用 | `sdk` | importlib | akshare、tushare 等 |

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

1. 在 `config/tasks/` 下创建 `my_task.yaml`：

```yaml
name: "我的第一个采集任务"
type: api
method: GET
url: "https://jsonplaceholder.typicode.com/posts/1"
schedule: "0 */6 * * *"
target_table: "my_table"
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

3. 启动定时调度：

```bash
python main.py
```

---

## 项目结构

```
everything/
├── main.py                     # 入口（命令行参数、调度启动）
├── requirements.txt             # Python 依赖
├── README.md                   # 本文件
│
├── crawler/                    # 核心爬虫模块
│   ├── engine.py               # 爬虫引擎（流水线编排）
│   ├── fetcher.py              # HTTP 请求（aiohttp + 重试）
│   ├── fetcher_browser.py      # 浏览器请求（Playwright）
│   ├── parser.py               # 数据解析（JSON/HTML/SDK）
│   ├── cleaner.py              # 数据清洗（正则/类型转换）
│   ├── anti_spider.py          # 反反爬策略
│   ├── dedup.py                # URL 内存去重
│   ├── template.py             # URL 模板变量解析
│   └── sdk_provider.py         # SDK 调用封装
│
├── storage/                    # 数据存储模块
│   ├── database.py             # SQLite 连接管理 + CRUD
│   └── schema.sql             # 系统表 DDL
│
├── scheduler/                  # 任务调度模块
│   └── scheduler.py            # APScheduler 集成
│
├── task_manager/               # 任务管理模块
│   └── loader.py               # YAML 配置加载 + 任务注册
│
├── config/tasks/               # 任务配置文件
│   ├── example_news.yaml       # 网页采集示例
│   ├── example_api.yaml        # JSON API 示例
│   ├── example_api_reference.yaml # 完整 JSON API 参考
│   ├── stocks_daily_kline.yaml # SDK (akshare) 示例
│   ├── steam_peak_players.yaml # JSON 二维数组 + 迭代示例
│   ├── steam_peak_players_monthly.yaml # HTML 表格解析示例
│   ├── steam_palyer_review.yaml # 多表输出示例
│   ├── steam_best_seller_list_hourly.yaml # 浏览器动态页面示例
│   └── hk_market_liquidity_daily.yaml # 数组展开示例
│
└── docs/                       # 文档
    └── task_config_guide.md    # 任务配置完整说明
```

## 数据流图

```
                     ┌──────────────┐
                     │  Scheduler   │  定时触发
                     └──────┬───────┘
                            │
                     ┌──────▼───────┐
                     │  TaskLoader  │  加载 YAML 配置
                     └──────┬───────┘
                            │
              ┌─────────────▼─────────────┐
              │     CrawlerEngine.run()   │
              │  路由: sdk / outputs /    │
              │  iterate / single         │
              └─────────────┬─────────────┘
                            │
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
  │ Fetcher     │    │ Browser     │    │ SDKProvider │
  │ (aiohttp)   │    │ (Playwright)│    │ (动态调用)  │
  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
         │                  │                  │
         └──────────┬───────┴──────────────────┘
                    ▼
            ┌─────────────┐
            │   Parser    │  解析为 list[dict]
            │ parse_rows()│
            └──────┬──────┘
                   ▼
            ┌─────────────┐
            │   Cleaner   │  清洗每个字段
            └──────┬──────┘
                   ▼
            ┌─────────────┐
            │   Filters   │  tail/head/where
            └──────┬──────┘
                   ▼
            ┌─────────────┐
            │  Database   │  UPSERT 批量写入
            │ batch insert│
            └─────────────┘
```

## 配置文档

完整配置说明请参考：[docs/task_config_guide.md](docs/task_config_guide.md)

### 配置速查

| 章节 | 内容 |
|------|------|
| 基础配置 | name, type, method, url, schedule |
| 浏览器模式 | Playwright 无头浏览器，等待/点击/滚动 |
| 动态参数 | `{today}`, `{yesterday}`, `{days_ago:N}` |
| 多值迭代 | `iterate` 遍历多个参数值 |
| 多表输出 | `outputs` 一次请求写入多张表 |
| 表结构 | columns (INTEGER/REAL/TEXT), indexes |
| JSON 解析 | path / root_path / array_index_mapping |
| HTML 解析 | css_selector / html_table + column + selector |
| SDK 映射 | source → name 字段映射 |
| 数据过滤 | tail, head, where (>, <, >=, <=, ==, !=, in, contains) |
| 字段清洗 | strip, truncate_left/right, to_number, to_datetime, regex_extract |

---

## License

MIT