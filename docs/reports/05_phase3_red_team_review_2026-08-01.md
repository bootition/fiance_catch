---
title: 重构阶段 3 红队审查报告
status: approved
category: reports
created: 2026-08-01
last-reviewed: 2026-08-01
---

# 重构阶段 3 红队审查报告

## 结论

**不通过。**

规则、观察期、待确认分组和批量确认的基本路径均已实现，但红队验证发现三条可直接污染财务事实或误导待办的攻击路径。全部 155 项测试通过并不代表阶段 3 达到规格要求，原因是测试仅覆盖普通未分类交易的批量确认，未覆盖高风险待办、重跑批次计数和无效规则模式。

审查依据：`docs/decisions/01_refactor_spec.md`（`last-reviewed: 2026-08-01`）第 2.1、3.3、3.5、5、7.3 节。

## Findings

### P1：批量确认可绕过提现、退款和人际资金的人工处理边界

**位置：** `app/decisions/confirm.py:46-86`、`app/decisions/confirm.py:95-163`

`group_review_items()` 查询所有 `review_queue.status = 'pending'` 的项目；`confirm_group()` 随后对整个商户组无条件创建任意 `entry_type` 和 `category` 的账本记录。它没有限制 `reason`，没有要求逐笔提现用途，也没有阻止 `refund_pending` 和 `person_transfer` 进入普通分类批量确认。

红队复现：创建同一商户的两笔 `withdrawal` 待确认项，调用 `confirm_group(..., entry_type='consumption', category='x')` 后，两笔提现都被直接写为消费，待确认项被关闭。这违反“提现到银行卡必须逐笔选择用途”和“退款必须关联原消费后才影响统计”的硬约束。

**修复要求：**

1. 将风险区和普通分类区的数据访问隔离。
2. `confirm_group()` 仅接受允许批量分类的原因，例如 `unmatched`、`observing_rule`；拒绝退款、提现、人际转账和其他中性资金流。
3. 为提现、人际资金和退款提供分别受约束的确认命令，或保留到阶段 4；不得让通用 `entry_type/category` 入口绕过它们。
4. 增加每一种高风险 `reason` 不能调用批量确认的回归测试。

### P1：空规则模式可匹配所有交易并批量自动入账

**位置：** `app/ledger_repo.py:538-558`、`app/decisions/rules.py:9-16`、`app/decisions/confirm.py:149-161`

`classification_rules.match_pattern` 没有非空约束，`create_classification_rule()` 不校验空白字符串，而 `_text_contains(value, pattern)` 直接执行 `pattern in value`。Python 中 `'' in value` 永远为真。将此规则提升为 `active` 后，任意普通来源流水都会命中，并被自动写入该规则指定的类型和分类。

红队复现：创建 `counterparty=''`、`target_type='consumption'`、`target_category='bad'` 的规则并提升；一笔无关商户的消费被自动写为 `consumption/bad`。

这会造成全量自动错分，且 active 规则会跳过人工确认，属于账本完整性风险。

**修复要求：** 在 schema 与仓储/API 两层拒绝 `TRIM(match_pattern) = ''`，并在匹配函数中防御性跳过空模式；为观察期创建、规则提升和 active 匹配分别补测试。

### P2：幂等重跑将批次待确认数清零，维护页和后续导入结果会误报

**位置：** `app/decisions/engine.py:219-260`

`process_batch()` 的幂等判断会跳过已经创建待确认项的来源流水，但最后把 `import_batches.pending_count` 设置为本次新增 `queued` 数，而不是该批次当前所有 `pending` 队列项的数量。

红队复现：首次处理两笔提现后批次 `pending_count=2`；第二次处理同一批次得到 `skipped_existing=2`，并将 `pending_count` 写为 `0`，但两条待确认记录仍在队列中。

这违反批次结果计数应反映当前状态的预期，并会使用户误以为无需处理。

**修复要求：** 在完成处理后，以数据库查询计算该批次所有 `review_queue.status='pending'` 的实际数量再更新计数；增加重跑、部分确认后重跑和多种待确认原因混合的测试。

## 已通过项

- `process_batch()` 使用单事务，来源决策路径可完整回滚。
- 退款、提现、人际转账在决策引擎的初始分类阶段正确进入高风险待确认。
- 明确的中性余额宝/零钱通等调拨可自动记为 `transfer`。
- 观察期规则仅预填建议，不自动入账；active 规则可自动入账。
- 正常未分类/观察期分类项可按同平台同商户分组，批量确认会创建观察期规则。
- 真实账单冒烟计数与阶段状态记录一致。
- `pytest`：155 passed。

## 阶段 3 重新通过条件

1. 修复全部 P1/P2 问题并新增相应红队回归测试。
2. 在临时库中验证：退款、提现、人际转账和其他资金流均不能经批量确认进入消费/收入；空/空白规则不可创建或提升；重跑后 `pending_count` 等于真实队列数。
3. 直接执行 `pytest` 并通过。
4. 更新 `docs/STATUS.md` 并重新审查。
