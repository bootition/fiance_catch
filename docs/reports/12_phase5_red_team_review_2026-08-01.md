---
title: 重构阶段 5 红队审查报告
status: approved
category: reports
created: 2026-08-01
last-reviewed: 2026-08-01
---

# 重构阶段 5 红队审查报告

## 结论

**不通过。**

新页面覆盖了导入、待确认、规则、流水、概览和撤销，但红队通过公开页面路由复现了三条 P1 路径：规则观察期可被直接绕过，首页对部分退款统计错误，已退款消费可被编辑为负净额。208 项测试通过，但未覆盖这些跨层业务不变量。

审查依据：`docs/decisions/01_refactor_spec.md`（`last-reviewed: 2026-08-01`）第 2.2、2.3、3.1、3.5、6、7.5 节。

## Findings

### P1：规则管理页面可绕过观察期，直接启用新规则自动入账

**位置：** `app/routers/rules.py:102-109`

`POST /rules/{rule_id}/status/active` 直接调用 `update_rule_status(..., 'active')`，绕过 `promote_rule()` 的观察期提升语义。新创建规则可由该路由立即变为 active，后续交易将绕过人工确认自动入账。

红队复现：创建观察期规则后请求 `/rules/{id}/status/active`，响应 200，规则状态立即为 `active`。

**修复要求：** 页面“启用”不得把 `observing` 直接变为 `active`；观察期规则只能调用 `promote_rule()`。状态路由仅允许 active/disabled 之间切换，或统一由服务层实施明确状态机和审计。增加从 observing 直跳 active 被拒绝的路由测试。

### P1：首页统计对多笔部分退款重复累计原消费金额

**位置：** `app/stats.py:51-64`

`_category_net()` 对 `ledger_entries` 左联 `refund_links` 后直接计算 `SUM(le.amount_cents) - SUM(rl.refund_amount_cents)`。一笔消费关联两笔部分退款时，联接产生两行，原消费金额会被求和两次。

红队复现：¥50 消费关联 ¥20 和 ¥30 退款，正确净消费应为 ¥0；`overview_stats(...)["total_consumption_cents"]` 返回 ¥50。

**修复要求：** 先在退款链接子查询按 `original_ledger_id` 聚合，再与账本记录连接；或复用 `list_consumption_with_refunds()` 的逐条净额语义。补单笔全额、多笔部分、跨期、多笔消费混合的概览测试。

### P1：流水编辑允许将已退款消费改小，产生负净额

**位置：** `app/routers/transactions.py:150-179`、`app/ledger_repo.py:360-386`

页面编辑未检查该消费已关联退款金额。用户可把原 ¥50（已关联 ¥20+¥30 退款）改为 ¥10，统计净额成为 -¥30。编辑也没有限制来源交易的金额/类型/日期，允许通过 UI 破坏退款关联的财务不变量。

红队复现：上述已全额退款消费经 `POST /transactions/{id}/edit` 改为 ¥10 后，首页返回 `total_consumption_cents=-3000`。

**修复要求：** 对已有退款链接的账本记录，拒绝会使 `amount_cents < 已关联退款总额` 的编辑；更稳妥的第一版是限制编辑来源交易的金额、类型和日期，仅允许分类/备注等非金额字段修改。数据库/仓储层也必须执行该约束，不能只依赖页面。

## 已通过项

- 导入页面临时保存上传文件并在 finally 删除，不持久化原文件。
- 导入后调用原子导入与批次处理；重复来源交易仍由下层去重。
- 待确认页面展示高风险区与分类区，批量确认仍由阶段 3 受约束服务执行。
- 批次撤销页面含显式浏览器确认，并调用阶段 4 安全撤销服务。
- 规则创建仍受空模式校验保护。
- `pytest`：208 passed。

## 阶段 5 重新通过条件

1. 修复三项 P1，并在服务/仓储层而非仅页面层强制业务不变量。
2. 添加相应路由和统计回归测试。
3. 直接执行 `pytest` 并通过，再进行红队复审。
