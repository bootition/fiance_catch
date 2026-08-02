---
title: 重构阶段 5 最终红队复审报告
status: approved
category: reports
created: 2026-08-01
last-reviewed: 2026-08-01
supersedes: reports/13_phase5_fix_red_team_review_2026-08-01.md
---

# 重构阶段 5 最终红队复审报告

## 结论

**通过。**

已关联退款的账本记录删除已在仓储层提前拒绝，页面将冲突转为 URL 编码的 303 提示。红队无法再通过正常删除入口触发 HTTP 500、删除关联或改变退款后净额；普通未关联记录仍可删除。

审查依据：`docs/decisions/01_refactor_spec.md`（`last-reviewed: 2026-08-01`）第 2.1、2.3、3.1、3.4、3.5、6、7.5 节。

## 验证结果

- `delete_ledger_entry()` 发现 `refund_links.original_ledger_id` 引用后抛出业务 `ValueError`，而非裸 SQLite 外键异常。
- `POST /transactions/{entry_id}/delete` 对已退款消费返回 303，Location 含编码后的失败提示；不返回 500。
- 拒绝后退款链接和账本记录仍在，退款后净额保持不变。
- 普通未关联记录删除成功。
- 阶段五此前修复的观察期状态机、部分退款预聚合和退款关联记录编辑约束均未回归。
- 直接执行 `pytest`：219 passed。
