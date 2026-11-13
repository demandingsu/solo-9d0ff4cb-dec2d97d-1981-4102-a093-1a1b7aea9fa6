# 市政工程投标公告采集与异议追踪 API

面向两类监督场景的采集服务：

1. **投标截止时间核对**：接收公告目录 URL，沿分页抓取详情页，按招标编号归集**原公告 / 改期公告 / 撤回公告**，沿有效公告链计算当前截止时间；并行改期时间不一致时如实返回冲突，不猜测结果。
2. **结果公示异议催办**：给定公示编号、核查时点与异议记录目录 URL，沿分页读取**异议事项 / 异议答复 / 答复撤销**，按核查时点输出逐事项的已答复 / 未答复 / 待核实状态与证据链接，帮助评标监督员在公示期满前定位尚未得到有效答复的异议。

## 业务规则

1. **归集**：以招标编号分组。原公告为链根；每条改期公告通过“依据公告”注明其依据的公告（原公告或上一条改期）；撤回公告通过“撤回公告”字段指定被撤回的改期。
2. **撤回级联失效**：撤回指定改期后，该改期及其全部后续改期（沿依据链传递依赖）标记为失效并注明原因；失效公告不参与截止时间计算。
3. **有效链与截止时间**：从原公告出发，沿有效改期逐级下行，链末端公告的投标截止时间即当前有效截止时间，返回结果同时给出该公告的来源链接。
4. **并行改期冲突**：同一有效依据下存在多条有效并行改期时：
   - 截止时间一致 → 取该时间并在 `notes` 说明；
   - 截止时间不一致 → `status=conflict`，`current_deadline=null`，`conflict` 列出全部冲突公告及其时间与来源链接，**不猜结果**。
5. **重新采集**：再次采集成功时用完整新结果**整体替换**旧结果（临时文件 + 原子 rename）；抓取或解析任一页面失败时返回 502 且**不触碰存储**，旧结果完整保留。

## 异议追踪业务规则

1. **三类记录**：异议事项（`O...`）、异议答复（`R...`，列明所答复的异议编号，一份可覆盖多项）、答复撤销（`X...`，指定被撤销的答复编号）。每条记录保留记录编号、发布时间、引用关系与来源链接。
2. **核查时点**：仅统计发布时间**不晚于**核查时点（含）的记录；晚于时点的记录不参与判定，也不作为证据。
3. **答复口径**：答复仅覆盖其列明的事项。撤销指定答复后该答复失效；事项仍有其他未撤销有效答复时，**继续算已答复**；唯一有效答复被撤销则回到未答复。
4. **待核实（不猜测）**：
   - 同一记录编号跨页出现且内容（标题/正文/发布时间/引用/撤销目标）冲突 → 受影响事项 `pending_verify`；
   - 答复引用了不存在的异议、撤销指定了不存在的答复等**引用缺失** → 相关事项 `pending_verify`，撤销目标缺失时该撤销被忽略并在 `data_issues` 注明。
5. **分页读取失败**：HTTP 仍返回 200，但 `status=incomplete`、`completed=false`，响应只包含**已成功采集的证据**；所有事项一律 `pending_verify`，**绝不把任何事项断言为已答复或未答复**，`incomplete_reason` 注明失败页面。
6. 逐事项结果包含 `status`、有效答复编号、全部相关证据（含被撤销答复与撤销记录，标注 `valid` / `invalid_reason`）及来源 URL；全局数据问题汇总在 `data_issues`。

### `POST /api/objections/track` 异议追踪

```json
// 请求
{
  "publication_no": "GS-2026-007",
  "checkpoint": "2026-11-12 00:00:00",
  "catalog_url": "http://localhost:8000/sample/objections/catalog/page_1.html"
}
// 成功响应 200（status=tracked；分页失败时 status=incomplete）
{
  "status": "tracked",
  "collected_at": "2026-11-13T03:00:00+00:00",
  "replaced_previous": false,
  "result": {
    "publication_no": "GS-2026-007",
    "checkpoint": "2026-11-12 00:00:00",
    "completed": true, "incomplete_reason": null,
    "total_pages": 3, "total_records": 17,
    "counts": { "answered": 3, "unanswered": 2, "pending_verify": 2 },
    "items": [
      {
        "record_id": "O003", "title": "关于中标候选人业绩材料的异议",
        "published_at": "2026-11-03 09:00", "status": "unanswered",
        "source_url": "http://.../detail/O003.html",
        "valid_reply_ids": [],
        "evidence": [
          { "record_id": "R002", "type": "异议答复", "valid": false,
            "invalid_reason": "答复已被撤销记录撤销：X001", "source_url": "..." },
          { "record_id": "X001", "type": "答复撤销", "revokes_id": "R002", "source_url": "..." }
        ],
        "issues": ["已有答复记录但均被撤销，当前无有效答复"]
      }
    ],
    "data_issues": ["记录编号 O004 在第 2、3 页出现且内容冲突 ...", "..."]
  }
}
```

其他：`GET /api/objections`（最近一次追踪结果）、`GET /api/objections/raw`（含未完成时的部分原始证据）。核查时点格式非法或 URL 非法时返回 422。

### 异议样例覆盖的场景（公示编号 GS-2026-007）

| 事项 | 场景 | 预期状态 |
|---|---|---|
| O001 / O002 | 一份答复 R001 同时覆盖两项 | 均 `answered` |
| O003 | 唯一答复 R002 被 X001 撤销 | `unanswered`（催办对象） |
| O004 | 第 2、3 页同号内容冲突（O004.html / O004-conflict.html） | `pending_verify` |
| O005 | 答复 R004 同时引用不存在的 O999 | `pending_verify` |
| O006 | 被 R005 引用但事项记录本身缺失 | 全局 `data_issues` 注明 |
| O007 | 无任何答复 | `unanswered`（催办对象） |
| O008 | 答复 R008 被撤销后仍有有效答复 R009 | `answered` |

## 目录结构

```
app/                          FastAPI 应用
  main.py                     路由：采集、查询、异议追踪、离线样例静态挂载
  crawler.py                  公告分页抓取（httpx 异步 + 并发限制 + 上限/循环保护）
  parser.py                   公告目录页/详情页 HTML 解析
  chain.py                    公告归集、撤回级联失效、并行冲突判定
  storage.py                  公告采集结果 JSON 存储（异步锁、原子替换）
  objection_crawler.py        异议记录分页抓取（失败保留已采集证据，不丢弃）
  objection_parser.py         异议目录页/详情页 HTML 解析
  objection_tracker.py        时点过滤、引用校验、跨页冲突、撤销有效性与逐事项判定
  objection_storage.py        异议追踪结果 JSON 存储（独立文件 objections.json）
  objection_models.py         异议相关 Pydantic 模型与请求校验
  models.py / config.py       公告模型与全局配置
sample_site/                  可离线访问的分页 HTML 样例（相对链接、无外部资源）
  index.html、catalog/、detail/               公告样例（18 条）
  objections/index.html、catalog/、detail/    异议记录样例（17 个目录条目）
scripts/generate_samples.py             公告样例生成脚本
scripts/generate_objection_samples.py   异议样例生成脚本（结构即解析契约）
tests/                        29 项单元 + 集成测试
Dockerfile / docker-compose.yml
```

## 快速开始（Docker Compose）

```bash
docker compose up --build
# API:      http://localhost:8000
# 交互文档:  http://localhost:8000/docs
# 公告样例:  http://localhost:8000/sample  （也可直接用浏览器打开 sample_site/index.html）
# 异议样例:  http://localhost:8000/sample/objections
```

采集与追踪结果持久化在 `tender-data` 卷内（`/data/store.json`、`/data/objections.json`）。

## 本地无 Docker 运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_samples.py             # 公告样例（已随仓库提供，修改后重新生成）
python scripts/generate_objection_samples.py   # 异议记录样例
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 接口

### `POST /api/collect` 触发采集

```json
// 请求
{ "catalog_url": "http://localhost:8000/sample/catalog/page_1.html" }
// 成功响应 200
{
  "status": "collected",
  "catalog_url": "...",
  "total_pages": 3,
  "total_announcements": 18,
  "collected_at": "2026-11-11T10:46:32.894225+00:00",
  "replaced_previous": true
}
// 失败响应 502（旧结果保留）
{ "detail": { "error": "抓取失败 ...", "previous_result_retained": true,
              "message": "采集失败，已保留上一次成功采集的结果" } }
```

### `GET /api/tenders` 各标段截止时间汇总

```json
[
  { "tender_code": "ZG-2026-001", "project_name": "市政道路改造工程",
    "status": "ok", "current_deadline": "2026-12-04 09:30",
    "conflict": null, "notes": ["当前截止时间依据有效公告链末端公告 A018（改期公告）"] },
  { "tender_code": "ZG-2026-003", "status": "conflict", "current_deadline": null,
    "conflict": [ {"announcement_id": "A012", "deadline": "2026-12-05 09:30",
                   "published_at": "2026-11-12 09:00", "source_url": "..."},
                  {"announcement_id": "A013", "deadline": "2026-12-08 09:30", ...} ] }
]
```

### `GET /api/tenders/{招标编号}` 单标段完整公告链

每条公告带 `valid` / `invalid_reason` / `source_url`，可直接核对来源。例：

```
A011 改期公告 valid=False 被撤回公告直接撤回
A017 改期公告 valid=False 所依据的改期公告 A011 已失效（撤回级联）
```

其他：`GET /api/raw`（最近一次采集原始数据）、`GET /health`。

## 内置样例覆盖的场景

| 招标编号 | 场景 | 预期结果 |
|---|---|---|
| ZG-2026-001 | 原公告 + 4 次串行改期 | 截止时间取末次改期 `2026-12-04 09:30` |
| ZG-2026-002 | 第二次改期被撤回，第三次改期依赖它 | 撤回 + 级联失效，回退到第一次改期 `2026-11-26 09:30` |
| ZG-2026-003 | 同一依据下两条并行改期、时间不同 | `conflict`，列出 A012/A013 两个时间与来源 |
| ZG-2026-004 | 第一次改期被撤回、第二次级联失效 | 回退到原公告 `2026-11-21 09:30` |

## 页面结构约定（采集目标需与样例一致）

**公告页面**

- 目录页：`<ul class="catalog">` 内每条一个详情 `<a href>`；分页用 `<a rel="next" href="...">`，无该链接即末页。
- 详情页：`<table id="announcement-meta">` 内 `th/td` 键值对，字段为
  公告编号、招标编号、公告类型（招标公告/改期公告/撤回公告）、项目名称、发布时间、
  投标截止时间、依据公告（改期必填）、撤回公告（撤回必填）。

**异议记录页面**

- 目录页：`<ul class="objection-catalog">` 内每条一个详情 `<a href>`；分页同样用 `<a rel="next">`。
- 详情页：`<table id="objection-meta">` 内 `th/td` 键值对，字段为
  记录编号、公示编号、记录类型（异议事项/异议答复/答复撤销）、标题、发布时间、
  答复事项（答复必填，多个编号以顿号/逗号/分号/空白分隔）、撤销答复（撤销必填）；
  正文摘要位于 `<div id="objection-body">`，用于跨页同号内容冲突比对。

## 测试

```bash
pip install -r requirements.txt
pytest -q
# 29 passed：
#   公告链路规则 7 项 + 公告 API 5 项
#   异议判定规则 11 项（时点过滤、多事项答复、撤销后仍有效、跨页冲突、引用缺失、未完成采集不断言等）
#   异议 API 6 项（全流程、持久化/替换、时点过滤、目录页失败、详情页失败、离线样例可访问）
```

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATA_DIR` | `./data` | 采集与追踪结果存储目录（`store.json`、`objections.json`） |
| `REQUEST_TIMEOUT` | `10` | 单次 HTTP 超时秒数 |
| `MAX_PAGES` | `100` | 目录分页上限（防循环/异常） |
| `MAX_CONCURRENCY` | `8` | 详情页抓取并发数 |
