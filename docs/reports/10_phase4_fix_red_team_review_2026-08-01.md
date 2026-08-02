---
title: 重构阶段 4 修复红队复审报告
status: approved
category: reports
created: 2026-08-01
last-reviewed: 2026-08-01
supersedes: reports/09_phase4_red_team_review_2026-08-01.md
---

# 重构阶段 4 修复红队复审报告

## 结论

**不通过。**

新的退款服务入口已正确验证退款状态、`refund_pending` 待办和退款来源一对一约束，候选也会排除已全额退款的消费。但旧公开仓储函数 `app.ledger_repo.link_refund()` 仍直接写入 `refund_links`，可完整绕过这些新约束并虚减消费净额。

审查依据：`docs/decisions/01_refactor_spec.md`（`last-reviewed: 2026-08-01`）第 2.1、2.3、5、7.4 节。

## 已通过的修复验证

- `app/refunds/linking.py:81-158` 拒绝非退款状态来源、缺少 `refund_pending` 待办的来源，以及已经关联的退款来源。
- schema v4 对 `refund_links.refund_source_id` 建唯一约束；候选列表会排除已无可退款余额的消费。
- `pytest`：194 passed。

## Finding

### P1：旧公开仓储写入口绕过全部退款业务不变量

**位置：** `app/ledger_repo.py:480-495`

`link_refund()` 直接执行：

```sql
INSERT INTO refund_links(refund_source_id, original_ledger_id, refund_amount_cents)
```

它没有验证：

- 来源流水是否为退款；
- 来源是否仍有 `refund_pending` 待办；
- 金额是否与来源退款金额一致；
- 原消费累计退款是否超额；
- 关联后待办、批次计数和审计是否同步。

红队复现：创建普通收入来源流水与一笔 ¥10 消费，调用该公开函数并传入 ¥10。插入成功，`list_consumption_with_refunds()` 返回净成本 ¥0，即非退款资金被当作退款冲减消费。

数据库的退款来源唯一约束只能防止同一来源多重关联，不能防止错误来源第一次关联，因此不能替代服务层校验。

**修复要求：**

1. 删除该公开低层写接口，或改为私有连接级 helper，仅供受约束退款服务在事务中使用。
2. 所有公开退款关联必须只经过 `link_refund_to_ledger()`，保持来源检查、超额检查、待办关闭、审计和批次计数同步的原子性。
3. 迁移/更新依赖旧函数的测试；新增回归测试，确保该路径不可导入或不能写入非退款链接。
4. 搜索整个仓库，确认没有第二个直接向 `refund_links` 写入的业务接口。

## 阶段 4 重新通过条件

1. 封闭 P1 绕过入口并补测试。
2. 直接执行 `pytest` 并通过。
3. 再次红队验证普通收入不能创建退款链接或改变消费净额。
