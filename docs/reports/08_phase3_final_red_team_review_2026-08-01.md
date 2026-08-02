---
title: 重构阶段 3 最终红队复审报告
status: approved
category: reports
created: 2026-08-01
last-reviewed: 2026-08-01
supersedes: reports/07_phase3_second_red_team_review_2026-08-01.md
---

# 重构阶段 3 最终红队复审报告

## 结论

**通过。**

旧 v2 库包含阶段 2 合法的空规则时，当前迁移会隔离空规则、保留有效规则、写入隔离计数并成功升级到 schema version 3；重复启动保持幂等。此前发现的高风险批量确认绕过、空规则全量匹配、待确认计数不同步和已有 v2 库无法升级的问题均未复现。

审查依据：`docs/decisions/01_refactor_spec.md`（`last-reviewed: 2026-08-01`）第 2.1、3.3、3.5、5、7.3 节。

## 验证结果

- 构造阶段 2 v2 库：无 `raw_type`、无规则非空 CHECK，包含一条空模式 active 规则和一条有效观察期规则。
- 运行生产 `init_db()`：成功启动并升级至 schema version 3。
- 升级后仅保留有效规则；空规则未被复制；`schema_meta.migration_dropped_blank_rules=1`。
- 正式 `.data/ledger.sqlite` 检查：`schema_version=3`，`source_transactions.raw_type` 存在。
- 高风险原因继续不参与批量确认；空规则继续无法创建、提升或匹配；跨批次确认继续同步各批次 `pending_count`。
- 直接执行 `pytest`：172 passed。

## 非阻塞建议

迁移目前仅记录 `migration_dropped_blank_rules` 数量。若未来需要逐条追溯隔离过哪些遗留规则，可在后续迁移框架中记录规则 ID、字段和隔离原因；空模式没有安全业务语义，因此不阻塞当前阶段。
