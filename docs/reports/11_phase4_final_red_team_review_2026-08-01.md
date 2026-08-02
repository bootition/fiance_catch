---
title: 重构阶段 4 最终红队复审报告
status: approved
category: reports
created: 2026-08-01
last-reviewed: 2026-08-01
supersedes: reports/10_phase4_fix_red_team_review_2026-08-01.md
---

# 重构阶段 4 最终红队复审报告

## 结论

**通过。**

旧公开退款写入口已移除，退款链接只能由受约束的 `link_refund_to_ledger()` 服务在事务内创建。全仓库检查未发现第二个业务级 `refund_links` 直接写入口；普通收入无法创建退款链接或改变消费净额。

审查依据：`docs/decisions/01_refactor_spec.md`（`last-reviewed: 2026-08-01`）第 2.1、2.3、3.6、5、6、7.4 节。

## 验证结果

- `app.ledger_repo.link_refund` 不存在；低层插入已收敛为连接级私有 `_link_refund()`，仅由退款服务导入并在既有事务中调用。
- 全仓库 `refund_links` 写入搜索仅发现私有 helper 和 schema 迁移；不存在其他业务级绕过入口。
- 红队复现普通收入来源关联消费：退款服务抛出 `ValueError`，`refund_links` 数为 0，消费净额保持原值。
- 退款服务继续强制退款状态、pending 待办、一对一来源、消费类型与原消费额度。
- 候选继续排除已全额退款的消费；schema v4 来源唯一约束和旧多重链接迁移仍保留。
- 直接执行 `pytest`：195 passed。

## 非阻塞建议

v4 多重退款关联迁移目前只记录清理数量；如需逐条解释被清理的历史错误关联，可在后续迁移框架记录退款来源、保留链接和隔离链接的 ID。当前不会产生新的错误链接，不阻塞阶段 4。
