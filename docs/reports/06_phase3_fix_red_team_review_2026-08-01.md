---
title: 重构阶段 3 修复红队复审报告
status: approved
category: reports
created: 2026-08-01
last-reviewed: 2026-08-01
supersedes: reports/05_phase3_red_team_review_2026-08-01.md
---

# 重构阶段 3 修复红队复审报告

## 结论

**不通过。**

上一轮发现的三条攻击路径已修复并有回归测试：高风险项不再进入批量确认分组，空规则不能创建或匹配，`process_batch()` 重跑会以真实队列数回写 `pending_count`。但红队复审发现两个新的生产一致性缺陷，其中一个会使已存在的正式 v2 数据库无法使用阶段 3 导入/决策路径。

审查依据：`docs/decisions/01_refactor_spec.md`（`last-reviewed: 2026-08-01`）第 2.1、3.3、3.5、5、7.3 节。

## 已通过的原攻击修复

- `app/decisions/confirm.py:65-111` 仅查询 `unmatched`、`observing_rule` 分类区项目；退款、提现、人际转账和其他中性资金流不会出现在批量确认分组。
- `app/decisions/rules.py:9-13`、`app/ledger_repo.py:546-560`、`app/migration_v2.py:156` 分别在匹配、仓储和新 schema 层拒绝空白规则模式。
- `app/decisions/engine.py:247-266` 使用批次当前 pending 队列的真实计数，修复了 `process_batch()` 重跑清零问题。
- `tests/test_red_team_phase3.py` 覆盖高风险隔离、混合组隔离、空规则三层防御及重跑/部分处理的计数回写。
- `pytest`：166 passed。

## Findings

### P1：已有正式 v2 库未升级 `raw_type` 和规则约束，阶段 3 在生产库不可用

**位置：** `app/migration_v2.py:28-37`、`app/migration_v2.py:258-286`、`app/decisions/engine.py:69-85`、`app/importing/service.py:92-105`

阶段 3 为 `source_transactions` 新增 `raw_type` 字段，并给 `classification_rules.match_pattern` 新增非空 CHECK。可是 `new_schema_initialized()` 只检查八张表是否存在；已在阶段 1/2 初始化的 v2 库会直接跳过 `init_new_schema()`，没有 ALTER TABLE 或 schema version 迁移。

对正式 `.data/ledger.sqlite` 的实际检查结果：

- `source_transactions` 不含 `raw_type`；
- `classification_rules` 定义中不含 `CHECK(TRIM(match_pattern) <> '')`。

新来源流水写入和决策引擎都依赖 `raw_type`。因此正式库与临时测试库的 schema 不同，阶段 3 不能安全部署到当前正式库。测试全部以新建临时库运行，未覆盖“已有 v2 库升级”路径。

**修复要求：** 为 v2 schema 建立明确版本号/迁移序列。对已初始化的库：安全添加 `raw_type TEXT NOT NULL DEFAULT ''`；通过重建 `classification_rules` 或等效迁移添加空模式 CHECK；在事务中更新 schema version。新增测试应从阶段 2 旧 v2 schema 创建数据库，运行当前 `init_db()` 后验证字段/约束存在并能导入、决策。

### P2：批量确认后没有同步所属批次 `pending_count`

**位置：** `app/decisions/confirm.py:120-194`

`confirm_group()` 创建账本记录并将待确认项标为 `resolved`，但未更新每条来源流水所属 `import_batches.pending_count`。该计数仅在之后手动重新执行 `process_batch()` 时才纠正。

红队复现：一个批次有两条普通未分类项；处理后 `pending_count=2`。调用 `confirm_group()` 成功后，实际 pending 队列为 0，但批次字段仍为 2。

这会使导入批次、维护页或阶段 5 页面持续报告不存在的待办。应在同一个确认事务中按受影响批次查询真实 pending 数并更新计数；若一个分组跨批次，必须更新全部涉及批次。补充跨批次同商户分组确认的测试。

## 阶段 3 重新通过条件

1. 修复 P1：实现并测试从已有阶段 2 v2 schema 升级至阶段 3 schema 的迁移。
2. 修复 P2：批量确认事务内同步每个受影响批次的真实 `pending_count`，含跨批次分组。
3. 用正式 `.data` 库或其无敏感 schema 副本验证 `init_db()` 后 `raw_type` 存在，且规则约束生效。
4. 直接执行 `pytest` 并通过，再重新红队复审。
