# 爬虫任务配置文件说明文档

> 版本：v2.0 | 最后更新：2026-08-03
>
> 对应代码版本：重构后（注册表模式 Cleaner/Parser + 统一 DataSource 抽象层 + outputs 多表架构）

---

## 目录

- [一、核心概念](#一核心概念)
  - [任务文件放置规则](#任务文件放置规则)
  - [定时任务 vs 手动任务](#定时任务-vs-手动任务)
  - [数据流水线](#数据流水线)
  - [数据源类型速查](#数据源类型速查)
- [二、最小可运行任务](#二最小可运行任务)
  - [API 任务](#api-任务)
  - [文件补录任务](#文件补录任务)
- [三、顶层配置字段](#三顶层配置字段)
  - [name](#name)
  - [type / method / url](#type--method--url)
  - [trigger_type / schedule](#trigger_type--schedule)
  - [encoding](#encoding)
  - [params](#params)
- [四、outputs — 输出目标](#四outputs--输出目标)
  - [单表输出](#单表输出)
  - [多表输出（从一次请求拆出多张表）](#多表输出从一次请求拆出多张表)
- [五、table_schema — 表结构定义](#五table_schema--表结构定义)
  - [columns](#columns)
  - [indexes](#indexes)
  - [crawled_at 列约定](#crawled_at-列约定)
- [六、parser — 解析配置](#六parser--解析配置)
  - [JSON 解析](#json-解析)
  - [JSON 二维数组（array_index_mapping）](#json-二维数组array_index_mapping)
  - [HTML 表格（html_table）](#html-表格html_table)
  - [HTML 表格 + element_selector 页面级变量](#html-表格--element_selector-页面级变量)
  - [SDK/文件字段映射（sdk_mapping）](#sdk文件字段映射sdk_mapping)
  - [parser 级过滤：filters](#parser-级过滤filters)
- [七、字段提取方式对照表](#七字段提取方式对照表)
- [八、字段清洗规则](#八字段清洗规则)
- [九、字段级过滤 where](#九字段级过滤-where)
- [十、动态参数模板](#十动态参数模板)
- [十一、iterate — 多值迭代](#十一iterate--多值迭代)
- [十二、浏览器动态页面 browser](#十二浏览器动态页面-browser)
- [十三、数据源专项配置](#十三数据源专项配置)
  - [SDK 调用（type: sdk）](#sdk-调用type-sdk)
  - [CSV / Excel 文件读取（type: csv / excel）](#csv--excel-文件读取type-csv--excel)
  - [外部数据库查询（type: db）](#外部数据库查询type-db)
- [十四、anti_spider — 反反爬](#十四anti_spider--反反爬)
- [十五、retry — 重试策略](#十五retry--重试策略)
- [十六、完整实战示例](#十六完整实战示例)
  - [示例一：JSON API + root_path 数组展开 + 动态日期](#示例一json-api--root_path-数组展开--动态日期)
  - [示例二：HTML 表格 + element_selector + 日期清洗](#示例二html-表格--element_selector--日期清洗)
  - [示例三：JSON 二维数组 + iterate + 时间戳取整](#示例三json-二维数组--iterate--时间戳取整)
  - [示例四：多表输出 + 反反爬](#示例四多表输出--反反爬)
  - [示例五：SDK 调用 + sdk_mapping](#示例五sdk-调用--sdk_mapping)
  - [示例六：浏览器动态页面 + 多值迭代 + 正则提取](#示例六浏览器动态页面--多值迭代--正则提取)
  - [示例七：外部 SQLite 查询 + sdk_mapping 透传](#示例七外部-sqlite-查询--sdk_mapping-透传)
- [十七、常见问题](#十七常见问题)
- [十八、附录：清洗规则全矩阵](#十八附录清洗规则全矩阵)
- [十九、附录：模板变量全矩阵](#十九附录模板变量全矩阵)

---

## 一、核心概念

### 任务文件放置规则

```
config/tasks/
├── system_trigger/     ← 定时任务，系统启动后 APScheduler 自动调度
│   └── *.yaml
└── user_trigger/       ← 手动任务，只能通过 API 或 --run-once 触发
    └── *.yaml
```

**文件名任意**，系统只读文件内容。文件可以包含单个任务（一个 dict）或多个任务（一个 list）。

### 定时任务 vs 手动任务

| | system（定时） | user（手动） |
|---|---|---|
| `trigger_type` | `system` | `user` |
| `schedule` | **必填** | **禁止出现** |
| 存放目录 | `system_trigger/` | `user_trigger/` |
| 触发方式 | 按 cron 自动执行 | `POST /api/tasks/{name}/run` 或 `--run-once` |

**目录与 trigger_type 必须一致**——如果 trigger_type 写了 `system` 但文件放在了 `user_trigger/` 下，系统会报错拒绝加载。

### 数据流水线

```
URL / SDK / 文件 / 数据库
         │
         ▼
    DataSource.fetch()      ← 按 type 路由到对应数据源
         │
         ▼
    DataPipeline.process()  ← 对每个 output 执行：
         │                    1. 建表（如果不存在）
         ├─── element_selector 2. 提取页面级变量注入 context
         ├─── Parser           3. 解析原始数据 → list[dict]
         ├─── Cleaner          4. 清洗每个字段 + where 过滤
         ├─── source_url       5. 注入采集来源 URL
         └─── Database         6. ON CONFLICT UPSERT 批量写入
```

### 数据源类型速查

| type | 引擎 | 何时使用 | 需配置的专属块 |
|------|------|----------|---------------|
| `api` | aiohttp | RESTful JSON API | `url` |
| `web` | aiohttp | 静态 HTML 页面 | `url` |
| `web` + `browser` | Playwright | JS 渲染的动态页面 | `url` + `browser` |
| `sdk` | importlib | akshare/tushare 等第三方库 | `provider` |
| `csv` | csv.DictReader | CSV 文件补录 | `file` |
| `excel` | openpyxl | Excel 文件补录 | `file` |
| `db` | sqlite3 / pymysql | 从其他数据库迁移数据 | `db` |

---

## 二、最小可运行任务

下面两个任务可以直接复制、保存、运行。

### API 任务

将以下内容保存为 `config/tasks/user_trigger/hello_api.yaml`：

```yaml
name: "API 快速开始"
type: api
trigger_type: user
method: GET
url: "https://jsonplaceholder.typicode.com/posts/1"
outputs:
  - target_table: "hello_api"
    table_schema:
      columns:
        - name: id
          type: INTEGER
          constraint: PRIMARY KEY AUTOINCREMENT
        - name: post_id
          type: INTEGER NOT NULL
        - name: title
          type: TEXT
        - name: body
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
          to_number: true
        - name: title
          path: "title"
        - name: body
          path: "body"
        - name: source_url
          value: "{url}"
```

运行：

```bash
python main.py --run-once "API 快速开始"
```

输出类似：

```
任务 'API 快速开始' 执行结果:
  新增: 1
  更新: 0
  跳过: 0
  耗时: 0.8s
```

验证数据已写入：

```bash
python -c "import sqlite3; conn=sqlite3.connect('crawler.db'); conn.row_factory=sqlite3.Row; \
  rows=conn.execute('SELECT post_id, title FROM hello_api').fetchall(); \
  [print(dict(r)) for r in rows]"
```

### 文件补录任务

将以下内容保存为 `config/tasks/user_trigger/hello_csv.yaml`：

```yaml
name: "CSV 快速开始"
type: csv
trigger_type: user
file:
  format: csv
  path: "config/data/steam_best_seller_list_hourly_his_20260101-20260730.csv"
  encoding: utf-8-sig
outputs:
  - target_table: "hello_csv"
    table_schema:
      columns:
        - name: id
          type: INTEGER
          constraint: PRIMARY KEY AUTOINCREMENT
        - name: steam_id
          type: INTEGER
        - name: rank
          type: INTEGER
        - name: region
          type: TEXT
        - name: crawled_at
          type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
      indexes:
        - name: idx_uk
          columns: [steam_id, region, crawled_at]
          unique: true
    parser:
      type: sdk_mapping
      fields:
        - name: steam_id
          source: steam_id
          to_number: true
        - name: rank
          source: rank
          to_number: true
        - name: region
          source: region
```

运行：

```bash
python main.py --run-once "CSV 快速开始"
```

---

## 三、顶层配置字段

### name

| 属性 | 说明 |
|------|------|
| 类型 | `string` |
| 必填 | **是** |
| 唯一 | 是，任务名不可重复 |

任务唯一标识。用于日志输出、`--run-once` 指定执行、API 端点路由。

```yaml
name: "Steam 玩家峰值数据采集"
```

### type / method / url

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `type` | string | **是** | — | `api` / `web` / `sdk` / `csv` / `excel` / `db` |
| `method` | string | 否 | `GET` | HTTP 方法，仅 `api` / `web` 有效 |
| `url` | string | `api`/`web` 必填 | — | 请求目标 URL，支持模板变量 |

```yaml
type: api
method: GET
url: "https://api.example.com/v2/data?date={today}&from={yesterday}"
```

### trigger_type / schedule

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `trigger_type` | string | **是** | `system` 或 `user` |
| `schedule` | string | system 必填 / user 禁止 | 5 位 cron 表达式：`分 时 日 月 星期` |

**cron 表达式速查：**

| 表达式 | 含义 |
|--------|------|
| `"35 * * * *"` | 每小时第 35 分 |
| `"20 */4 * * *"` | 每 4 小时的第 20 分 |
| `"0 8 * * *"` | 每天 8:00 |
| `"0 20 * * 1-5"` | 工作日 20:00 |
| `"35 8 2 * *"` | 每月 2 号 8:35 |
| `"30 19 * * *"` | 每天 19:30 |

```yaml
# 定时任务
trigger_type: system
schedule: "0 8 * * *"

# 手动任务
trigger_type: user
# 不能写 schedule 字段
```

### encoding

| 类型 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `string` | 否 | `utf-8` | HTTP 响应的字符编码 |

当目标网站使用非 UTF-8 编码时指定（如香港交易所 Big5 页面）。

```yaml
type: web
url: "https://www.hkex.com.hk/chi/stat/smstat/ssturnover/ncms/ashtmain_c.htm"
encoding: 'big5hkscs'
```

### params

| 类型 | 必填 | 说明 |
|------|------|------|
| `dict` | 否 | 全局参数字典，注入到模板变量上下文 |

`params` 中定义的键值对会自动成为模板变量，可在 URL 和 parser 的 `value` 占位符中使用。

```yaml
params:
  api_key: "abc123"
  source: "production"

url: "https://api.example.com/data?key={api_key}&source={source}"
```

---

## 四、outputs — 输出目标

每个任务必须有一个或多个 `outputs`。每个 output 定义一张目标表、其表结构和解析方式。

> **注意**：当前架构不再使用旧版的顶层 `target_table` / `parser` / `table_schema` 字段。所有配置必须在 `outputs` 列表中。

### 单表输出

一次请求的结果写入一张表：

```yaml
outputs:
  - target_table: "my_table"
    table_schema:
      columns: [...]
      indexes: [...]
    parser:
      type: json
      fields: [...]
```

### 多表输出（从一次请求拆出多张表）

当 API 返回的 JSON 包含多个独立数据集时，用 `root_path` 分别定位后写入不同表。**只发起一次 HTTP 请求。**

```yaml
type: api
url: "https://store.steampowered.com/appreviewhistogram/{steam_id}"

outputs:
  - target_table: "review_recent"
    table_schema: {...}
    parser:
      type: json
      root_path: "results.recent"     # 从同一份 JSON 中取 'recent' 数组
      fields:
        - name: steam_id
          value: "{steam_id}"
        - name: up
          path: "recommendations_up"

  - target_table: "review_rollup"
    table_schema: {...}
    parser:
      type: json
      root_path: "results.rollups"    # 从同一份 JSON 中取 'rollups' 数组
      fields:
        - name: steam_id
          value: "{steam_id}"
        - name: up
          path: "recommendations_up"
```

---

## 五、table_schema — 表结构定义

### columns

每个 output 的 `table_schema.columns` 定义该业务表的所有列。系统首次运行时自动建表。

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 列名 |
| `type` | string | 是 | SQLite 类型：`INTEGER` / `REAL` / `TEXT` |
| `constraint` | string | 否 | 约束表达式，空格分隔多个约束 |

**SQLite 类型 → Python 类型对应：**

| SQLite 类型 | Python 类型 | 存储内容 |
|-------------|-------------|----------|
| `INTEGER` | `int` | 整数、时间戳毫秒 |
| `REAL` | `float` | 浮点数、金额 |
| `TEXT` | `str` | 文本、日期字符串 |

**推荐约束写法：**

```yaml
columns:
  - name: id
    type: INTEGER
    constraint: PRIMARY KEY AUTOINCREMENT   # 自增主键，推荐每个表都配一个

  - name: code
    type: TEXT NOT NULL                      # 不允许空值

  - name: crawled_at
    type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))   # 自动时间戳
```

### indexes

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 索引名称 |
| `columns` | list[string] | 是 | 包含的列，按顺序 |
| `unique` | boolean | 否 | 是否唯一索引（默认 `false`） |

**唯一索引 = 数据库级去重**。重复行会触发 `ON CONFLICT DO UPDATE`，自动更新已有记录。这是系统唯一的去重机制。

```yaml
indexes:
  # 单列唯一索引 — 如：每个 stock code 只保留一条
  - name: idx_code
    columns: [code]
    unique: true

  # 联合唯一索引 — 如：同一个游戏在同一小时只保留一条
  - name: idx_uk_steam_id_stat_ts
    columns: [steam_id, stat_ts]
    unique: true

  # 普通索引 — 加速查询
  - name: idx_date
    columns: [trade_date]
```

### crawled_at 列约定

建议每个业务表都包含一行采集时间列：

```yaml
- name: crawled_at
  type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
```

你也可以根据需要自定义粒度和函数：

```yaml
# 精确到小时
- name: crawled_at
  type: TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H', 'now', 'localtime'))

# 精确到天
- name: crawled_at
  type: TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d', 'now', 'localtime'))
```

---

## 六、parser — 解析配置

parser 的 `type` 决定如何从原始数据中提取行：

| type | 原始数据格式 | row_selector / root_path | 字段定位方式 |
|------|-------------|--------------------------|-------------|
| `json` | JSON 字符串 | `root_path`（可选） | `path`（点号路径） |
| `html_table` | HTML 页面 | `row_selector`（CSS 选择器） | `column`（td 索引）或 `selector` |
| `sdk_mapping` | 已是 list[dict] | 无需 — 直接透传 | `source`（原始字段名） |

### JSON 解析

#### 单对象模式（无 root_path）

API 返回一个对象（或本身就是数组）：

```json
{"userId": 1, "id": 1, "title": "Hello World", "body": "..."}
```

```yaml
parser:
  type: json
  fields:
    - name: post_id
      path: "id"
      to_number: true
    - name: title
      path: "title"
    - name: body
      path: "body"
```

**path 点号路径导航：**

| path | 对以下 JSON 取到的值 |
|------|---------------------|
| `"id"` | 顶层字段 |
| `"data.title"` | 嵌套对象字段 |
| `"items.0.name"` | 数组第一个元素的 name |

#### 数组展开模式（含 root_path）

API 返回 `{"header": ..., "result": {"records": [...]}}`，需要定位到嵌套数组：

```json
{
  "result": {
    "records": [
      {"end_of_date": "2026-08-01", "cu_weakside": 7.85},
      {"end_of_date": "2026-07-31", "cu_weakside": 7.86}
    ]
  }
}
```

```yaml
parser:
  type: json
  root_path: "result.records"     # 先定位到数组
  fields:
    - name: end_of_date
      path: "end_of_date"         # path 相对于数组内每个元素
    - name: cu_weakside
      path: "cu_weakside"
      to_number: true
```

### JSON 二维数组（array_index_mapping）

API 返回 `[[ts1, val1], [ts2, val2], ...]`——每个元素是数组而不是对象：

```json
[
  [1664582400000, 20954],
  [1667260800000, 18549]
]
```

```yaml
parser:
  type: json
  root_path: ""                    # JSON 本身已经是数组
  array_index_mapping: true        # 启用按位置索引
  fields:
    - name: steam_id
      value: "{steam_id}"          # 配合 iterate 使用
    - name: stat_ts
      position: 0                  # 数组中第 0 个位置
      to_number: true
      ts_floor_to_hour: true
    - name: peak_players
      position: 1                  # 数组中第 1 个位置
```

### HTML 表格（html_table）

通过 CSS 选择器定位每一行，再用 `column` 取对应的 `<td>` / `<th>`：

```html
<table class="common-table">
  <tbody>
    <tr><td>2026-07</td><td>12,345</td><td>18,000</td></tr>
    <tr><td>2026-06</td><td>11,000</td><td>15,000</td></tr>
  </tbody>
</table>
```

```yaml
parser:
  type: html_table
  row_selector: "table.common-table tbody tr"    # 定位每一行
  filters:
    skip_lines: 1                                 # 跳过表头行
  fields:
    - name: stat_month
      column: 0                                   # td 索引（从 0 开始）
      strip: true
    - name: avg_players
      column: 1
      to_number: true
    - name: peak_players
      column: 4
      to_number: true
```

**column + selector 组合提取**——先用 `column` 定位到 `<td>`，再用 `selector` 在 `<td>` 内找子元素：

```html
<tr>
  <td>1</td>
  <td>Game Name</td>
  <td><a href="https://store.steampowered.com/app/1974050">Link</a></td>
</tr>
```

```yaml
fields:
  - name: steam_id
    column: 2              # 先定位到第 3 个 td
    selector: "a"          # 在 td 内找 <a>
    attr: "href"           # 取 href 属性值
    regex_extract: "/app/(\\d+)"   # 从 URL 中提取数字 ID
    to_number: true
```

### HTML 表格 + element_selector 页面级变量

有些页面的统计日期不在表格内，而在页头的某个 `<div>` 中。`element_selector` 提取这类信息后注入 context，供字段引用。

**HTML 结构：**
```html
<div class="header">
  <div class="_2AQqwf9WZKu7d8zUGYJ5VR">2026年8月2日 截止</div>
</div>
<table class="sales-table">...</table>
```

**配置：**

```yaml
parser:
  type: html_table
  row_selector: "table.sales-table tbody tr"
  element_selector:
    stat_date:                                         # 自定义变量名
      selector: "div._2AQqwf9WZKu7d8zUGYJ5VR"         # 页头元素的 CSS 选择器
  fields:
    - name: rank
      column: 1
      to_number: true
    - name: crawled_at
      value: "{stat_date}"                             # 引用 element_selector 提取的值
      truncate_right: 2                                # 去掉末尾的 " 截止"
      to_datetime: true
      date_format: "%Y年%-m月%-d日"
      date_output_format: "%Y-%m-%d"
```

**工作机制**：
1. `element_selector` 从 HTML 中提取页头元素的文本 → `stat_date = "2026年8月2日 截止"`
2. 注入到 context → `ctx["stat_date"] = "2026年8月2日 截止"`
3. 字段 `crawled_at` 通过 `value: "{stat_date}"` 引用 → 拿到 `"2026年8月2日 截止"`
4. 经 `truncate_right: 2` → `"2026年8月2日"`
5. 经 `to_datetime` → `"2026-08-02"`

> **注意**：`element_selector` 只做原始文本提取，不做清洗。清洗发生在引用它的字段上。

### SDK/文件字段映射（sdk_mapping）

用于 SDK 返回的 `list[dict]`（如 akshare 返回的 DataFrame 转 list）、CSV 文件读取结果、数据库查询结果。

原理：原始字段名 → 数据库列名 的映射，不做额外解析。

```yaml
parser:
  type: sdk_mapping
  fields:
    - name: code              # 数据库列名
      value: "02400"          # 静态值（SDK 数据里没有的字段）
    - name: date
      source: "date"          # 原始字段名 → 数据库列名
    - name: open
      source: "open"
      to_number: true
    - name: close
      source: "close"
      to_number: true
```

### parser 级过滤：filters

`filters` 在 `parser` 下配置，作用于解析后的整批行，在字段清洗之前执行。

| 过滤关键字 | 类型 | 说明 |
|-----------|------|------|
| `skip_lines` | int | 跳过前 N 行（如 HTML 表格有表头行需要跳过） |
| `head` | int | 只保留前 N 条 |
| `tail` | int | 只保留最后 N 条 |

```yaml
parser:
  type: html_table
  row_selector: "table tbody tr"
  filters:
    skip_lines: 1        # 跳过第 1 行（表头）
    tail: 48             # 只保留最后 48 行
```

> **与字段级 where 的区别**：`filters`（skip_lines/head/tail）是数量级过滤，在清洗前执行；`where` 是值级过滤，在清洗后执行。详见第九节。

---

## 七、字段提取方式对照表

根据 parser type 不同，字段支持以下提取属性（按优先级从高到低）：

| 优先级 | 属性 | 何时生效 | 值示例 |
|--------|------|----------|--------|
| 1（最高） | `value` | 任何 type，字段配置了 `value` 时 | `"hello"` 或 `"{region}"` |
| 2 | `position` | parser 配置了 `array_index_mapping: true` | `0`, `1`, `2` |
| 3 | `column` / `selector` / `attr` | `html_table` 或 `css_selector` | `column: 2` + `selector: "a"` + `attr: "href"` |
| 4（最低） | `source` 或 `path` | JSON dict 模式（`json` / `sdk_mapping`） | `source: "原字段名"` 或 `path: "data.items.0.title"` |

> **source > path**：当 `source` 和 `path` 同时配置时，`source` 优先。

---

## 八、字段清洗规则

清洗规则直接写在 `parser.fields` 的每个字段上，规则名就是 YAML 的键名。

清洗分三个阶段按顺序执行：

| 阶段 | 执行时机 | 包含规则 |
|------|----------|----------|
| **phase 0（文本）** | 最先 | `strip` `truncate_left` `truncate_right` `trim_whitespace` `remove_html` `regex_extract` `regex_replace` |
| **phase 1（类型转换）** | 文本清洗之后 | `number_expr_to_int` `to_number` `to_datetime` |
| **phase 2（后处理）** | 类型转换之后 | `ts_floor_to_hour` `ts_floor_to_day` |

### 规则详解

#### strip

去除首尾空白。**默认开启**——不需要显式配置。

```yaml
- name: title
  path: "title"            # strip 自动生效
```

显式关闭：

```yaml
- name: raw_text
  path: "content"
  strip: false             # 保留原始空白
```

#### truncate_left / truncate_right

字符串截断。

```yaml
- name: short_code
  path: "code"
  truncate_left: 6         # 保留左侧 6 个字符，"ABCDEF12345" → "ABCDEF"

- name: date_raw
  selector: "div.header"
  truncate_right: 2        # 截去右侧 2 个字符，"8月2日 截止" → "8月2日"
```

#### trim_whitespace

压缩连续空白为单个空格。

```yaml
- name: description
  path: "desc"
  trim_whitespace: true    # "hello    world  !" → "hello world !"
```

#### remove_html

去除残留 HTML 标签并解码 HTML 实体。

```yaml
- name: clean_content
  path: "html_body"
  remove_html: true        # "<p>Hello &amp; World</p>" → "Hello & World"
```

#### regex_extract

正则提取——取第一个捕获组；无捕获组则取整个匹配。

```yaml
- name: steam_id
  selector: "a"
  attr: "href"
  regex_extract: "/app/(\\d+)"   # 从 URL 提取数字 ID
```

#### regex_replace

批量正则替换，每项是 `{pattern, replacement}`。

```yaml
- name: clean_text
  path: "content"
  regex_replace:
    - pattern: "<[^>]+>"
      replacement: ""            # 去掉标签
    - pattern: "\\s+"
      replacement: " "           # 合并空白
```

#### to_number

转为数字——自动处理千分位逗号（中英文）。

```yaml
- name: price
  path: "price"
  to_number: true          # "12,345" → 12345, "3.14" → 3.14
```

#### number_expr_to_int

中文数字单位转整数。

| 输入 | 输出 |
|------|------|
| `"1234.56万"` | 12345600 |
| `"5.67亿"` | 567000000 |
| `"200M"` | 200000000 |

支持的单位：`十`、`百`、`千`、`万`、`M`（百万）、`亿`、`B`（十亿）

```yaml
- name: amt_hkd
  column: 6
  number_expr_to_int: true   # "1.23亿" → 123000000
```

#### to_datetime

日期字符串标准化。

```yaml
- name: stat_month
  column: 0
  to_datetime: true                    # "July 2026" → "2026-07-01 00:00:00"
  date_output_format: "%Y-%m-%d"       # 指定输出格式 → "2026-07-01"
```

**自动尝试的日期格式：**

系统按 `date_format`（如果配置了）→ 12 种内置格式的顺序尝试解析。内置格式包括：
`%Y-%m-%d %H:%M:%S`、`%Y-%m-%dT%H:%M:%S`、`%Y-%m-%d`、`%Y/%m/%d`、`%Y年%m月%d日`、`%b %d, %Y`、`%B %Y`、`%Y-%m` 等。

```yaml
- name: publish_date
  path: "publishTime"
  to_datetime: true
  date_format: "%Y-%m-%dT%H:%M:%S"       # 优先尝试此格式
  date_output_format: "%Y-%m-%d %H:%M"   # 输出格式
```

#### ts_floor_to_hour / ts_floor_to_day

毫秒时间戳按小时或按天取整。

```yaml
- name: stat_ts
  position: 0
  to_number: true
  ts_floor_to_hour: true       # 1664583945000 → 1664582400000
```

#### default

字段值为 `None` 时的兜底值。

```yaml
- name: title
  path: "data.title"
  default: "无标题"            # 字段不存在时使用
```

---

## 九、字段级过滤 where

`where` 写在**字段级别**（不是 parser 级别），在清洗完成后按值过滤行。多个 where 条件是 **AND** 关系，全部满足才保留。

| op | 含义 | value 示例 |
|----|------|-----------|
| `">"` | 大于 | `5000` |
| `"<"` | 小于 | `100` |
| `">="` | 大于等于 | `0` |
| `"<="` | 小于等于 | `100` |
| `"=="` | 等于 | `"active"` |
| `"!="` | 不等于 | `null` |
| `"in"` | 在列表中 | `[730, 570, 578080]` |
| `"not_in"` | 不在列表中 | `["deleted"]` |
| `"contains"` | 字符串包含 | `"keyword"` |

```yaml
fields:
  - name: steam_id
    column: 2
    selector: "a"
    attr: "href"
    regex_extract: "/app/(\\d+)"
    to_number: true
    where:
      op: "in"
      value: [1974050, 2315040, 4025700]     # 只保留这三个游戏的数据
```

---

## 十、动态参数模板

URL 和字段的 `value` 中可以使用 `{变量}` 占位符。引擎在请求前自动替换为运行时的实际值。

### 时间类变量

| 变量 | 说明 | 示例（2026-08-03 14:30:00 执行时） |
|------|------|-----------------------------------|
| `{today}` | 当天日期 | `2026-08-03` |
| `{today:%Y%m%d}` | 自定义 strftime 格式 | `20260803` |
| `{yesterday}` | 昨天 | `2026-08-02` |
| `{yesterday:%Y%m%d}` | 昨天 + 格式 | `20260802` |
| `{now}` | 当前日期时间 | `2026-08-03 14:30:00` |
| `{now:%Y%m%d%H%M%S}` | 当前时间 + 格式 | `20260803143000` |
| `{timestamp}` | Unix 秒时间戳 | `1753766400` |
| `{timestamp_ms}` | Unix 毫秒时间戳 | `1753766400000` |
| `{days_ago:7}` | 7 天前 | `2026-07-27` |
| `{days_ago:30:%Y%m%d}` | 30 天前 + 格式 | `20260704` |
| `{weeks_ago:2}` | 2 周前 | `2026-07-20` |
| `{weeks_ago:1:%Y%m%d}` | 1 周前 + 格式 | `20260727` |
| `{this_week:1}` | 本周一 | `2026-08-03`（周一） |
| `{this_week:7}` | 本周日 | `2026-08-09`（周日） |
| `{last_week:1}` | 上周一 | `2026-07-27` |
| `{last_week:5}` | 上周五 | `2026-07-31` |

### 自定义变量

| 变量 | 来源 |
|------|------|
| `{task_name}` | 任务 name 字段 |
| `{url}` | 解析后的完整 URL（自动注入） |
| `{region}`、`{steam_id}` 等 | iterate 变量 或 params 变量 或 element_selector 变量 |

### 使用场景

```yaml
# 场景一：查询当天数据
url: "https://api.example.com/forecast?date={today}"

# 场景二：查询近 7 天数据
url: "https://api.example.com/trade?from={days_ago:7}&to={today}"

# 场景三：自定义日期格式
url: "https://api.example.com/report?date={today:%Y%m%d}"

# 场景四：URL 中包含上周的日期路径
url: "https://store.steampowered.com/charts/topselling/{region}/{last_week:2}"
```

### 模板变量在 context 中的全量传递

系统对 context 中**所有字符串值**做递归模板解析。这意味着你在 `url`、`value`、`params` 甚至 `db.query` 中都可以使用变量。`iterate` 的 var_name、`params`、`element_selector` 的 var_name 和 `task_name` 都会进入 context 池。

---

## 十一、iterate — 多值迭代

一个任务需要遍历多个参数值分别请求时使用（如多个 steam_id、多个 region、多页等）。

```yaml
iterate:
  - var_name: "region"
    values: [global, CN, TW, BR, JP, FR, TH, US, KR]
```

**多变量笛卡尔积**——同时迭代两个以上维度：

```yaml
iterate:
  - var_name: "region"
    values: [global, CN]
  - var_name: "page"
    values: [1, 2, 3]

# 生成 2 × 3 = 6 个 context:
# region=global, page=1 → url?region=global&page=1
# region=global, page=2 → url?region=global&page=2
# ...
```

**执行逻辑**：
1. 笛卡尔积展开为 N 个 context
2. 每个 context 独立执行（含反爬延迟、重试）
3. 所有结果汇总后返回

> 配合浏览器模式时，每个 iterate 都会启动独立浏览器上下文，开销较大。建议合理控制 `values` 数量。

---

## 十二、浏览器动态页面 browser

对于 JavaScript 渲染的页面（如 Steam 畅销榜、React/Vue 单页应用），aiohttp 取不到渲染后的内容。配置 `browser` 启用 Playwright 无头浏览器。

### 依赖

```bash
pip install playwright
playwright install chromium
```

### 配置项

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `headless` | boolean | `true` | 无头模式；`false` 可看到浏览器窗口 |
| `wait_selector` | string | — | 等待该 CSS 选择器出现后再操作 |
| `wait_timeout` | int | `15000` | 等待超时毫秒数 |
| `actions` | list | `[]` | 页面交互操作序列 |
| `screenshot` | string | — | 调试用截图/快照路径 |

### actions 操作

| type | 说明 | 参数 |
|------|------|------|
| `click` | 点击选择器 | `selector`：CSS 选择器 |
| `scroll` | 滚动到底部 | `repeat`：滚动次数 |
| `wait` | 纯等待 | `ms`：毫秒数 |
| 通用 | 操作后等待 | `wait_after`：毫秒（默认 1000） |

### 示例：点击弹窗按钮后采集

```yaml
type: web
browser:
  headless: true
  wait_selector: "table.sales-table"
  wait_timeout: 15000
  actions:
    - type: "wait"
      ms: 2000
    - type: "click"                       # 先点掉弹窗按钮
      selector: "button.DialogButton.Primary"
      wait_after: 5000
    - type: "wait"
      ms: 3000
```

### 截图调试

```yaml
browser:
  screenshot: "debug_steam.html"          # .html 后缀 → 保存页面源码
  # screenshot: "debug_steam.png"         # .png 后缀 → 保存全页截图
```

---

## 十三、数据源专项配置

### SDK 调用（type: sdk）

```yaml
type: sdk
provider:
  module: "akshare"                  # Python 模块名
  function: "stock_hk_daily"         # 模块中的函数名
  params:                            # 传给函数的参数
    symbol: "02400"
    adjust: "qfq"
```

SDK 返回的 pandas DataFrame 会自动转为 `list[dict]`。用 `sdk_mapping` 解析器映射字段：

```yaml
parser:
  type: sdk_mapping
  fields:
    - name: code
      value: "02400"
    - name: date
      source: "date"                 # DataFrame 的列名
    - name: open
      source: "open"
      to_number: true
```

### CSV / Excel 文件读取（type: csv / excel）

**CSV：**

```yaml
type: csv
file:
  format: csv
  path: "config/data/history.csv"    # 相对路径或绝对路径
  encoding: utf-8-sig                # 默认 utf-8；带 BOM 用 utf-8-sig
  delimiter: ","                     # 默认逗号
```

**Excel：**

```yaml
type: excel
file:
  format: excel
  path: "data/offline/history.xlsx"
  sheet_name: "2026Q3"               # 工作表名；也可用数字索引如 0
```

> 文件首行作为列名（表头）。数据和 SDK/HTTP 一样走完整清洗→UPSERT 流水线。

### 外部数据库查询（type: db）

**SQLite：**

```yaml
type: db
db:
  type: sqlite
  path: "crawler.db"                 # 源数据库路径
  query: >
    SELECT steam_id, ss, MAX(peak_players) as peak_players
    FROM steam_game_peak_players_hourly
    WHERE stat_ts >= 1784217600000
    GROUP BY steam_id, ss, ss_day
```

**MySQL：**

```yaml
type: db
db:
  type: mysql
  host: "192.168.1.100"
  port: 3306
  user: "reader"
  password: "${MYSQL_PWD}"           # 支持 ${ENV_VAR} 环境变量
  database: "source_db"
  query: "SELECT * FROM trades WHERE created_at >= '2026-01-01'"
```

> MySQL 需要安装 `pymysql`。查询完成后立即关闭连接，不保持长连接。

---

## 十四、anti_spider — 反反爬

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | boolean | `false` | 总开关 |
| `delay` | [min, max] | `[1, 3]` | 每次请求前随机延迟（秒） |
| `rotate_user_agent` | boolean | `false` | 每次随机换 User-Agent |
| `use_proxy` | boolean | `false` | 是否启用代理 |
| `proxies` | list | `[]` | 代理地址列表 |

```yaml
anti_spider:
  enabled: true
  delay: [3, 7]                    # Steam Charts 有频率限制
  rotate_user_agent: true
```

---

## 十五、retry — 重试策略

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_attempts` | int | `3` | 最大尝试次数（含第一次） |
| `backoff_base` | float | `2.0` | 退避底数 |

退避间隔：`base^0` → `base^1` → `base^2` → …

| base=2, max=3 | base=3, max=4 |
|---------------|---------------|
| 失败 → 等 1s | 失败 → 等 1s |
| 失败 → 等 2s | 失败 → 等 3s |
| 失败 → 放弃 | 失败 → 等 9s |
| | 失败 → 放弃 |

> 仅对网络错误和 5xx 重试，4xx 不重试。

---

## 十六、完整实战示例

以下示例均基于项目实际运行的配置简化而来。

### 示例一：JSON API + root_path 数组展开 + 动态日期

```yaml
name: "香港金融市场流动性"
type: api
trigger_type: system
schedule: "0 20 * * 1-5"
url: "https://api.hkma.gov.hk/public/market-data-and-statistics/daily-monetary-statistics/daily-figures-interbank-liquidity?choose=end_of_date&from={days_ago:7}&to={today}"
outputs:
  - target_table: "hk_market_liquidity_daily"
    table_schema:
      columns:
        - name: id
          type: INTEGER
          constraint: PRIMARY KEY AUTOINCREMENT
        - name: end_of_date
          type: TEXT
        - name: cu_weakside
          type: REAL
        - name: cu_strongside
          type: REAL
        - name: hibor_overnight
          type: REAL
        - name: hibor_fixing_1m
          type: REAL
        - name: twi
          type: REAL
        - name: opening_balance
          type: REAL
        - name: closing_balance
          type: REAL
        - name: crawled_at
          type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
      indexes:
        - name: idx_uk_end_of_date
          columns: [end_of_date]
          unique: true
    parser:
      type: json
      root_path: "result.records"
      fields:
        - name: end_of_date
          path: "end_of_date"
        - name: cu_weakside
          path: "cu_weakside"
          to_number: true
        - name: cu_strongside
          path: "cu_strongside"
          to_number: true
        - name: hibor_overnight
          path: "hibor_overnight"
          to_number: true
        - name: hibor_fixing_1m
          path: "hibor_fixing_1m"
          to_number: true
        - name: twi
          path: "twi"
          to_number: true
        - name: opening_balance
          path: "opening_balance"
          to_number: true
        - name: closing_balance
          path: "closing_balance"
          to_number: true
retry:
  max_attempts: 3
  backoff_base: 2.0
```

### 示例二：HTML 表格 + element_selector + 日期清洗

```yaml
name: "Steam 畅销榜周榜"
type: web
trigger_type: system
schedule: "12 21 * * *"
url: "https://store.steampowered.com/charts/topselling/{region}/{last_week:2}"
iterate:
  - var_name: "region"
    values: [global, CN, TW, BR, JP, FR, TH, US, KR, HK]
outputs:
  - target_table: "steam_best_seller_list_weekly"
    table_schema:
      columns:
        - name: id
          type: INTEGER
          constraint: PRIMARY KEY AUTOINCREMENT
        - name: steam_id
          type: INTEGER
        - name: rank
          type: INTEGER
        - name: region
          type: TEXT
        - name: crawled_at
          type: TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d', 'now', 'localtime'))
      indexes:
        - name: idx_uk_steam_id_region_crawled_at
          columns: [steam_id, region, crawled_at]
          unique: true
    parser:
      type: html_table
      row_selector: "table._3arZn0BMPzyhcYNADe193m tbody tr"
      element_selector:
        stat_date:
          selector: "div.vlHd8EhkUPvzcN2Xn4Y0j._2AQqwf9WZKu7d8zUGYJ5VR"
      fields:
        - name: steam_id
          column: 2
          selector: "a"
          attr: "href"
          regex_extract: "/app/(\\d+)"
          to_number: true
        - name: rank
          column: 1
          strip: true
          to_number: true
        - name: region
          value: "{region}"
        - name: crawled_at
          value: "{stat_date}"
          strip: true
          truncate_right: 2
          to_datetime: true
          date_format: "%Y年%-m月%-d日"
          date_output_format: "%Y-%m-%d"
retry:
  max_attempts: 3
  backoff_base: 2.0
```

### 示例三：JSON 二维数组 + iterate + 时间戳取整

```yaml
name: "Steam 每小时峰值玩家"
type: api
trigger_type: system
schedule: "35 * * * *"
url: "https://steamcharts.com/app/{steam_id}/chart-data.json"
iterate:
  - var_name: "steam_id"
    values: [1974050, 2315040, 4025700]
outputs:
  - target_table: "steam_game_peak_players_hourly"
    table_schema:
      columns:
        - name: id
          type: INTEGER
          constraint: PRIMARY KEY AUTOINCREMENT
        - name: steam_id
          type: INTEGER
        - name: stat_ts
          type: INTEGER
        - name: peak_players
          type: INTEGER
        - name: crawled_at
          type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
      indexes:
        - name: idx_uk_steam_id_stat_ts
          columns: [steam_id, stat_ts]
          unique: true
    parser:
      type: json
      array_index_mapping: true
      filters:
        tail: 48
      fields:
        - name: steam_id
          value: "{steam_id}"
        - name: stat_ts
          position: 0
          to_number: true
          ts_floor_to_hour: true
        - name: peak_players
          position: 1
anti_spider:
  enabled: true
  delay: [2, 5]
retry:
  max_attempts: 3
  backoff_base: 2.0
```

### 示例四：多表输出 + 反反爬

```yaml
name: "Steam 玩家评价"
type: api
trigger_type: system
schedule: "0 8 * * *"
url: "https://store.steampowered.com/appreviewhistogram/{steam_id}"
iterate:
  - var_name: "steam_id"
    values: [1974050, 2315040, 4025700]
outputs:
  - target_table: "review_recent"
    table_schema:
      columns:
        - name: id
          type: INTEGER
          constraint: PRIMARY KEY AUTOINCREMENT
        - name: steam_id
          type: INTEGER
        - name: stat_ts
          type: INTEGER
        - name: up
          type: INTEGER
        - name: down
          type: INTEGER
        - name: crawled_at
          type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
      indexes:
        - name: idx_uk_steam_id_stat_ts
          columns: [steam_id, stat_ts]
          unique: true
    parser:
      type: json
      root_path: "results.recent"
      fields:
        - name: steam_id
          value: "{steam_id}"
        - name: stat_ts
          path: "date"
        - name: up
          path: "recommendations_up"
        - name: down
          path: "recommendations_down"

  - target_table: "review_rollup"
    table_schema:
      columns:
        - name: id
          type: INTEGER
          constraint: PRIMARY KEY AUTOINCREMENT
        - name: steam_id
          type: INTEGER
        - name: stat_ts
          type: INTEGER
        - name: up
          type: INTEGER
        - name: down
          type: INTEGER
        - name: crawled_at
          type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
      indexes:
        - name: idx_uk_steam_id_stat_ts
          columns: [steam_id, stat_ts]
          unique: true
    parser:
      type: json
      root_path: "results.rollups"
      fields:
        - name: steam_id
          value: "{steam_id}"
        - name: stat_ts
          path: "date"
        - name: up
          path: "recommendations_up"
        - name: down
          path: "recommendations_down"
anti_spider:
  enabled: true
  delay: [2, 5]
retry:
  max_attempts: 3
  backoff_base: 2.0
```

### 示例五：SDK 调用 + sdk_mapping

```yaml
name: "港股日线"
type: sdk
trigger_type: system
schedule: "0 7 * * *"
provider:
  module: "akshare"
  function: "stock_hk_daily"
  params:
    symbol: "02400"
    adjust: "qfq"
outputs:
  - target_table: "stocks_daily_kline"
    table_schema:
      columns:
        - name: id
          type: INTEGER
          constraint: PRIMARY KEY AUTOINCREMENT
        - name: code
          type: TEXT NOT NULL
        - name: type
          type: TEXT NOT NULL
        - name: market
          type: TEXT NOT NULL
        - name: date
          type: TEXT
        - name: open
          type: REAL
        - name: high
          type: REAL
        - name: low
          type: REAL
        - name: close
          type: REAL
        - name: volume
          type: REAL
        - name: crawled_at
          type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
      indexes:
        - name: idx_uk_code_type_market_date
          columns: [code, type, market, date]
          unique: true
    parser:
      type: sdk_mapping
      fields:
        - name: code
          value: "02400"
        - name: type
          value: "STOCK"
        - name: market
          value: "HK"
        - name: date
          source: "date"
        - name: open
          source: "open"
          to_number: true
        - name: high
          source: "high"
          to_number: true
        - name: low
          source: "low"
          to_number: true
        - name: close
          source: "close"
          to_number: true
        - name: volume
          source: "volume"
          to_number: true
```

### 示例六：浏览器动态页面 + 多值迭代 + 正则提取

```yaml
name: "Steam 畅销榜小时采集"
type: web
trigger_type: system
schedule: "20 */4 * * *"
iterate:
  - var_name: "region"
    values: [global, CN, TW, BR, JP, FR, TH, US, KR]
url: "https://store.steampowered.com/charts/topselling/{region}"
browser:
  headless: true
  wait_selector: "table._3arZn0BMPzyhcYNADe193m"
  wait_timeout: 15000
  actions:
    - type: "wait"
      ms: 1000
outputs:
  - target_table: "steam_best_seller_list_hourly"
    table_schema:
      columns:
        - name: id
          type: INTEGER
          constraint: PRIMARY KEY AUTOINCREMENT
        - name: steam_id
          type: INTEGER
        - name: rank
          type: INTEGER
        - name: region
          type: TEXT
        - name: crawled_at
          type: TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H', 'now', 'localtime'))
      indexes:
        - name: idx_uk_steam_id_region_crawled_at
          columns: [steam_id, region, crawled_at]
          unique: true
    parser:
      type: html_table
      row_selector: "table._3arZn0BMPzyhcYNADe193m tbody tr"
      fields:
        - name: steam_id
          column: 2
          selector: "a"
          attr: "href"
          regex_extract: "/app/(\\d+)"
          to_number: true
          where:
            op: "in"
            value: [1974050, 2315040, 4025700]
        - name: rank
          column: 1
          strip: true
          to_number: true
        - name: region
          value: "{region}"
retry:
  max_attempts: 3
  backoff_base: 2.0
```

### 示例七：外部 SQLite 查询 + sdk_mapping 透传

```yaml
name: "火炬之光赛季明细"
type: db
trigger_type: system
schedule: "40 * * * *"
db:
  type: sqlite
  path: "crawler.db"
  query: >
    SELECT steam_id, 13 as ss,
           ((stat_ts - 1784217600000) / 86400000) + 1 as ss_day,
           MAX(peak_players) as peak_players
    FROM steam_game_peak_players_hourly
    WHERE stat_ts >= 1784217600000 AND stat_ts < 1792202400000
      AND steam_id in (1974050, 2315040)
    GROUP BY steam_id, ss, ss_day
outputs:
  - target_table: "torchlight_season_steam_peak_players"
    table_schema:
      columns:
        - name: id
          type: INTEGER
          constraint: PRIMARY KEY AUTOINCREMENT
        - name: steam_id
          type: INTEGER
        - name: ss
          type: INTEGER
        - name: ss_day
          type: INTEGER
        - name: peak_players
          type: INTEGER
        - name: crawled_at
          type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
      indexes:
        - name: idx_uk_steam_id_ss_ss_day
          columns: [steam_id, ss, ss_day]
          unique: true
    parser:
      type: sdk_mapping
      fields:
        - name: steam_id
          source: steam_id
          to_number: true
        - name: ss
          source: ss
          to_number: true
        - name: ss_day
          source: ss_day
          to_number: true
        - name: peak_players
          source: peak_players
          to_number: true
```

---

## 十七、常见问题

**Q: 顶层还有 `target_table` 和 `parser` 字段吗？**

没有。当前架构只用 `outputs` 列表。旧版顶层字段已不再支持。

**Q: YAML 文件可以包含多个任务吗？**

可以，用 YAML 列表格式（`-` 开头）：

```yaml
- name: "任务一"
  type: api
  ...

- name: "任务二"
  type: web
  ...
```

**Q: 修改 YAML 后需要重启吗？**

定时任务需要重启 `python main.py`。手动任务通过 API 创建/更新会自动热加载。

**Q: 如何调试一个任务？**

```bash
python main.py --run-once "任务名" --log-level DEBUG
```

**Q: 唯一索引和去重的关系？**

`unique: true` 的索引由 SQLite 保证唯一性。重复数据触发 `ON CONFLICT DO UPDATE`，自动更新已有记录。这是系统唯一的数据去重机制。

**Q: SDK 中 value 和 source 能同时用吗？**

能，但 `value` 优先级更高。同时配置时 `source` 被忽略。

**Q: {today} 的时间基准？**

系统本地时间。

**Q: CSV 表头如何处理？**

CSV 使用第一行作为列名（通过 `csv.DictReader`），Excel 同样使用第一行。如果文件第一行不是表头，需要先在文件中调整。

**Q: element_selector 的值在哪里可用？**

`element_selector` 提取的变量注入到 context，可在同一 output 的所有字段的 `value` 占位符中引用。还可通过 context 的 `{变量名}` 传递到其他 output（同一请求内的）。

**Q: 如何指定非 UTF-8 编码？**

使用顶层 `encoding` 字段：

```yaml
type: web
url: "https://example.com/page.htm"
encoding: 'big5hkscs'
```

---

## 十八、附录：清洗规则全矩阵

| 规则名 | 阶段 | 用法 | 输入 → 输出示例 |
|--------|------|------|----------------|
| `strip` | 0 | `strip: false` 关闭 | `" hello "` → `"hello"` |
| `truncate_left` | 0 | `truncate_left: 6` | `"ABCDEF12345"` → `"ABCDEF"` |
| `truncate_right` | 0 | `truncate_right: 2` | `"8月2日 截止"` → `"8月2日"` |
| `trim_whitespace` | 0 | `trim_whitespace: true` | `"a   b"` → `"a b"` |
| `remove_html` | 0 | `remove_html: true` | `"<p>Hello &amp;</p>"` → `"Hello &"` |
| `regex_extract` | 0 | `regex_extract: "/app/(\\d+)"` | `"/app/1974050"` → `"1974050"` |
| `regex_replace` | 0 | `regex_replace: [{pattern, replacement}]` | 多组替换 |
| `number_expr_to_int` | 1 | `number_expr_to_int: true` | `"1.23亿"` → `123000000` |
| `to_number` | 1 | `to_number: true` | `"12,345"` → `12345` |
| `to_datetime` | 1 | `to_datetime: true` + `date_format` + `date_output_format` | `"July 2026"` → `"2026-07-01"` |
| `ts_floor_to_hour` | 2 | `ts_floor_to_hour: true` | `1664583945000` → `1664582400000` |
| `ts_floor_to_day` | 2 | `ts_floor_to_day: true` | `1664583945000` → `1664496000000` |
| `default` | — | `default: "N/A"` | 字段值为 `None` 时使用 |

---

## 十九、附录：模板变量全矩阵

| 变量 | 参数 | 示例模板 | 2026-08-03 执行结果 |
|------|------|----------|-------------------|
| `{today}` | — | `{today}` | `2026-08-03` |
| `{today:fmt}` | format | `{today:%Y%m%d}` | `20260803` |
| `{yesterday}` | — | `{yesterday}` | `2026-08-02` |
| `{yesterday:fmt}` | format | `{yesterday:%Y%m%d}` | `20260802` |
| `{now}` | — | `{now}` | `2026-08-03 14:30:00` |
| `{now:fmt}` | format | `{now:%Y%m%d%H%M%S}` | `20260803143000` |
| `{timestamp}` | — | `{timestamp}` | `1753766400` |
| `{timestamp_ms}` | — | `{timestamp_ms}` | `1753766400000` |
| `{days_ago:N}` | N=天数 | `{days_ago:7}` | `2026-07-27` |
| `{days_ago:N:fmt}` | N=天数, fmt=格式 | `{days_ago:30:%Y%m%d}` | `20260704` |
| `{weeks_ago:N}` | N=周数 | `{weeks_ago:2}` | `2026-07-20` |
| `{weeks_ago:N:fmt}` | N=周数, fmt=格式 | `{weeks_ago:1:%Y%m%d}` | `20260727` |
| `{this_week:N}` | N=1~7(周一到周日) | `{this_week:1}` | 本周一的日期 |
| `{this_week:N:fmt}` | N=1~7, fmt=格式 | `{this_week:7:%Y%m%d}` | 本周日的日期 |
| `{last_week:N}` | N=1~7(周一到周日) | `{last_week:2}` | 上周二的日期 |
| `{last_week:N:fmt}` | N=1~7, fmt=格式 | `{last_week:5:%Y%m%d}` | 上周五的日期 |
| `{task_name}` | — | `{task_name}` | 当前任务名称 |
| `{xxx}` | 自定义变量 | `{region}` | 从 context 查找 |
