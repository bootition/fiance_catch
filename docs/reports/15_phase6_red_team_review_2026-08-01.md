---
title: 重构阶段 6 红队审查报告
status: approved
category: reports
created: 2026-08-01
last-reviewed: 2026-08-01
---

# 重构阶段 6 红队审查报告

## 结论

**不通过。**

阶段六的匿名化样本和主业务链路测试有价值，但绝大多数端到端场景直接调用服务函数，而非经页面公开表单入口。红队通过公开 HTTP 请求发现非法手工补账会先写入数据库再返回 HTTP 500；非法平台与规则字段也返回 500。端到端验收不能在用户输入篡改下产生坏数据或服务端错误。

审查依据：`docs/decisions/01_refactor_spec.md`（`last-reviewed: 2026-08-01`）第 7.6、8 节。

## Findings

### P1：非法日期手工补账先持久化坏记录，随后返回 HTTP 500

**位置：** `app/routers/transactions.py:101-147`

`POST /transactions` 将 `txn_date` 直接传给 `create_ledger_entry()`，数据库接受任意 TEXT 日期；写入后调用 `_month_range(txn_date)`，该函数执行 `year, month = txn_date.split('-')[:2]`。例如 `txn_date=garbage` 时，记录已被持久化，随后解包失败，响应 HTTP 500。

红队复现：

```text
POST /transactions
entry_type=consumption&amount=12.34&category=x&txn_date=garbage
```

结果：HTTP 500，且账本中存在 `txn_date='garbage'`、金额 1234 的记录。

这会污染日期筛选、月度统计和后续页面渲染，是财务数据完整性 P1。

**修复要求：** 在任何写入前验证 ISO 日期并返回正常错误页面/422；仓储层同样验证或采用数据库日期 CHECK，防止其他调用方绕过。新增路由测试断言非法日期无新增记录、不返回 500。

### P2：非法平台和规则字段通过页面接口返回 HTTP 500

**位置：** `app/routers/imports.py:36-79`、`app/routers/rules.py:71-91`

- `POST /imports/new` 传 `platform=evil` 时，`import_file()` 抛 `ValueError`，路由未捕获，返回 500。
- `POST /rules` 传 `match_field=evil` 时，SQLite CHECK 约束抛 `IntegrityError`，路由只捕获 `ValueError`，返回 500。

两条路径不应依赖模板 select 选项保证输入合法。用户、脚本或旧表单都可构造请求。应在路由/服务层校验枚举并统一转为用户可处理提示；规则创建还应处理数据库完整性异常。

## 测试覆盖缺口

`tests/test_e2e.py` 的完整流程中，退款关联、规则创建/提升、批量确认、撤销和统计大量直接调用 `link_refund_to_ledger()`、`confirm_group()`、`promote_rule()`、`revoke_batch()`、`import_file()`，未通过对应 HTTP 路由。这不能证明阶段五页面在有效和无效用户输入下安全可用。

阶段六应至少新增页面级端到端测试：上传错误平台/格式、非法补账日期/类型/金额、非法规则字段/状态、已退款记录编辑/删除冲突，并验证失败不写库、响应不为 500。

## 已通过项

- 匿名化固定样本覆盖支付宝 CSV 和微信 XLSX 的真实格式。
- 直接 `pytest`：228 passed。
- 服务层主链路覆盖重复导入、规则观察/激活、高风险待办、跨期退款、批量确认、撤销阻塞和概览口径。

## 阶段 6 重新通过条件

1. 修复 P1，确保非法手工补账无法持久化。
2. 修复 P2，所有已知表单篡改返回可处理错误而非 500。
3. 增加经 HTTP 页面入口的有效与无效端到端回归测试。
4. 直接执行 `pytest` 并通过，再重新红队审查。
