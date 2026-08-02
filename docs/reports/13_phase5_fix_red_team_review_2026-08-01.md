---
title: 重构阶段 5 修复红队复审报告
status: approved
category: reports
created: 2026-08-01
last-reviewed: 2026-08-01
supersedes: reports/12_phase5_red_team_review_2026-08-01.md
---

# 重构阶段 5 修复红队复审报告

## 结论

**不通过。**

上一轮三项 P1 均修复：规则观察期状态机在仓储层生效，概览预聚合退款，退款关联记录的金额和类型编辑受仓储层限制。但红队补充验证发现用户通过正常删除按钮删除已关联退款的消费时，SQLite 外键异常未被处理，路由返回 HTTP 500。

审查依据：`docs/decisions/01_refactor_spec.md`（`last-reviewed: 2026-08-01`）第 2.1、2.3、3.1、3.4、3.5、6、7.5 节。

## 已通过的原攻击修复

- `app/ledger_repo.py:659-689` 将规则状态机放入仓储层：`observing` 不能通过 `update_rule_status(..., 'active')` 直跳，必须经 `promote_rule()`。
- 页面直接请求 `/rules/{id}/status/active` 后规则状态保持 `observing`。
- `app/stats.py:51-73` 先按原账本预聚合退款；¥50 消费关联 ¥20+¥30 退款后，概览消费总额为 ¥0。
- `app/ledger_repo.py:360-413` 拒绝退款关联消费改为小于已退款金额，且拒绝改变其交易类型。
- `pytest`：217 passed。

## Finding

### P1：页面删除已关联退款的消费时触发 HTTP 500

**位置：** `app/routers/transactions.py:182-187`、`app/ledger_repo.py:416-421`

`delete_ledger_entry()` 直接执行 `DELETE FROM ledger_entries`。已关联退款的消费被 `refund_links.original_ledger_id` 外键 `RESTRICT` 保护，SQLite 正确抛出 `sqlite3.IntegrityError`；但仓储函数和页面路由均未捕获这个预期业务冲突。

红队复现：创建已关联退款的消费，通过 `/transactions/{entry_id}/delete` 的正常表单入口删除。请求返回 HTTP 500，而不是说明“该记录已有关联退款，不能删除”的可恢复响应。

数据不会被删除，但这是用户可触发的服务端错误，并破坏阶段 5 承诺的流水删除体验。

**修复要求：** 在仓储层把退款关联删除冲突转换为明确的业务结果/`ValueError`，并在路由层捕获后返回正常页面提示。增加路由测试断言 HTTP 200/303、记录仍存在、退款链接仍存在、页面显示说明而非 500。也应评估手工编辑/批次撤销等其他 FK 冲突的统一异常处理。

## 阶段 5 重新通过条件

1. 修复 P1 并添加仓储和页面回归测试。
2. 直接执行 `pytest` 并通过。
3. 复测删除已关联退款的消费不会返回 500，也不会破坏关联或统计。
