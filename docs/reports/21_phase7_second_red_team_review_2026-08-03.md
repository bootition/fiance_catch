---
title: 重构阶段七二次红队复审报告
status: approved
category: reports
created: 2026-08-03
last-reviewed: 2026-08-03
supersedes: reports/20_phase7_red_team_review_2026-08-03.md
---

# 重构阶段七二次红队复审报告

## 结论

**不通过，尚不能进入下一阶段、导入真实账单或发布。**

上一轮 P0/P1 交付缺口已经实质修复：高风险待办具备浏览器操作入口，流水详情与编辑审计已交付，批次撤销会显示阻塞项，阶段七验收测试也已改为经 HTTP 完成。但是，自动规则入账的审计事件没有关联其生成的账本记录，导致用户在流水详情中看不到“规则命中证据”。

这不是仅影响日志显示的低优先级问题。规格要求流水详情必须展示规则命中证据；当前系统已经把详情页作为该追溯信息的产品入口，却因关联键缺失而对自动入账记录显示“暂无审计事件”。因此阶段七重新验收条件尚未全部满足。

审查依据：

- `docs/decisions/01_refactor_spec.md`（`status: approved`，`last-reviewed: 2026-08-01`）第 3.4、7.6、8 节。
- `docs/reports/20_phase7_red_team_review_2026-08-03.md`（已被本报告取代）第“重新验收条件”。

## 已验证修复

以下项目通过代码审查和 HTTP 验收测试，原报告中的对应阻塞项可关闭：

- `GET /inbox` 为退款显示候选原消费、金额、剩余可退金额和匹配依据；`POST /inbox/refund/link` 调用受约束退款关联服务。见 `app/routers/inbox.py:44-91, 159-179` 与 `templates/inbox.html:24-48`。
- 提现有逐笔用途选择；人际转账和其他中性资金流有消费/收入/调拨定性表单。受约束服务在一个事务中写账本、关闭待办、写审计事件并同步批次待确认数。见 `templates/inbox.html:49-79` 与 `app/decisions/high_risk.py:87-167`。
- 流水列表有详情入口，详情页展示来源、批次、退款关联、编辑表单和审计事件；人工编辑在同一数据库连接中写入 `manual_edit` 事件。见 `templates/transactions.html:114-119`、`app/routers/transactions.py:155-240`、`templates/entry_detail.html`、`app/ledger_repo.py:429-457`。
- 批次撤销结果将每个保留对象、ID、原始原因、人类可读说明和跳转链接传递到页面。见 `app/routers/imports.py:122-152` 与 `templates/imports.html:7-37`。
- 阶段七测试从上传开始，通过公开 HTTP 路由完成退款关联、提现定性、人际定性、编辑和撤销。`tests/test_e2e.py:1-6, 696-837`。
- 本次执行 `pytest`：**258 passed**。

## 发现

### P1：自动规则入账在流水详情中不可追溯

**规格要求：**

`/transactions` 必须展示“规则命中证据”（规格 3.4）。阶段七上一轮报告也将“流水页可编辑并查看来源、批次、规则、退款和人工改动证据”列为重新验收条件。

**复现路径：**

1. 在 `/rules` 创建一条规则并提升为自动入账。
2. 在 `/imports/new` 导入一条匹配该规则的成功交易。
3. 进入 `/transactions`，打开该自动入账记录的详情。
4. 详情页“审计事件”区域显示“暂无审计事件”，无法显示命中规则、匹配条件或规则 ID。

**代码证据：**

- `app/decisions/engine.py:145-154` 调用 `_create_ledger_entry()` 创建自动入账记录，但忽略其返回的 `entry_id`。
- 同文件 `156-162` 写入 `rule_applied` 审计事件时只传入 `ref_rule_id` 与 `ref_batch_id`，没有传入 `ref_ledger_id`。
- `app/routers/transactions.py:194-203` 的详情查询严格使用 `WHERE ref_ledger_id = ?` 获取审计事件。

因此自动入账记录存在规则事件，但事件没有指向账本记录，详情页无法查询到它。规则页面的“命中历史”也不能替代此缺口：用户需要从具体流水追溯其入账依据，且规则历史无法为每条流水提供详情入口。

**影响：**

- 用户无法判断某笔自动入账是由哪条规则、以何种证据分类。
- 规则误分类后，用户无法从流水详情定位需要停用或修正的规则。
- 规格 3.4 和阶段七追溯性验收条件未满足。

**解决方式：**

1. 在 `process_source()` 的 active 规则分支保存 `_create_ledger_entry()` 返回的 `entry_id`。
2. 调用 `_add_audit_event()` 时传入 `ref_ledger_id=entry_id`，保留现有 `ref_rule_id` 与 `ref_batch_id`。`detail` 应至少保留来源流水 ID；若可行，补充匹配字段和模式以提高独立可读性。
3. 新增回归测试：经 HTTP 在规则页创建并提升自动规则、上传命中交易、访问 `/transactions/{entry_id}`，断言页面显示“规则自动入账”以及规则 ID 或匹配依据；同时断言 `entry_audit_events.ref_ledger_id` 等于该账本 ID。
4. 保留现有规则页命中历史测试，确保一条事件可同时按规则和账本记录追溯。

## 测试评价

`pytest` 的 258 项测试全部通过，但未覆盖此问题，原因是现有测试仅验证：

- 自动规则确实创建了账本记录（`tests/test_e2e.py:186-223`）。
- 详情页具有审计事件区域，并在人工编辑后显示 `manual_edit`（`tests/test_e2e.py:795-837`）。

测试没有把“自动规则入账”与“打开该笔流水详情并验证规则审计证据”串联。因此测试覆盖已从上一轮的服务层绕过问题改善为 HTTP 闭环，但仍遗漏了跨页面追溯链路。

重新验收时，应新增上述回归测试后再执行全量测试；不得只用规则表 `hit_count` 或规则页历史替代账本详情证据。

## 重新验收条件

阶段七可通过并进入下一阶段的最低条件：

- 修复 `rule_applied` 审计事件与自动创建账本记录的关联。
- 新增仅经 HTTP 的回归测试，证明自动规则命中后，具体流水详情能展示规则命中证据。
- 执行 `pytest` 并全量通过。
- 生成下一份阶段七最终复审报告，将本报告标记为 `superseded`，并同步更新 `docs/STATUS.md`。

真实支付宝 CSV 与微信 XLSX 的受控用户验收仍应在代码复审通过后执行；在此之前不建议向正式 `.data/ledger.sqlite` 导入真实财务账单。
