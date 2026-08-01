---
title: 重构阶段 1 修复复审报告
status: approved
category: reports
created: 2026-08-01
last-reviewed: 2026-08-01
supersedes: reports/01_phase1_review_2026-08-01.md
---

# 重构阶段 1 修复复审报告

## 结论

**原 P1、P2 修复通过；阶段 1 整体验收不通过。**

原审查指出的两个迁移缺陷均已正确修复：活动库重置为 v2 模型，备份改为 SQLite backup API。直接运行 `pytest` 的 114 项测试通过。

但正式数据库已完成重置后，现有应用根路径 `/` 返回 HTTP 500。原因是活跃路由继续经 `app/repo.py` 查询已被迁移删除的旧 `transactions` 表。测试套件把路由测试改为调用 `init_legacy_db()`，人为创建旧 schema，未覆盖生产 `init_db()` 迁移后的真实运行状态。因此阶段 1 不能标记为整体通过或进入下一阶段，除非用户明确接受应用在阶段 5 前不可运行的中间态。

审查依据：`docs/decisions/01_refactor_spec.md`（`last-reviewed: 2026-08-01`）第 4、6、7.1 节。

## 原缺陷修复验证

### 已通过：备份后重置为干净 v2 模型

`app/migration_v2.py:258-286` 先检测旧数据、生成备份，然后在 `BEGIN IMMEDIATE` 事务内删除旧业务表并创建/保留 v2 schema。`app/db.py:281-295` 已避免在 v2 库上再次创建旧表。

`tests/test_migration_v2.py:81-109` 验证活动库移除旧表、备份库保留旧 `transactions` 数据；`tests/test_migration_v2.py:124-144` 覆盖了此前新旧 schema 并存的半迁移状态。

### 已通过：SQLite 一致性备份

`app/migration_v2.py:40-54` 使用 SQLite `Connection.backup()` 而非文件复制，并继续执行 `PRAGMA integrity_check`。`tests/test_migration_v2.py:183-209` 在 WAL 模式下写入已提交流水，验证备份包含主库和 WAL 中的全部数据。

## Findings

### P1：真实生产库迁移后，现有应用首页立即 500

**位置：** `app/migration_v2.py:217-229`、`app/repo.py:316-326`、`app/routers/ledger.py:43`。

迁移删除 `transactions` 等旧表，而当前 `/` 请求调用 `list_txns()`，该函数继续执行 `SELECT * FROM transactions`。在正式 `.data/ledger.sqlite` 已迁移为 v2 的状态下，实测 `GET /` 返回 500 和 `Internal Server Error`。

这不是测试环境的偶然性：正式库当前仅含 `classification_rules`、`entry_audit_events`、`import_batches`、`ledger_entries`、`refund_links`、`review_queue`、`schema_meta`、`source_transactions` 和 `sqlite_sequence`。

`tests/test_main_routes.py:62-69` 的 fixture 为旧路由调用 `init_legacy_db()` 而不是生产的 `init_db()`，因此 114 项测试不能发现此故障。这把“历史页面单测通过”与“生产应用可启动”混为一谈。

**建议修复：** 在进入阶段 2 前选择其一并测试：

1. 添加迁移后仍可访问的最小首页/维护页和迁移状态提示，并移除或禁用旧路由，直到阶段 5 重建页面。
2. 直接提前实施新的最小首页和导入入口，使它们只依赖 v2 schema。
3. 用户明确批准应用在阶段 5 前不可用，并将此作为已接受的发布中断写入规格和状态；不建议采用。

无论选择哪一项，都必须新增一个使用生产 `init_db()` 初始化新库的端到端路由测试，至少验证 `/` 不返回 500。

## 验证记录

- `pytest`：114 passed。
- 以正式 `.data/ledger.sqlite` 启动 FastAPI TestClient 后，`GET /`：HTTP 500。

## 阶段 1 重新通过条件

1. 解决 P1，保证生产 `init_db()` 后的应用根路径不返回 500。
2. 添加覆盖生产 v2 schema 的路由测试。
3. 直接执行 `pytest` 并通过。
4. 更新 `docs/STATUS.md`，并以本报告取代阶段 1 已通过的结论。
