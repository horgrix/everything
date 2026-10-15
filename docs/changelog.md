# 爬虫系统变更日志

> 版本：v1.7 | 更新日期：2026-10-14

---

## v1.7 动态参数增强：共享/分层参数 + 通用 `${ENV}` + 查库参数（2026-10-14 新增）

### 共享/分层参数（P1）

- 新增 `crawler/params.py`：`ParamResolver` 按「全局 `config/params.yaml` → 分组 `config/tasks/<group>/params.yaml`（group 取任务 `_group`，即目录名）→ 任务 YAML 顶层 `params:`」三层合并，优先级低 → 高覆盖。
- 合并规则：同 key 两边都是 dict 时递归合并（`deep_merge`）；list / 标量由上层直接覆盖。
- 每次 `resolve()` 都重新读文件，改 `params.yaml` **无需重启进程**即生效；分组文件缺失 / 无 `_group` 时静默跳过。
- `task_manager/loader.py` 扫描时跳过 `params.yaml`，不将其当作任务配置加载。

### 通用 `${ENV_VAR}`（P2）

- `crawler/template.py` 的 `URLTemplate.resolve` 新增 `${ENV_VAR}` 替换：凡走模板解析的字符串均生效（`url`、`params` 值、`headers` 静态值、parser `value`、`db.query`、sink 的 `url`/`path`）。
- **缺失的环境变量保留 `${VAR}` 原样并写 warning**（不再静默变空串）；`${ENV}` 先于 `{var}` 替换。

### 查库参数（P4）

- `crawler/params.py` 新增查库参数：顶层 param 值为含 `query` 键的 dict → 查库取值。`db` 缺省 `{type: sqlite, path: crawler.db}`（本地），也可写 `{type: mysql, host/port/user/password/database}` 查外部库；复用 `DbSource`（`password` 支持 `${ENV}`）。
- 取值：默认第一行第一列标量；`column: <列名>` 指定列；`many: true` 返回该列所有行的 list（未指定 column 时取第一列）。
- 兜底优先级：`required: true`（失败 / 空结果 / 取值失败时抛错）> `default`（告警后用其值）> 都没有则置 `None` + 告警。
- `query` 串支持 `${ENV}` / `{today}` 等模板变量，可引用已解析的普通参数；执行走 `asyncio.to_thread`（不阻塞事件循环），`ParamResolver.resolve` 为 `async`。
- 限制：查库参数之间不能互相引用；仅顶层含 `query` 的 dict 才查库；SQL 由模板拼接、无占位符绑定（仅适用可信配置）；`many: true` 遇空结果仍走兜底。

**受影响文件**：`crawler/params.py`（新增）、`crawler/template.py`、`crawler/engine.py`、`app.py`、`api/routes/tasks.py`、`task_manager/loader.py`、`docs/task_config_guide.md`（v2.3 → v2.4）、`README.md`

---

## v1.6 可插拔 OutputSink + 远程 MySQL / 文件输出（2026-10-11 新增）

### OutputSink 输出架构

- 新增 `crawler/sinks/` 包（与 `crawler/sources/` 对称）：`OutputSink` 抽象 + `SinkRegistry` + `SinkResult`，各 sink 认领一个 `output` 配置键。
- 4 个内置 sink：`SqliteSink`（`target_table`）、`ApiSink`（`target_api`）、`MysqlSink`（`target_mysql`）、`FileSink`（`target_file`）；`create_default_sink_registry()` 构造注册表。
- `DataPipeline.process()` 改为遍历注册表分发（先 `prepare` 再 `write`），新增 `flush_outputs()` 收尾；一个 output 可同时命中多个 sink。
- 批处理下沉到 sink 内部：`ApiSink` / `MysqlSink` 自持缓冲，按自身 `batch_size` flush；`engine` 删除 `collected_rows` / `_flush_output_api` 等 target_api 专属批处理。

### 新增输出目标

- `target_mysql`：写入远程 MySQL。**不做任何 SQL 方言映射**，`table_schema.columns` 的 `type`/`constraint` 由用户按原生 MySQL 方言书写并原样拼进 `CREATE TABLE`；`password` 支持 `${ENV_VAR}`；写入用 `INSERT ... ON DUPLICATE KEY UPDATE`（排除主键/自增列）。每个 output 自带 `mysql` 连接块。
- `target_file`：导出 CSV / JSONL / JSON / Excel，`path` 支持 `{today}` 等模板变量，`mode` 支持 `overwrite` / `append`。
- 约束：一个 output 只能挂一个 SQL sink（`target_table` 或 `target_mysql`）；`target_api` / `target_file` 可与 SQL sink 共存。
- `task_manager/loader.py` 的 output 校验从「必须有 target_table 或 target_api」改为「至少命中一个已注册 sink 的 key」。

**受影响文件**：`crawler/sinks/`（新增 base/sqlite_sink/api_sink/mysql_sink/file_sink/__init__）、`crawler/pipeline.py`、`crawler/engine.py`、`task_manager/loader.py`、`app.py`、`api/routes/tasks.py`、`docs/task_config_guide.md`、`README.md`、`tests/test_sinks.py`（新增）、`tests/test_mysql_sink.py`（新增）、`tests/test_file_sink.py`（新增）

---

## v1.5 任务分组 + 组级文件日志（2026-10-09 新增）

### 任务分组（group）

- 任务目录从旧版「system 定时 / user 手动两级目录」改为「目录名 = 分组」结构：`config/tasks/<group>/*.yaml`（组根 = system）、`config/tasks/<group>/manual/*.yaml`（= user）。
- `group` 由目录名推导，不写入 YAML；`crawl_tasks` 表新增 `group` 列 + `idx_tasks_group` 索引，`upsert_task` 带 group。
- `scheduler` 新增 `trigger_group(group)`；API 新增 `GET /api/tasks/groups`、`POST /api/tasks/groups/{group}/trigger`、`/enable`、`/disable`。

### 组级文件日志（TaskLogHandler）

- 新增 `crawler/task_log.py`：`TaskLogHandler` 覆盖式写 `logs/<group>/{data,runtime,error}.log`。
- 任务级 `logging: {enabled, data, runtime, error}`，默认 error/runtime 开、data 关。
- `engine._fetch_data` 成功后写 data.log；`scheduler._execute` / `main.run_once` 完成/失败写 runtime.log 与 error.log。

### 迁移脚本 + gitignore

- 新增 `scripts/migrate_groups.py`：旧目录 → 分组目录一次性迁移（默认 dry-run，`--apply` 执行）。
- `.gitignore`：旧版 user 手动目录忽略项改为 `config/tasks/**/manual/`。

**受影响文件**：`task_manager/loader.py`、`task_manager/schema.py`、`storage/database.py`、`storage/schema.sql`、`scheduler/scheduler.py`、`crawler/engine.py`、`crawler/task_log.py`（新增）、`api/routes/tasks.py`、`api/deps.py`、`static/tasks.html`、`main.py`、`scripts/migrate_groups.py`（新增）

---

## 一、配置增强（v1.4 新增）

### 1.1 全局自定义参数 `params`

YAML 顶层新增 `params` 字典，可在 URL、db.query、value 占位符等任意位置通过 `{param_name}` 引用：

```yaml
params:
  steam_id_list: "1974050, 2315040"
  base_url: "https://store.steampowered.com"

url: "{base_url}/charts/topselling/{region}"
```

### 1.2 内置时间变量扩展

原支持 `{today}` `{yesterday}` `{now}` `{days_ago:N}`，新增：

| 变量 | 说明 | 示例 |
|------|------|------|
| `{timestamp}` | Unix 秒级时间戳 | `1785303035` |
| `{timestamp_ms}` | Unix 毫秒级时间戳 | `1785303035000` |
| `{weeks_ago:N}` | N 周前日期 | `{weeks_ago:1}` → 7天前 |
| `{weeks_ago:N:format}` | N 周前 + 格式化 | `{weeks_ago:2:%Y%m%d}` |
| `{this_week:N}` | 本周一～周日 (N=1~7) | `{this_week:1}` → 本周一 |
| `{this_week:N:format}` | 本周 + 格式化 | `{this_week:7:%Y%m%d}` |
| `{last_week:N}` | 上周一～周日 (N=1~7) | `{last_week:1}` → 上周一 |
| `{last_week:N:format}` | 上周 + 格式化 | `{last_week:7:%Y-%m-%d}` |

> 所有 `format` 参数遵循 Python `strftime` 语法。

**受影响文件**：`crawler/template.py`

### 1.3 `iterate` 多变量笛卡尔积

**旧版（单变量，仍兼容）：**

```yaml
iterate:
  var_name: "region"
  values: [global, CN, TW]
```

**新版（多变量，笛卡尔积展开）：**

```yaml
iterate:
  - var_name: "region"
    values: [global, CN, US]
  - var_name: "page"
    values: [1, 2]
# 展开结果：3 × 2 = 6 个上下文
```

**受影响文件**：`crawler/engine.py` — `_build_iterate_contexts()` 改用 `itertools.product`

### 1.4 `element_selector` 页面级元素提取

`html_table` 解析器新增 `element_selector`，从表格外部提取页面级信息（如统计时间标题），提取结果注入 context 后可通过 `value: "{var_name}"` 引用到每行：

```yaml
parser:
  type: html_table
  row_selector: "table.data-table tbody tr"
  element_selector:           # 新增
    stat_date:
      selector: "div.stat-header span.time"
      attr: "data-ts"
      strip: true
      to_datetime: true       # 支持所有 Cleaner 清洗规则
      date_output_format: "%Y-%m-%d"
  fields:
    - name: stat_date
      value: "{stat_date}"    # 引用 element_selector 结果
```

**受影响文件**：`crawler/parser.py`（+`extract_element_vars()`）+ `crawler/engine.py`

---

## 二、任务管理 API（CRUD）

### 新增端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/tasks` | 创建任务（写入 YAML + 注册 DB + 热加载到调度器） |
| `PUT` | `/api/tasks/{name}` | 更新任务配置（覆写文件 + 重注册 + 调度器热重载） |
| `DELETE` | `/api/tasks/{name}` | 删除任务（调度器移除 + 删除 YAML + 清理 DB） |

### 请求体（POST/PUT）

```json
{
  "name": "my_task",
  "config_yaml": "name: my_task\ntype: web\nurl: https://...\nschedule: \"0 * * * *\"\n..."
}
```

### 热加载流程

- **调度器模式**（`python main.py --api`）：创建/更新即时生效，无需重启
- **纯 API 模式**（`python -m api`）：仅写文件 + DB，重启调度器后生效

**受影响文件**：`api/routes/tasks.py`，`scheduler/scheduler.py`（+`add_job()`），`api/__init__.py`（接受 scheduler 参数），`main.py`

---

## 三、数据管理 API（增删改查）

### 新增端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `PUT` | `/api/data/{table}/rows/{id_col}/{id_val}` | 按主键列更新单行 |
| `POST` | `/api/data/{table}/rows/batch` | 批量 UPSERT 插入 |
| `DELETE` | `/api/data/{table}/rows/{id_col}/{id_val}` | 按主键列删除单行 |

### 批量插入格式

```json
{
  "rows": [
    {"col1": "val1", "col2": 123},
    {"col1": "val2", "col2": 456}
  ]
}
```

使用 `INSERT ... ON CONFLICT DO UPDATE`（UPSERT），自动处理新增/更新。

**受影响文件**：`api/routes/data.py`，`static/js/api.js`（+`apiPut()` `apiDelete()`）

---

## 四、前端功能（Dashboard UI）

### 4.1 任务管理页 `tasks.html`

| 功能 | 说明 |
|------|------|
| 创建任务 | Modal 表单：任务名称 + YAML 编辑器 + 模板加载 |
| 编辑配置 | 从数据库读取原始 YAML 填入编辑器，保存后热重载 |
| 删除任务 | confirm 确认后调用 DELETE API |
| 操作列 | 👁 查看 · ✏️ 编辑 · ▶ 执行 · 🗑 删除（并排 `text-nowrap`） |

### 4.2 数据浏览页 `data.html`

| 功能 | 说明 |
|------|------|
| 编辑行 | 操作列 ✏️ 按钮 → Modal（自动识别数字列用 number 输入框）→ PUT API |
| 删除行 | 操作列 🗑 按钮 → confirm 确认 → DELETE API |
| 批量插入 | 筛选栏下方按钮 → Modal JSON textarea → POST API |
| 查询/批量按钮 | 筛选条件下方独立一行左对齐 |

### 编辑按钮技术细节

使用 `data-row-idx` + `addEventListener()` 替代 JSON-inlining，避免特殊字符导致的 HTML 转义问题。

---

## 五、Bug 修复

| 问题 | 原因 | 修复 |
|------|------|------|
| 执行任务 FOREIGN KEY 失败 | `trigger_run` 中 `task_id=0` 违反外键约束 | 先查 DB 获取真实 id，不存在时自动注册 |
| `'int' object has no attribute 'strip'` | `_to_number()` 收到已是 int 的值时仍调 `.strip()` | 开头增加 `if not isinstance(text, str): return text` |
| 编辑按钮点击无反应 | `innerHTML` 中的 `"` 被反向解码截断 attribute | 改用 `data-row-idx` + `addEventListener` |
| apiPut is not defined | 浏览器缓存旧版 `api.js` | 添加 `?v=2` / `?v=3` 版本号强制刷新 |

---

## 六、文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `crawler/template.py` | 重写 | 新增 8 个时间变量 + params 全局引用 |
| `crawler/engine.py` | 重写 | iterate 多变量笛卡尔积 + params 注入 + element_selector 编排 + 模板递归解析 |
| `crawler/parser.py` | 重写 | 新增 `extract_element_vars()` + Cleaner 清洗 |
| `crawler/cleaner.py` | 修复 | `_to_number()` 非字符串类型直接返回 |
| `scheduler/scheduler.py` | 新增方法 | `add_job()` 热加载 |
| `api/__init__.py` | 参数扩展 | `create_app()` 接受 scheduler |
| `main.py` | 连线 | 传递 scheduler 给 `create_app()` |
| `api/routes/tasks.py` | 重写 | +POST +PUT +DELETE 端点 + `CreateTaskRequest` |
| `api/routes/data.py` | 新增端点 | +PUT +POST(batch) +DELETE |
| `static/tasks.html` | 重写 | 创建/编辑/删除按钮 + Modal |
| `static/data.html` | 重写 | 编辑/删除行 + 批量插入 |
| `static/js/api.js` | 新增函数 | `apiPut()` `apiDelete()` |
| `config/tasks/_example_all_features.yaml` | 新增 | 完整功能示例配置 |

---

> 以上所有变更自版本 **41553b4** 以来累积。