---
title: 重构阶段 2 修复复审报告
status: approved
category: reports
created: 2026-08-01
last-reviewed: 2026-08-01
supersedes: reports/03_phase2_review_2026-08-01.md
---

# 重构阶段 2 修复复审报告

## 结论

**通过。**

上一轮审查的 P1 已修复：文件导入现在将批次创建、全部来源流水写入和批次计数更新放在同一 `BEGIN IMMEDIATE` 事务中。中途写入失败或计数更新失败会由连接上下文回滚，重试可以重新完整导入。

审查依据：`docs/decisions/01_refactor_spec.md`（`last-reviewed: 2026-08-01`）第 5、7.2 节。

## 修复验证

- `app/importing/service.py:77-120` 在单一数据库连接内启动 `BEGIN IMMEDIATE`，并调用连接级仓储函数完成批次、流水和计数写入，正常路径才 `commit()`。
- `tests/test_import_service.py:122-141` 模拟第二条来源流水失败，断言批次和来源流水均零残留。
- `tests/test_import_service.py:143-156` 模拟批次计数更新失败，断言完整回滚。
- `tests/test_import_service.py:159-182` 验证失败后重传同一文件，全部有效流水新增且没有错误重复跳过。
- `tests/test_import_service.py:185-197` 验证成功/退款行缺少来源交易号时计为 `invalid`、不写库，避免空交易号相互去重。
- `tests/test_import_service.py:200-211` 验证零金额成功流水作为来源平台事实保留。
- 直接执行 `pytest`：140 passed。

## 真实账单冒烟验证

在临时 v2 数据库中导入项目外本地账单，未保存原文件：

| 平台 | 账单行数 | 新增来源流水 | 跳过 | invalid | 退款 | 批次 row_count |
|---|---:|---:|---:|---:|---:|---:|
| 支付宝 | 2,017 | 1,973 | 44 | 0 | 34 | 2,017 |
| 微信 | 349 | 349 | 0 | 0 | 30 | 349 |

行数和批次计数与账单头部及上一轮成功解析结果一致。

## 非阻塞建议

`ImportResult` 返回 `invalid` 数，但当前 `import_batches` 表只有 `row_count`、`accepted_count`、`skipped_count` 和 `pending_count`，未持久化该数值。阶段 5 导入结果页面如需显示“无来源单号异常数”，应新增 `invalid_count`，或明确把它纳入 `skipped_count`。这不影响阶段 2 的原子导入、去重或数据安全。
