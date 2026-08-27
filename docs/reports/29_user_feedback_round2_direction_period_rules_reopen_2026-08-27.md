---
title: 用户反馈第二轮修复（方向锁定/筛选 422/周月年概览/规则条件/误操作退回）
status: approved
category: reports
created: 2026-08-27
last-reviewed: 2026-08-27
---

# 29 用户反馈第二轮修复

**日期**：2026-08-27
**触发**：用户第二轮反馈 5 项——待确认方向不应手选、流水空筛选参数报错、概览不应写死本月、规则不能以脱敏商户名当条件、误操作后如何修改与查找流水。
**范围**：待确认交互、流水筛选、概览周期、规则模型（schema v6）、误操作退回闭环。业务统计口径不变。

## 1. 修复闭环

| # | 反馈 | 根因 | 修复 |
|---|---|---|---|
| R1 | 账单方向已知，不应让收入项目选成支出 | 分类区类型下拉未按 `direction` 约束 | UI 按方向锁定类型（收入→收入；支出→消费/调拨；不计收支→调拨），服务端 `confirm_group` 同步拒绝方向不符 |
| R2 | `2025-08-01~今日` 流水筛选报 int/bool 422 | 空 `<option value="">` 提交后 FastAPI 对 `batch_id:int`、`manual_only:bool` 解析失败 | 查询参数改为字符串 + 安全解析；空串视为未筛选；非法 `batch_id` 显示提示而不 500 |
| R3 | 概览写死“本月” | 首页只有 `ym` 月视图 | 支持 `period=week/month/year` + `anchor` 日期；页面提供周/月/年切换、上一/下一周期、日期跳转；保留旧 `?ym=YYYY-MM` |
| R4 | `商户/对方 含「****0」` 不能成为规则 | 批量确认无脑按商户名建规则；规则无平台/方向条件 | schema v6：`classification_rules` 增加 `platform`/`direction`；匹配、创建、规则页均支持；脱敏商户名禁止作为规则条件，自动规则改按组内高频商品说明 |
| R5 | 误操作后如何修改/查找 | 只能逐笔详情编辑，批量误确认没有退回入口 | 流水增加关键词搜索；详情页「退回待确认」；规则页「退回确认流水」按 `ref_rule_id` 批量退回；新增 `bulk_reopen` 审计事件 |

## 2. 规则数据修复（正式库已迁移）

迁移前自动备份：`.data/ledger.sqlite-20260827-170606.bak`。

正式库原有 8 条 `****0`~`****7` 脱敏商户规则全部改写为 `item_desc` 规则，并固化 `platform=alipay`、`direction=income`；迁移记录 `schema_meta.migration_repaired_masked_counterparty_rules=8`。

> 注意：规则 #2 是用户此前把收入组误选为“消费·日常三餐”后生成（已停用）。系统不自动篡改账本数据；请到「规则」页点该规则的「退回确认流水」把 17 笔退回待确认，再按收入重新分类。

## 3. 误操作纠正用法

1. **整组误确认**：`/rules` 找到对应规则 → 「退回确认流水」。系统删除未编辑、未退款关联的账本记录并把原待办恢复为 pending；阻塞项明确展示。
2. **单笔误确认**：`/transactions` 用关键词（商户/商品说明/单号/备注）搜索 → 打开详情 → 「退回待确认」。
3. 已人工编辑或已关联退款的记录不会被静默删除，会作为阻塞项保留并提示。

## 4. 验证

- `PYTHONPATH=. pytest`：**288 passed**（280 基线 + 8 项新增：方向锁定、空筛选/关键词、周月年概览、脱敏规则改写与平台方向匹配、单笔/规则组退回、人工编辑阻塞）。
- 正式库副本迁移验证：schema v5→v6、8 条 masked 规则改写、`entry_audit_events` 增加 `bulk_reopen`、备份生成。
- 正式库 HTTP 冒烟：`/`（周/月/年）、`/transactions`（含空参数与关键词）、`/inbox`、`/rules`、`/imports` 全部 200。
- 正式库迁移前数据快照：`.data/ledger.sqlite-20260827-170606.bak`。

## 5. 变更清单

- `app/decisions/constants.py`：`DIRECTION_ALLOWED_BULK_TYPES`
- `app/decisions/confirm.py`：方向校验、脱敏商户自动规则改用商品说明
- `app/decisions/rules.py`：规则匹配增加 platform/direction
- `app/decisions/engine.py`：匹配调用传 platform/direction
- `app/decisions/reopen.py`（新增）：单笔/规则组退回待确认
- `app/migration_v2.py`：schema v6 迁移、脱敏规则修复、`bulk_reopen` 审计事件、迁移前备份
- `app/ledger_repo.py`：规则创建/存储支持 platform/direction，拒绝脱敏商户规则
- `app/stats.py`：概览周/月/年统计；流水 `source_status`/`q` 筛选
- `app/routers/overview.py`：period/anchor 安全解析
- `app/routers/transactions.py`：空参数安全解析、关键词、单笔退回
- `app/routers/rules.py`：规则表单平台/方向、批量退回
- `templates/`：概览周期控件、待确认方向锁定、规则条件列、退回按钮、流水关键词
- 文档：PRD、架构、手册、README、STATUS 同步
