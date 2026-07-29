# 爬虫系统变更日志

> 版本：v1.4 | 更新日期：2026-07-29

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