---
title: 用户反馈修复与 PRD 总体复审（搜索/明细/分类选项/翻页/星号来源/流水筛选）
status: approved
category: reports
created: 2026-08-27
last-reviewed: 2026-08-27
---

# 28 用户反馈修复与 PRD 总体复审

**日期**：2026-08-27
**触发**：用户对 `/inbox` 提出 5 项具体反馈（搜索回车、明细不足、按钮/分类、翻页、星号与交易时间），并要求按 PRD 总体审视项目、识别未达标或跑偏项。
**性质**：交互缺陷修复 + PRD 合规补缺 + 文档对齐。未改动任何业务规则（退款、提现、人际定性、统计口径不变）。

## 1. 用户反馈与修复闭环

| # | 用户反馈 | 根因 | 修复 |
|---|---|---|---|
| F1 | 搜索关键词回车直接跳回顶部、无结果 | 分类区搜索表单 `hx-trigger` 未包含 `submit`，回车触发浏览器原生整页 GET 局部模板 | `_category_section.html` 改为 `hx-trigger="input changed delay:300ms, search, submit"`；新增显式「搜索」按钮；搜索与翻页保持局部刷新 |
| F2 | 待确认页看不到闲鱼等详细信息 | 分类区只显示商户/平台/方向/笔数/金额，`item_desc` 与交易时间未渲染 | 表格新增「商品/说明（样本）」与「最近交易时间」列；每组可展开查看全部笔数（时间、商品说明、原始分类、金额） |
| F3 | 按钮只有消费/收入/划转，分类只有“选择分类” | 分类候选来自已入账分类 `list_categories_used()`；首次使用时为空 | UI 按类型内置 PRD §2.2 正式分类：消费 7 个、收入 2 个、调拨免分类；保留历史自定义分类兜底；服务端校验消费/收入必须有分类、调拨清空分类 |
| F4 | 翻页没有 1、2、3 页码与跳页输入 | 两个分区只有上一页/下一页 | 新增 `app/router_support/pagination.py` 页码窗口（当前页 ±2 + 首尾页 + 省略号），模板 `_pagination.html` 统一渲染页码按钮与「跳至第 N 页」输入框，高风险区与分类区共用 |
| F5 | 商户名大量 `*`、没有交易时间 | `*` 来自支付宝账单导出的隐私脱敏；解析器只 `strip()` 原样入库，不做额外打码 | 代码审查确认无二次打码；正式库证据：230 条带 `*` 商户全部为支付宝、微信 0 条。界面通过商品说明样本/展开明细/最近时间帮助辨认；手册 FAQ 增加解释 |

### 证据（正式库只读查询，未修改正式库）

- 正式库带 `*` 商户：230 条（支付宝 230，微信 0）。
- `闲鱼` 相关来源流水 216 条；分类区 `/inbox/category?q=闲鱼` 命中 3 组，可看到对应商品说明。
- 正式库副本 HTTP 冒烟：`/`、`/inbox`、`/transactions`、`/imports`、`/rules`、`/imports/new` 全部 200。

## 2. PRD 总体复审（排除用户后来指引修改项）

以 `docs/decisions/01_refactor_spec.md` 为合同逐节核对。已排除报告 25（HTMX 局部刷新/退款候选刷新）、26（高风险分页）、27（分类分页/搜索）及当日 CSS 清理中用户已指引完成的内容。

### 2.1 复核达标（不重列，见 STATUS 历史裁决）

退款/提现/人际/中性处理、统计净额与跨期回写、导入去重、规则生命周期、批次撤销、流水详情审计——均维持历史红队验收结论。

### 2.2 本次发现并修复的偏差

| # | PRD 条款 | 偏差 | 修复 |
|---|---|---|---|
| G1 | §3.3 分组需足以辨认交易 | 分类区隐藏商品说明与时间 | 新增样本列、时间列、展开明细 |
| G2 | §2.2/§3.3 正式分类 | 分类下拉依赖“已入账分类”，首次为空 | 内置正式分类 + 历史自定义兜底 |
| G3 | §3.4 按状态筛选 | 流水页无状态筛选 | 新增“来源状态”筛选（正式库可筛 `交易成功/退款成功/已全额退款/...`） |
| G4 | §3.4 展示来源 | 列表未展示来源商品说明 | 流水列表来源列展示商品说明摘要 |
| G5 | 文档一致性 | `README.md` 仍写“阶段七等待复审”；`docs/decisions/02_architecture.md` 仍是旧 review/cleanup 架构 | README 指向 STATUS 当前真相；架构文档重写为 v2 当前产品面 |

### 2.3 仍记录的缺口/观察项（本次不实现，需用户定口径或另行立项）

1. **低置信度原因未单独建模**：§3.3 提到“未命中、低置信度、旅游等”，实现只有 `unmatched` + `observing_rule` 预填。功能语义近似但状态口径不等价；新增原因需规格先行。
2. **流水页 500 条上限无分页提示**：`list_entries_filtered(limit=500)` 会静默截断；待用户处理完 1755 条后可能不够。建议下轮做流水页分页/虚拟滚动（本次不扩大范围）。
3. **手工“退款调整”语义未闭环**：简易补账允许选 `refund` 类型，但该记录不参与统计、不关联原消费；PRD §3.4 的“退款调整”需要用户确认产品口径。
4. **htmx 走 CDN**：离线不可用，报告 25 已记录，建议本地化。
5. **概览月份切换**：报告 23 已记录，规格未要求。
6. **`pending_count` 全库口径**：报告 24 已记录，与“某月待确认”可能混淆。

## 3. 变更清单

### 后端

- `app/decisions/constants.py`：新增 `FORMAL_CATEGORIES`、`BULK_TYPES`、`CATEGORY_OPTIONS_BY_TYPE`
- `app/decisions/confirm.py`：`GroupItem` 增加 `raw_type`；`Group` 增加说明样本/首末时间；搜索扩展到 `item_desc`/`raw_type`；批量确认类型与分类校验
- `app/stats.py`：新增 `list_category_options()`（正式分类 + 历史兜底）与 `list_source_statuses()`；`list_entries_filtered()` 支持 `source_status`
- `app/routers/inbox.py`：分类候选使用正式分类列表，context 提供类型→分类映射
- `app/routers/transactions.py`：新增来源状态筛选
- `app/routers/rules.py`：规则目标分类候选使用正式分类 + 历史兜底
- `app/router_support/pagination.py`（新增）：`page_window()` 页码窗口
- `app/templates_core.py`：注册 `page_window` 到 Jinja globals

### 模板 / 样式

- `templates/_category_section.html`：搜索 submit 拦截 + 显式搜索按钮
- `templates/_category_table.html`：明细列/展开明细/正式分类 optgroup/页码与跳页
- `templates/_high_risk_section.html`：页码与跳页
- `templates/_pagination.html`（新增）：统一分页控件
- `templates/inbox.html`：分类 select 按类型联动脚本
- `templates/transactions.html`：来源状态筛选 + 商品说明展示
- `static/app.css`：页码/跳页/组内明细样式；版本号 `v=20260827b`

### 文档

- `docs/decisions/01_refactor_spec.md`：§3.3/§3.4 写入新交互要求，`last-reviewed=2026-08-27`
- `docs/runbooks/01_用户使用手册.md`：待确认操作、搜索、翻页、星号 FAQ、真实待确认数更新
- `docs/decisions/02_architecture.md`：重写为 v2 当前架构
- `README.md`：移除过期“阶段七等待复审”
- `docs/STATUS.md`：新增本次裁决与真实待确认数

## 4. 验证

- `PYTHONPATH=. pytest`：**280 passed**（原 275 + 新增 5 项回归：搜索回车与商品说明搜索、分组明细与正式分类、页码与跳页、调拨免分类、流水来源状态筛选）。
- 临时库 HTTP 验证：搜索 `q=闲鱼` 返回局部片段且含商品说明；第 2 页页码 current 正确；页码输入框存在。
- 正式库副本 HTTP 冒烟：6 个页面全部 200；`/inbox/category?q=闲鱼` 命中 3 组；流水来源状态筛选正常。
- 正式库未发生写入：所有正式库证据均为只读查询；HTTP 冒烟使用临时副本。

## 5. 结论

**通过。** 6 项用户反馈全部闭环，PRD 复审发现的 5 项偏差已修复；剩余 6 项观察项已在 STATUS 中诚实披露，不阻塞用户继续处理待确认。
