---
title: 账单驱动个人财务系统架构说明（v2 当前产品面）
status: approved
category: decisions
last-reviewed: 2026-08-27
---

# 账单驱动个人财务系统架构说明

> 本文件描述当前已上线产品面，与 `docs/decisions/01_refactor_spec.md`（规格）保持一致。
> 旧版 review/cleanup 页面与旧导入工作流已经下线；旧代码仅作为历史迁移保留，不参与产品面。

## Product Surface

| 页面 | 路由 | 说明 |
|---|---|---|
| 概览 | `/` | 周/月/年切换、自由选期、关键指标、日常消费环比、消费分类排行 |
| 账单导入 | `/imports/new` | 支付宝 CSV / 微信 XLSX 单文件上传与处理结果 |
| 待确认 | `/inbox` | 高风险区逐笔处理 + 分类区按组批量确认；分页、搜索、页码跳页 |
| 流水 | `/transactions` | 日期/类型/分类/平台/批次/来源状态/关键词筛选、简易补账、退回待确认；详情 `/transactions/{id}` |
| 规则 | `/rules` | 匹配字段 + 平台 + 方向条件，观察/自动/停用规则、命中历史、批量退回确认流水 |
| 批次 | `/imports` | 导入历史、撤销与阻塞项展示 |

## Runtime Model

- Backend: FastAPI
- Templates: Jinja2（`app/templates_core.py` 注册模板环境与分页窗口 helper）
- Partial interactivity: HTMX 1.9.12（CDN，本地化是后续优化项）
- Storage: SQLite at project-root `.data/ledger.sqlite`（schema v6）

## Domain Shape

### Import pipeline

1. `app/importing/alipay.py` / `app/importing/wechat.py`：原始账单解析与标准化；不保存原始文件。
2. `app/importing/service.py`：单事务写入 `import_batches` 与 `source_transactions`，按 `(platform, source_txn_id)` 去重。
3. `app/decisions/engine.py`：逐条决策——退款/提现/人际/中性进入 `review_queue`，可信调拨与 active 规则自动入账，未命中进入分类区。

### Review queue

- `app/decisions/confirm.py`：分类区按 `商户 × 平台 × 收支方向` 分组、分页搜索、批量确认并建议观察期规则；方向锁定类型，脱敏商户自动改按商品说明建规则。
- `app/decisions/high_risk.py`：提现用途与人际/中性资金流逐笔定性。
- `app/refunds/matching.py` + `app/refunds/linking.py`：退款候选匹配与受约束人工关联；跨期退款通过原消费 `txn_date` 回写统计。
- `app/routers/inbox.py`：待确认页面与局部刷新路由（分区翻页/搜索、退款候选刷新、处理提交）。

### Ledger and reporting

- `app/ledger_repo.py`：账本/来源流水/待办/规则/审计的仓储层；退款不变量在写路径强制。
- `app/stats.py`：概览净额统计（退款后口径）、流水筛选、正式分类候选。
- `app/routers/transactions.py`：流水筛选（日期/类型/分类/平台/批次/来源状态/关键词/人工改动）、简易补账、详情、编辑、退回待确认与删除。
- `app/revoke.py` + `app/routers/imports.py`：安全批次撤销，阻塞项（已编辑/已退款关联）明确列出。

### Rules

- `app/decisions/rules.py`：按匹配字段 + 平台 + 方向匹配；active 优先于 observing。
- `app/decisions/builtin_rules.py`：内置高置信交通识别规则（地铁_/单车/骑行/公交 → 出行交通），新导入自动入账，可对存量 unmatched 一次性应用。
- 旅游类规则命中只预填且禁止提升为自动入账（规格 §2.2/§3.5）。

## Key Modules

- `app/main.py`: app assembly and router mounting
- `app/db.py`: legacy schema repair entry（仅当未初始化 v2 时）
- `app/migration_v2.py`: v2 schema、备份/重置迁移（schema v5）
- `app/decisions/`: 入账决策、分组确认、高风险处理、规则匹配
- `app/importing/`: 支付宝/微信解析与导入服务
- `app/refunds/`: 退款状态、候选匹配、人工关联
- `app/router_support/`: settings 访问、请求解析、分页窗口 helper
- `app/routers/`: 产品面路由（overview/imports/inbox/transactions/rules）

## Persistence And Compatibility

- 新模型表：`import_batches`、`source_transactions`、`ledger_entries`、`review_queue`、`refund_links`、`classification_rules`、`entry_audit_events`、`schema_meta`。
- 旧 `transactions` / `import_sessions` / `import_rows` / `category_rules` 表与旧路由不参与当前产品面。
- `pytest` 直接运行；生产入口在应用 lifespan 调用 `init_db()` 建库/升级。

## Operational Notes

- Static assets and the default data directory are anchored to project paths, not the current working directory.
- Bulk-delete preview tokens from the legacy cleanup workflow are no longer part of the product surface.
- 当前测试基线以 `docs/STATUS.md` 为准。
