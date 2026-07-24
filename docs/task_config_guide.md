# 爬虫任务配置文件说明文档

> 版本：v1.3 | 最后更新：2026-07-24

---

## 目录

1. [概述](#概述)
2. [快速开始](#快速开始)
3. [配置结构总览](#配置结构总览)
4. [基础配置](#基础配置)
5. [浏览器动态页面 browser](#浏览器动态页面-browser)
6. [动态参数模板](#动态参数模板)
7. [多值迭代 iterate](#多值迭代-iterate)
8. [多表输出 outputs](#多表输出-outputs)
9. [表结构定义 table_schema](#表结构定义-table_schema)
10. [解析配置 parser](#解析配置-parser)
11. [反反爬配置 anti_spider](#反反爬配置-anti_spider)
12. [重试配置 retry](#重试配置-retry)
13. [SDK 数据源配置 provider](#sdk-数据源配置-provider)
14. [文件数据源配置 file](#文件数据源配置-file)
15. [数据库数据源配置 db](#数据库数据源配置-db)
16. [完整示例](#完整示例)
17. [常见问题](#常见问题)

---

## 概述

爬虫系统通过 YAML 文件定义每个采集任务。每个 `.yaml` 文件描述一个任务的**数据来源、解析方式、存储结构、调度频率**。系统启动时自动扫描 `config/tasks/` 目录，无需修改任何代码即可新增采集目标。

### 核心设计原则

- **零代码扩展**：新增采集目标只需写一个 YAML
- **表结构自管理**：系统自动建表、建索引
- **字段映射**：将非标准的原始数据字段映射为标准业务字段
- **可插拔解析器**：支持 HTML/CSS 选择器、JSON 路径、SDK 字段映射
- **动态参数模板**：URL 支持 `{today}` `{yesterday}` `{days_ago:N}` 等运行时变量

---

## 快速开始

### 1. 创建配置文件

在 `config/tasks/` 下新建 `my_first_task.yaml`：

```yaml
name: "我的第一个采集任务"
type: api
method: GET
url: "https://jsonplaceholder.typicode.com/posts/1"
schedule: "0 0 * * *"
target_table: "my_first_table"
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
retry:
  max_attempts: 3
  backoff_base: 2.0
```

### 2. 测试运行

```bash
python main.py --run-once "我的第一个采集任务"
```

### 3. 设为定时任务

```bash
python main.py
```

---

## 配置结构总览

```yaml
name: "任务名称"              # 必填，唯一标识
type: api                    # 必填：web | api | sdk
method: GET                  # 可选：GET | POST（web/api类型）
url: "https://..."           # web/api 类型必填，支持模板变量
schedule: "0 */6 * * *"      # 必填，cron 表达式
target_table: "table_name"   # 必填，数据库表名

table_schema:    {...}       # 必填，表结构定义（列+索引）
parser:          {...}       # 必填，数据解析配置
anti_spider:     {...}       # 可选，反反爬策略
retry:           {...}       # 可选，重试策略
provider:        {...}       # sdk 类型必填，SDK 调用配置
```

---

## 基础配置

### name

- **类型**：`string`
- **必填**：是
- **说明**：任务唯一标识，不可重复。用于日志输出和 `--run-once` 指定执行。

```yaml
name: "新闻头条采集"
```

### type

- **类型**：`string`
- **必填**：是
- **可选值**：
  - `web`：网页采集（HTML 响应）
  - `api`：JSON API 采集
  - `sdk`：第三方 SDK 调用采集
  - `csv`：CSV 文件读取采集
  - `excel`：Excel 文件读取采集
  - `db`：外部数据库查询采集（SQLite / MySQL）

```yaml
type: api
```

### method

- **类型**：`string`
- **必填**：否（默认 `GET`）
- **说明**：HTTP 请求方法，仅 `web` / `api` 类型有效。

```yaml
method: POST
```

### file（csv/excel 类型专属）

- **类型**：`dict`
- **必填**：`csv` / `excel` 类型必填
- **说明**：文件路径和读取参数配置。

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `format` | string | - | 文件格式：`csv` 或 `excel` |
| `path` | string | - | 文件路径（相对于项目根目录或绝对路径） |
| `encoding` | string | `utf-8` | 文件编码 |
| `delimiter` | string | `,` | CSV 分隔符（仅 CSV 有效） |
| `sheet_name` | string/int | `0` | Excel 工作表名或索引（仅 Excel 有效） |

```yaml
# CSV 文件
type: csv
file:
  format: csv
  path: "data/offline/steam_players.csv"
  encoding: utf-8
  delimiter: ","

# Excel 文件
type: excel
file:
  format: excel
  path: "data/offline/history_data.xlsx"
  sheet_name: "2026Q3"
```

### url

- **类型**：`string`
- **必填**：`web` / `api` 类型必填
- **说明**：请求的目标 URL。**支持动态模板变量**（见下一章）。

```yaml
url: "https://api.example.com/v2/data?date={today}&from={yesterday}"
```

### schedule

- **类型**：`string`
- **必填**：是
- **说明**：cron 表达式，控制执行频率。5 位格式：`分 时 日 月 星期`

| 表达式 | 含义 |
|--------|------|
| `"0 * * * *"` | 每小时整点 |
| `"0 */6 * * *"` | 每 6 小时 |
| `"0 0 * * *"` | 每天零点 |
| `"0 2 * * 1-5"` | 工作日凌晨 2 点 |
| `"30 8 * * 1"` | 每周一 8:30 |
| `"0 0 1 * *"` | 每月 1 号零点 |

### target_table

- **类型**：`string`
- **必填**：是
- **说明**：数据存入的 SQLite 表名。系统自动创建该表。

```yaml
target_table: "news_articles"
```

---

## 浏览器动态页面 browser

对于 **JavaScript 动态渲染**的页面（如 Steam 畅销榜、React/Vue 单页应用），普通的 `aiohttp` 无法获取到表格数据。配置 `browser` 块可启用 Playwright 无头浏览器引擎。

### 依赖安装

```bash
pip install playwright
playwright install chromium
```

### 配置项

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `headless` | boolean | `true` | 是否无头模式（`false` 可看到浏览器窗口） |
| `wait_selector` | string | - | 等待该 CSS 选择器出现后再操作 |
| `wait_timeout` | int | `15000` | 等待超时毫秒数 |
| `actions` | list | `[]` | 页面交互操作序列 |
| `screenshot` | string | - | 截图保存路径（调试用） |

### actions 操作类型

| type | 说明 | 额外参数 |
|------|------|----------|
| `click` | 点击选择器 | `selector`：CSS 选择器 |
| `scroll` | 滚动到底部（触发懒加载） | `repeat`：滚动次数 |
| `wait` | 纯等待 | `ms`：等待毫秒数 |
| 所有类型通用 | 操作后等待 | `wait_after`：操作完成后等多少毫秒（默认 1000） |

### 示例配置

```yaml
type: web
browser:
  headless: true
  wait_selector: "table.sales-table"       # 等表格加载完成
  wait_timeout: 15000
  actions:
    - type: "click"                         # 点"加载更多"按钮
      selector: "button.load-more"
      wait_after: 2000
    - type: "click"                         # 再点一次
      selector: "button.load-more"
      wait_after: 2000
    - type: "scroll"                        # 滚动到底部 3 次
      repeat: 3
      wait_after: 1000
parser:
  type: html_table
  row_selector: "table.sales-table tbody tr"
  fields:
    - name: rank
      column: 0
    - name: name
      column: 1
```

> 浏览器模式对**每个请求**都会启动独立上下文（context），自动清理。配合 `iterate` 迭代多页面时开销较大，建议合理控制 `values` 数量和延迟。

---

## 动态参数模板

URL 字符串中可以使用 `{变量}` 占位符，引擎在发起请求前自动替换为运行时的实际值。

### 支持的模板变量

| 变量 | 示例输出（2026-07-18 执行时） | 说明 |
|------|------------------------------|------|
| `{today}` | `2026-07-18` | 当天日期 |
| `{today:%Y%m%d}` | `20260718` | 当天日期 + 自定义 strftime 格式 |
| `{yesterday}` | `2026-07-17` | 昨天日期 |
| `{yesterday:%Y%m%d}` | `20260717` | 昨天 + 自定义格式 |
| `{now}` | `2026-07-18 20:30:00` | 当前日期时间 |
| `{now:%Y%m%d%H%M%S}` | `20260718203000` | 当前时间 + 自定义格式 |
| `{days_ago:7}` | `2026-07-11` | 7 天前 |
| `{days_ago:30:%Y%m%d}` | `20260618` | 30 天前 + 自定义格式 |
| `{task_name}` | `香港金融市场...` | 当前任务名称 |

### 使用场景

```yaml
# 场景一：查询当天数据
url: "https://api.example.com/forecast?date={today}"

# 场景二：查询昨天到今天的数据
url: "https://api.example.com/trade?from={yesterday}&to={today}"

# 场景三：自定义日期格式
url: "https://api.example.com/report?date={today:%Y%m%d}"

# 场景四：查询近30天数据
url: "https://api.example.com/history?start={days_ago:30}&end={today}"
```

### 与 source_url 字段配合

模板变量解析后的 URL 会自动传递给 `parser.fields` 中的 `{url}` 占位符：

```yaml
# URL 模板
url: "https://api.example.com/data?date={today}"

# 解析后实际请求的 URL:
# https://api.example.com/data?date=2026-07-18

# source_url 字段会存这个实际 URL:
parser:
  fields:
    - name: source_url
      value: "{url}"      # 存的是解析后的完整 URL
```

### 模板变量的去重行为

每次执行时模板变量解析为当天的值，所以：

- `url: "https://api.example.com?date={today}"` → 今天和明天的 URL **不同**
- URL 去重机制正常工作（因为 URL 整体变了，hash 自然不同）
- 内容去重由业务表 UNIQUE 索引保证

---

## 多值迭代 iterate

当一个任务需要**遍历多个参数值**分别请求时使用（如多个股票代码、多个游戏ID）。

| 属性 | 类型 | 说明 |
|------|------|------|
| `iterate.var_name` | string | 变量名，用于 URL 模板和 parser 占位符 |
| `iterate.values` | list | 要遍历的值列表 |

```yaml
iterate:
  var_name: "steam_id"
  values: [730, 570, 578080, 1172470]

url: "https://steamcharts.com/app/{steam_id}/chart-data.json"
```

**执行逻辑**：
1. 遍历 `values`，每次将当前值注入 `{steam_id}` 模板变量
2. 每次请求独立执行（含反爬延迟、重试）
3. 所有结果收集后一次性批量写入

**在 parser 中引用迭代变量**：
```yaml
parser:
  fields:
    - name: steam_id
      value: "{steam_id}"      # 每条记录注入当前迭代值
```

> `iterate` 可以和 `outputs` 组合使用，形成"多值迭代 × 多表输出"矩阵。

---

## 多表输出 outputs

当**一次 HTTP 请求返回的 JSON 包含多个数据集**时使用（如 `results.recent` 和 `results.rollups`），可将不同部分写入不同的表。

```yaml
outputs:
  - target_table: "table_recent"
    table_schema:
      columns:
        - name: id
          type: INTEGER
          constraint: PRIMARY KEY AUTOINCREMENT
        # ... 其他列
      indexes:
        - name: idx_uk
          columns: [steam_id, stat_ts]
          unique: true
    parser:
      type: json
      root_path: "results.recent"
      fields:
        - name: stat_ts
          path: "date"
        # ... 其他字段

  - target_table: "table_rollups"
    table_schema: {...}
    parser:
      type: json
      root_path: "results.rollups"
      fields: [...]
```

**执行逻辑**：
1. 发起一次 HTTP 请求
2. 遍历 `outputs` 列表，对每个输出用各自的 `parser` 解析同一份 JSON
3. 分别批量写入对应的业务表

> 有 `outputs` 配置时不需要顶层的 `target_table` 和 `parser`。

---

## 表结构定义 table_schema

### columns

定义业务表的所有列。每列包含以下属性：

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 列名 |
| `type` | string | 是 | SQLite 数据类型 |
| `constraint` | string | 否 | 约束（如 `NOT NULL`, `UNIQUE`） |

**必须包含的列**（约定）：

```yaml
- name: id
  type: INTEGER
  constraint: PRIMARY KEY AUTOINCREMENT
```

**SQLite 支持的类型**：

| SQLite 类型 | 对应 Python 类型 | 用途 |
|-------------|-----------------|------|
| `INTEGER` | `int` | 整数 |
| `REAL` | `float` | 浮点数 |
| `TEXT` | `str` | 文本（日期也存 TEXT） |
| `BLOB` | `bytes` | 二进制（极少用） |

**完整示例**：

```yaml
table_schema:
  columns:
    - name: id
      type: INTEGER
      constraint: PRIMARY KEY AUTOINCREMENT
    - name: code
      type: TEXT NOT NULL
    - name: price
      type: REAL
    - name: volume
      type: REAL
    - name: trade_date
      type: TEXT
    - name: source_url
      type: TEXT NOT NULL
    - name: crawled_at
      type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
```

### indexes

定义业务表的索引。每项包含以下属性：

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 索引名称 |
| `columns` | string[] | 是 | 包含的列名列表 |
| `unique` | boolean | 否 | 是否唯一索引（默认 `false`） |

**索引类型示例**：

```yaml
indexes:
  # 唯一索引：单列去重
  - name: idx_code
    columns: [code]
    unique: true

  # 普通索引：加速查询
  - name: idx_date
    columns: [trade_date]
    unique: false

  # 联合唯一索引：多列组合去重
  - name: idx_code_date
    columns: [code, trade_date]
    unique: true

  # 联合索引：加速组合查询
  - name: idx_category_date
    columns: [category, trade_date]
    unique: false
```

**最佳实践**：

- 唯一索引用于**数据去重**（如 URL、股票代码+日期）
- 普通索引用于**高频查询字段**（如日期、分类）
- 联合索引的**列顺序**应与查询条件保持一致

---

## 解析配置 parser

### type

- **类型**：`string`
- **必填**：是
- **可选值**：

| 值 | 适用场景 |
|----|----------|
| `json` | API 返回 JSON 数据 |
| `html` / `css_selector` | 网页 HTML 数据 |
| `sdk_mapping` | SDK 返回的 DataFrame/list 数据 |

### fields

定义每个字段如何从原始数据中提取。每项包含以下通用属性：

| 属性 | 类型 | 说明 |
|------|------|------|
| `name` | string | **必填**，对应数据库列名 |
| `path` / `selector` | string | JSON 路径 / CSS 选择器（取决于 type） |
| `source` | string | SDK 原始字段名（仅 `sdk_mapping`） |
| `value` | string | 静态赋值（如 `"{url}"`） |

---

### ① JSON 解析 (type: json)

通过点号路径从 JSON 响应中提取值。

#### 单对象模式（默认）

适用于 API 返回**单个对象**的场景：

| 路径写法 | 含义 |
|----------|------|
| `"id"` | 顶层字段 |
| `"data.title"` | 嵌套对象 |
| `"items.0.name"` | 数组第一个元素 |
| `"data"` | 取整个对象 |

```yaml
parser:
  type: json
  fields:
    - name: item_id
      path: "data.id"
      to_number: true

    - name: category
      path: "data.category.name"

    - name: tags
      path: "data.tags"         # 数组会自动序列化

    - name: source_url
      value: "{url}"            # 占位符：当前请求 URL
```

#### 数组展开模式（新增 root_path）

适用于 API 返回**数组嵌套在对象内**的场景（如 `{"result": {"records": [...]}}`）。

使用 `root_path` 定位数组，引擎自动展开为多条记录并批量写入。

| 配置 | 值 | 说明 |
|------|-----|------|
| `root_path` | `"result.records"` | 从根对象定位到数组的路径 |
| `fields[].path` | `"field_name"` | 相对于数组内**每个元素**的路径 |

**示例 JSON 结构**：
```json
{
  "header": {"success": true},
  "result": {
    "records": [
      {"end_of_date": "2026-07-17", "cu_weakside": 7.85},
      {"end_of_date": "2026-07-16", "cu_weakside": 7.86}
    ]
  }
}
```

**对应的 YAML 配置**：
```yaml
parser:
  type: json
  root_path: "result.records"       # 先定位到数组
  fields:
    - name: end_of_date
      path: "end_of_date"           # path 相对于数组内每个元素
    - name: cu_weakside
      path: "cu_weakside"
      to_number: true
    - name: source_url
      value: "{url}"
```

**工作原理**：
1. 解析整个 JSON → 用 `root_path` 定位到 `result.records` → 得到数组
2. 遍历数组中每个元素 → 按 `fields[].path` 提取字段
3. 批量 UPSERT 写入数据库

> **注意**：有 `root_path` 时自动走批量写入模式，不需要手动设置 `type: sdk`。

#### 二维数组模式（array_index_mapping）

适用于 JSON 是 `[[val1, val2], ...]` 格式——每个元素是**数组**而非对象。

使用 `array_index_mapping: true` + 字段的 `position` 按位置索引提取：

| 配置 | 值 | 说明 |
|------|-----|------|
| `root_path` | `""` | JSON 本身是数组（可选，留空即可） |
| `array_index_mapping` | `true` | 启用按位置索引模式 |
| `fields[].position` | `0`, `1`, `2`... | 数组中该字段对应的位置 |

**示例 JSON**：
```json
[
  [1664582400000, 20954],
  [1667260800000, 18549]
]
```

**对应的 YAML 配置**：
```yaml
parser:
  type: json
  root_path: ""
  array_index_mapping: true
  fields:
    - name: timestamp
      position: 0
    - name: value
      position: 1
    - name: game_id
      value: "{steam_id}"     # 配合 iterate 使用
```

---

### 数据过滤 filters

在 `parser` 下可配置 `filters`，对解析后的数据进行筛选，支持 `tail`、`head`、`where` 三种方式。

| 过滤方式 | 类型 | 说明 |
|----------|------|------|
| `skip_lines` | int | 跳过前 N 行（如跳过表头） |
| `head: N` | int | 只保留前 N 条 |
| `tail: N` | int | 只保留最后 N 条 |
| `where` | list | 按字段条件过滤（支持 `>`, `<`, `>=`, `<=`, `==`, `!=`, `in`, `contains`） |

**where 运算符**：

| op | 含义 | 示例 value |
|----|------|-----------|
| `">"` | 大于 | `1675000000000` |
| `"<"` | 小于 | `10000` |
| `">="` | 大于等于 | `0` |
| `"<="` | 小于等于 | `100` |
| `"=="` | 等于 | `"active"` |
| `"!="` | 不等于 | `null` |
| `"in"` | 在列表中 | `["a","b"]` |
| `"contains"` | 字符串包含 | `"keyword"` |

**示例**：

```yaml
# 只保留最近 5 条
parser:
  filters:
    tail: 5

# 只保留 peak_players > 5000 的数据
parser:
  filters:
    where:
      - field: peak_players
        op: ">"
        value: 5000

# tail + where 组合使用
parser:
  filters:
    tail: 10
    where:
      - field: stat_ts
        op: ">"
        value: 1675000000000
      - field: peak_players
        op: ">="
        value: 1000
```

> **注意**：多个 `where` 条件之间是 **AND** 关系，所有条件都满足才保留。
> `filters` 适用于 `parse_array()`（含 `root_path` 模式和 `array_index_mapping` 模式）和 `parse_sdk_mapping()`。

---

### ② HTML/CSS 解析 (type: css_selector)

使用 CSS 选择器从 HTML 中提取元素（**单记录模式**）：

| CSS 选择器示例 | 匹配 |
|----------------|------|
| `"h1.title"` | `<h1 class="title">...</h1>` |
| `"div.content > p"` | 直接子元素 |
| `"a.link"` | 超链接 |
| `"ul li:first-child"` | 第一个列表项 |

```yaml
parser:
  type: css_selector
  fields:
    - name: title
      selector: "h1.article-title"
      strip: true

    - name: author
      selector: "span.author-name"

    - name: link_url
      selector: "a.read-more"
      attr: "href"              # 提取属性值而非文本

    - name: paragraphs
      selector: "div.content p"
      multiple: true            # 多元素返回列表
      clean:
        remove_html: true
```

#### HTML 表格多行解析 (type: html_table)

适用于 HTML 页面中 `<table>` 的每一行 `<tr>` 是一条记录的场景。

| 配置 | 值 | 说明 |
|------|-----|------|
| `type` | `html_table` | 触发表格解析模式 |
| `row_selector` | `"table.common-table tbody tr"` | 定位每行的 CSS 选择器 |
| `fields[].column` | `0`, `1`, `2`... | 该字段在行内 `<td>` 的位置 |
| `fields[].selector` | CSS 选择器 | 也可以在当前行内用选择器提取 |

**示例 HTML**：
```html
<table class="common-table">
  <tbody>
    <tr><td>2026-07</td><td>123,456</td><td>200,000</td></tr>
    <tr><td>2026-06</td><td>110,000</td><td>190,000</td></tr>
  </tbody>
</table>
```

**对应的 YAML 配置**：
```yaml
parser:
  type: html_table
  row_selector: "table.common-table tbody tr"
  fields:
    - name: month
      column: 0
    - name: avg_players
      column: 1
    - name: peak_players
      column: 2
    - name: game_id
      value: "{steam_id}"     # 配合 iterate 使用
```

**工作原理**：
1. 用 `row_selector` 选中所有 `<tr>` 行
2. 对每行用 `column` 选 `<td>` 位置提取文本
3. 批量 UPSERT 写入（自动走批量模式）

---

### ③ SDK 字段映射 (type: sdk_mapping)

将 SDK 返回的原始字段名映射为业务表列名：

```yaml
parser:
  type: sdk_mapping
  fields:
    - name: trade_date          # 数据库列名
      source: "日期"            # SDK 原始字段名

    - name: open_price
      source: "开盘"
      to_number: true

    - name: code
      value: "000001"           # 静态值（SDK 数据中没有的字段）
```

---

### 字段清洗规则

清洗规则可以直接写在字段上，也可以用 `clean` 子节点包裹：

| 规则 | 类型 | 说明 |
|------|------|------|
| `strip` | boolean | 去除首尾空白（默认 `true`） |
| `truncate_left` | int | 保留左侧 N 个字符（截断右侧多余） |
| `truncate_right` | int | 保留右侧 N 个字符（截断左侧多余） |
| `trim_whitespace` | boolean | 压缩多余空白为一个空格 |
| `remove_html` | boolean | 去除残留 HTML 标签和实体 |
| `to_number` | boolean | 转换为数字（int/float） |
| `to_datetime` | boolean | 转换为标准日期格式 |
| `date_format` | string | 输入日期优先匹配格式 |
| `date_output_format` | string | 输出日期格式（默认 `%Y-%m-%d %H:%M:%S`） |
| `default` | any | 字段不存在时的默认值 |
| `regex_extract` | string | 正则提取（取第一个捕获组） |
| `regex_replace` | list | 正则替换（多组 pattern/replacement） |

```yaml
# 清洗规则示例
parser:
  fields:
    - name: title
      path: "data.title"
      strip: true
      trim_whitespace: true
      remove_html: true
      default: "无标题"

    - name: price
      path: "data.price"
      to_number: true
      default: 0

    - name: publish_date
      path: "data.publishTime"
      to_datetime: true
      date_format: "%Y-%m-%dT%H:%M:%S"

    - name: phone
      path: "data.contact"
      regex_extract: "电话[:：](\\d{11})"

    - name: clean_content
      path: "data.content"
      regex_replace:
        - pattern: "<[^>]+>"
          replacement: ""
        - pattern: "\\s+"
          replacement: " "
```

---

## 反反爬配置 anti_spider

- **类型**：`dict`
- **必填**：否
- **说明**：可开关的反爬应对策略

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | boolean | `false` | 总开关，关闭则所有策略不生效 |
| `delay` | [min, max] | `[1, 3]` | 请求前随机延迟范围（秒） |
| `rotate_user_agent` | boolean | `false` | 每次请求随机更换 User-Agent |
| `use_proxy` | boolean | `false` | 是否使用代理 |
| `proxies` | string[] | `[]` | 代理地址列表 |

```yaml
anti_spider:
  enabled: true
  delay: [2, 5]               # 随机延迟 2~5 秒
  rotate_user_agent: true
  use_proxy: true
  proxies:
    - "http://proxy1.example.com:8080"
    - "http://proxy2.example.com:8080"
```

---

## 重试配置 retry

- **类型**：`dict`
- **必填**：否

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_attempts` | int | `3` | 最大尝试次数（含首次） |
| `backoff_base` | float | `2.0` | 退避底数，间隔为 base⁰ → base¹ → base² |

**退避时间示例**（base=2，max=3）：
```
第1次失败 → 等待 1s (2⁰)
第2次失败 → 等待 2s (2¹)
第3次失败 → 不再重试，抛出异常
```

```yaml
retry:
  max_attempts: 5
  backoff_base: 3.0            # 间隔: 1s → 3s → 9s → 27s
```

> **注意**：仅对网络错误和 5xx 状态码重试，4xx 不重试。

---

## 数据库数据源配置 db

数据库数据源支持从外部 **SQLite** 或 **MySQL** 数据库中执行 SQL 查询，将结果集迁移到系统内部的 SQLite 业务表中。常用于系统间数据迁移、临时数据导入等场景。

### 依赖

- SQLite：Python 标准库 `sqlite3`，无需额外依赖
- MySQL：需要 `pymysql` 库
  ```bash
  pip install pymysql
  ```

### SQLite 配置示例

```yaml
name: "SQLite数据迁移"
type: db
db:
  type: sqlite
  path: "data/source.db"           # 源数据库路径
  query: "SELECT * FROM daily_trades WHERE date >= '2026-01-01'"
schedule: "0 3 * * *"
target_table: "migrated_trades"
table_schema:
  columns:
    - name: id
      type: INTEGER
      constraint: PRIMARY KEY AUTOINCREMENT
    - name: trade_date
      type: TEXT
    - name: code
      type: TEXT
    - name: open_price
      type: REAL
    - name: close_price
      type: REAL
    - name: volume
      type: REAL
    - name: crawled_at
      type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
  indexes:
    - name: idx_code_date
      columns: [code, trade_date]
      unique: true
parser:
  type: sdk_mapping                # 查询结果已是 list[dict]，透传映射
  fields:
    - name: trade_date
      source: trade_date            # 映射 SQL 查询结果的列名
    - name: code
      source: code
    - name: open_price
      source: open
      to_number: true
    - name: close_price
      source: close
      to_number: true
    - name: volume
      source: volume
      to_number: true
```

### MySQL 配置示例

```yaml
name: "MySQL数据迁移"
type: db
db:
  type: mysql
  host: "192.168.1.100"
  port: 3306
  user: "reader"
  password: "${MYSQL_PWD}"         # 支持 ${ENV_VAR} 环境变量引用
  database: "source_db"
  query: "SELECT * FROM trades WHERE created_at >= '2026-07-01'"
schedule: "0 4 * * *"
target_table: "migrated_trades"
table_schema: {...}
parser:
  type: sdk_mapping
  fields:
    - name: code
      source: stock_code
    # ...
```

### 配置项说明

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `type` | string | `sqlite` | 数据库类型：`sqlite` 或 `mysql` |
| `query` | string | - | **必填**，要执行的 SQL 查询语句 |
| `path` | string | - | SQLite 数据库文件路径（SQLite 必填） |
| `host` | string | `localhost` | MySQL 主机地址 |
| `port` | int | `3306` | MySQL 端口 |
| `user` | string | - | MySQL 用户名 |
| `password` | string | - | MySQL 密码，支持 `${ENV_VAR}` 环境变量 |
| `database` | string | - | MySQL 数据库名（MySQL 必填） |

### 安全注意事项

- **密码保护**：密码支持 `${MYSQL_PWD}` 格式的环境变量引用，不在 YAML 中明文存储
  ```bash
  # 设置环境变量后运行
  export MYSQL_PWD=your_password
  python main.py --run-once "MySQL数据迁移"
  ```
- **只读连接**：系统仅执行 SELECT 查询，不会修改源数据库
- **连接即关**：查询完成后立即关闭连接，不保持长连接

---

## 文件数据源配置 file

文件数据源支持从 **CSV** 和 **Excel** 文件中读取数据，用于离线任务和历史数据补录。文件读取后返回 `list[dict]`，通过 `sdk_mapping` 解析器做字段映射后写入数据库。

### 依赖

- CSV：Python 标准库 `csv`，无需额外依赖
- Excel：需要 `openpyxl` 库
  ```bash
  pip install openpyxl
  ```

### CSV 配置示例

假设 `data/offline/steam_players.csv` 内容如下：

```csv
steam_id,stat_ts,peak_players
1974050,1664582400000,20954
1974050,1667260800000,18549
```

对应的任务 YAML：

```yaml
name: "Steam玩家离线数据补录"
type: csv
file:
  format: csv
  path: "data/offline/steam_players.csv"
  encoding: utf-8
schedule: "0 2 * * *"            # 定时任务或手动 --run-once
target_table: "steam_game_peak_players_hourly"
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
  type: sdk_mapping               # 文件数据已是 dict，用 sdk_mapping 透传
  fields:
    - name: steam_id
      source: steam_id             # 映射 CSV 列名
      to_number: true
    - name: stat_ts
      source: stat_ts
      to_number: true
    - name: peak_players
      source: peak_players
      to_number: true
```

### Excel 配置示例

```yaml
name: "A股历史数据补录"
type: excel
file:
  format: excel
  path: "data/offline/stock_history.xlsx"
  sheet_name: "daily"              # 工作表名；也可用索引如 0
schedule: "0 3 * * *"
target_table: "stock_daily"
table_schema: {...}
parser:
  type: sdk_mapping
  fields:
    - name: code
      source: "股票代码"           # 映射 Excel 表头列名
    - name: trade_date
      source: "交易日期"
    - name: open
      source: "开盘价"
      to_number: true
    # ...
```

### 注意事项

- CSV/Excel 的第一行默认为表头（列名），系统用 `DictReader` / `openpyxl` 将表头作为 dict 的 key
- `sdk_mapping` 解析器不修改原数据，仅做字段映射（`source` 映射原始列名 → `name` 数据库列名）
- 数据走与 SDK/HTTP 完全相同的 cleanser → 批量 UPSERT 流水线，无需额外处理
- 配合 `--run-once` 实现手动补数据：
  ```bash
  python main.py --run-once "Steam玩家离线数据补录"
  ```

---

## SDK 数据源配置 provider

- **类型**：`dict`
- **必填**：`type: sdk` 时必填

| 属性 | 类型 | 说明 |
|------|------|------|
| `module` | string | 要导入的 Python 模块名（如 `"akshare"`） |
| `function` | string | 要调用的函数名（如 `"stock_zh_a_hist"`） |
| `params` | dict | 传给函数的参数 |

```yaml
type: sdk
provider:
  module: "akshare"
  function: "stock_zh_a_hist"
  params:
    symbol: "000001"
    period: "daily"
    start_date: "20260101"
    end_date: "20260718"
    adjust: "qfq"
```

**SDK 返回数据格式支持**：
- pandas DataFrame → 自动转 `list[dict]`
- `list[dict]` → 直接使用
- `dict` → 包装为 `[dict]`
- 标量值 → 包装为 `[{"value": ...}]`

---

## 完整示例

### 示例一：HTML 网页采集

```yaml
name: "新闻标题采集"
type: web
method: GET
url: "https://news.example.com/latest"
schedule: "0 */2 * * *"
target_table: "news_headlines"
table_schema:
  columns:
    - name: id
      type: INTEGER
      constraint: PRIMARY KEY AUTOINCREMENT
    - name: title
      type: TEXT
    - name: link
      type: TEXT
    - name: source_url
      type: TEXT NOT NULL
    - name: crawled_at
      type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
  indexes:
    - name: idx_link
      columns: [link]
      unique: true
parser:
  type: css_selector
  fields:
    - name: title
      selector: "h2.news-title a"
      strip: true
    - name: link
      selector: "h2.news-title a"
      attr: "href"
    - name: source_url
      value: "{url}"
anti_spider:
  enabled: true
  delay: [2, 5]
retry:
  max_attempts: 3
  backoff_base: 2.0
```

### 示例二：JSON API 采集（含动态参数）

```yaml
name: "天气数据采集"
type: api
method: GET
url: "https://api.weather.example.com/forecast?city=beijing&date={today}"
schedule: "0 6,18 * * *"
target_table: "weather_forecast"
table_schema:
  columns:
    - name: id
      type: INTEGER
      constraint: PRIMARY KEY AUTOINCREMENT
    - name: city
      type: TEXT NOT NULL
    - name: forecast_date
      type: TEXT NOT NULL
    - name: temp_high
      type: REAL
    - name: temp_low
      type: REAL
    - name: weather_desc
      type: TEXT
    - name: source_url
      type: TEXT NOT NULL
    - name: crawled_at
      type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
  indexes:
    - name: idx_city_date
      columns: [city, forecast_date]
      unique: true
parser:
  type: json
  fields:
    - name: city
      value: "beijing"
    - name: forecast_date
      path: "date"
    - name: temp_high
      path: "temperature.max"
      to_number: true
    - name: temp_low
      path: "temperature.min"
      to_number: true
    - name: weather_desc
      path: "description"
    - name: source_url
      value: "{url}"
retry:
  max_attempts: 3
  backoff_base: 2.0
```

### 示例三：SDK 数据采集

```yaml
name: "A股日线数据"
type: sdk
schedule: "0 2 * * *"
target_table: "stock_daily"
table_schema:
  columns:
    - name: id
      type: INTEGER
      constraint: PRIMARY KEY AUTOINCREMENT
    - name: code
      type: TEXT NOT NULL
    - name: trade_date
      type: TEXT NOT NULL
    - name: open
      type: REAL
    - name: close
      type: REAL
    - name: volume
      type: REAL
    - name: crawled_at
      type: TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
  indexes:
    - name: idx_code_date
      columns: [code, trade_date]
      unique: true
provider:
  module: "akshare"
  function: "stock_zh_a_hist"
  params:
    symbol: "000001"
    period: "daily"
    start_date: "20260101"
    end_date: "20260718"
    adjust: "qfq"
parser:
  type: sdk_mapping
  fields:
    - name: code
      value: "000001"
    - name: trade_date
      source: "日期"
    - name: open
      source: "开盘"
      to_number: true
    - name: close
      source: "收盘"
      to_number: true
    - name: volume
      source: "成交量"
      to_number: true
```

---

## 常见问题

### Q: YAML 文件可以包含多个任务吗？

可以。用 YAML 列表格式（`-` 开头）：

```yaml
- name: "任务一"
  type: api
  ...

- name: "任务二"
  type: web
  ...
```

### Q: 修改 YAML 后需要重启系统吗？

是的。当前版本需要在修改配置文件后重启 `python main.py`，系统启动时会重新加载所有任务配置。

### Q: cron 表达式支持几位的？

5 位标准格式（分 时 日 月 星期），与 Linux crontab 一致。

### Q: 如何调试一个任务？

```bash
# 单次执行并查看详细日志
python main.py --run-once "任务名" --log-level DEBUG
```

### Q: 索引中的 unique 和业务去重的关系？

- `unique: true` 的索引由 SQLite 保证唯一性，重复数据会触发 `ON CONFLICT DO UPDATE`
- 这是**数据库层面的去重**，无需在应用层额外处理
- 对于 SDK 类型任务来说，这是唯一的去重机制

### Q: 支持 POST 请求吗？

支持 `method: POST`，可在 YAML 中配置 `body` 或 `json_body`（需在 fetcher 代码中适配）。

### Q: SDK provider 中 `value` 与 `source` 能同时使用吗？

能，但 `value` 优先级更高。如果同时配置，`source` 会被忽略。

### Q: URL 模板变量在哪些地方可用？

目前 URL 模板变量在 `url` 字段中可用，解析后的完整 URL 会通过 `{url}` 占位符传递给 `parser.fields` 中的 `source_url` 等字段。

### Q: 文件数据源的 CSV/Excel 表头如何处理？

CSV 使用第一行作为列名（通过 `csv.DictReader`），Excel 使用第一行作为列名（通过 `openpyxl`）。如果 Excel 第一行不是表头，需要先在文件中调整或删除无效行。

### Q: {today} 的时间基准是什么？

系统本地时间。每天零点后执行会使用当天的日期。
