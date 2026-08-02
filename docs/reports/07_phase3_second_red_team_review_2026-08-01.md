---
title: 重构阶段 3 二次红队复审报告
status: approved
category: reports
created: 2026-08-01
last-reviewed: 2026-08-01
supersedes: reports/06_phase3_fix_red_team_review_2026-08-01.md
---

# 重构阶段 3 二次红队复审报告

## 结论

**不通过。**

正式 `.data` 库已成功升级为 schema version 3，`raw_type` 和空规则 CHECK 已存在；跨批次批量确认也会同步 `pending_count`。但版本化升级遗漏了一个此前合法的阶段 2 数据状态：旧 `classification_rules` 可包含空 `match_pattern`。迁移重建规则表时直接复制这些行，新的 CHECK 会使整个应用启动失败。

审查依据：`docs/decisions/01_refactor_spec.md`（`last-reviewed: 2026-08-01`）第 3.5、7.3 节。

## 已通过的修复验证

- 已有 v2 库升级会添加 `source_transactions.raw_type`、添加规则空模式 CHECK，并写入 `schema_version=3`（`app/migration_v2.py:229-324`）。
- 正式 `.data/ledger.sqlite` 实测为 version 3，含 `raw_type`，且 `classification_rules` 含 `CHECK(TRIM(match_pattern) <> '')`。
- `confirm_group()` 在一个事务内按实际 pending 队列同步每个受影响批次的计数，覆盖跨批次同商户分组（`app/decisions/confirm.py:183-202`）。
- 直接执行 `pytest`：171 passed。

## Finding

### P1：升级遇到阶段 2 允许的空规则时，应用无法启动

**位置：** `app/migration_v2.py:254-286`

阶段 2 的 `classification_rules.match_pattern` 只有 `NOT NULL`，没有非空 CHECK；`create_classification_rule(..., match_pattern='')` 在该版本可以成功写入。阶段 3 升级时，`_rebuild_classification_rules()` 创建带 `CHECK(TRIM(match_pattern) <> '')` 的新表，然后无过滤地从旧表复制全部行。

红队复现步骤：

1. 创建完整阶段 2 v2 库（无 `raw_type`、规则表无空模式 CHECK）。
2. 插入一条 `match_pattern=''` 的合法阶段 2 规则，并清除版本号。
3. 调用生产 `init_db()`。

结果：迁移复制该规则时抛出 `sqlite3.IntegrityError: CHECK constraint failed: TRIM(match_pattern) <> ''`；应用不能完成启动。事务会回滚，避免半迁移，但没有可恢复的自动处理或可操作错误信息。

这不是假设的直接数据库篡改：该空规则可以由阶段 2 的公共仓储 API 创建。不能以当前版本的新校验替代对既有数据的迁移策略。

**修复要求：** 明确定义遗留无效规则的迁移策略，并测试。推荐在重建时不复制空白模式规则，同时在同一事务记录其数量/审计事件；由于空规则从不具有安全的业务含义，不能自动转为有效规则。至少应：

1. 过滤 `TRIM(match_pattern) <> ''` 后再复制规则。
2. 保留或记录被隔离规则的可追溯信息，避免静默丢失。
3. 新增“旧库包含空规则”升级测试，验证 `init_db()` 成功、schema 到 v3、有效规则仍保留、无效规则不会匹配交易。
4. 重复执行 `init_db()` 验证幂等。

## 阶段 3 重新通过条件

1. 修复 P1 并补充上述旧数据迁移回归测试。
2. 直接执行 `pytest` 并通过。
3. 更新 `docs/STATUS.md` 并再次红队复审。
