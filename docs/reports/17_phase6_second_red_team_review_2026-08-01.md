---
title: 重构阶段 6 二次红队复审报告
status: approved
category: reports
created: 2026-08-01
last-reviewed: 2026-08-01
supersedes: reports/16_phase6_fix_red_team_review_2026-08-01.md
---

# 重构阶段 6 二次红队复审报告

## 结论

**不通过，暂不建议进入阶段七。**

损坏微信 XLSX 现在安全处理：解析异常转换为错误页、无批次残留、临时文件会清理。但红队发现结构合法、字段非法的支付宝账单仍会写入坏时间戳。随后用户确认该流水会把坏日期带入 `ledger_entries`，重新污染筛选、统计与页面。

审查依据：`docs/decisions/01_refactor_spec.md`（`last-reviewed: 2026-08-01`）第 5、7.2、7.6、8 节。

## 已通过的修复验证

- 非 ZIP 微信 XLSX、ZIP 内 XML 损坏的 XLSX 均不返回 500，无批次残留，上传临时文件清理。
- 非法平台、坏支付宝内容、伪装文件、非法页面日期/金额/类型/规则字段均不写坏数据。
- `pytest`：238 passed。

## Finding

### P1：账单行交易时间未校验，合法 CSV 可写入坏来源流水

**位置：** `app/importing/alipay.py:121-154`、`app/importing/wechat.py:134-169`、`app/importing/service.py:76-120`

标准化器把交易时间原样存为字符串，不验证 ISO 日期或时间格式。导入服务随后将其写入 `source_transactions.occurred_at`。决策引擎和批量确认会使用 `occurred_at[:10]` 作为账本日期，因此坏来源时间会进一步写入 `ledger_entries.txn_date`。

红队复现：上传表头与字段数均正确、但交易时间为 `not-a-date` 的支付宝 CSV：

```text
not-a-date,...,支出,10.00,...,交易成功,BAD-DATE-1
```

结果：上传返回 HTTP 200，但数据库已存在 1 个批次和 1 条 `occurred_at='not-a-date'` 的来源流水。后续待确认操作可产生坏账本日期。

这违反“失败不落库”和日期完整性要求；之前修复的手工补账日期校验无法保护账单导入路径。

**修复要求：**

1. 在支付宝和微信标准化时解析并规范化交易时间为 `YYYY-MM-DD HH:MM:SS`；对空、非法或不存在的日历日期抛 `ValueError`。
2. 因导入是原子事务，任一有效/退款行日期无效时整批不能创建批次或来源流水。
3. 对来源库/账本库增加适当的日期格式约束，或在全部公开写入口统一验证。
4. 增加支付宝与微信的无效日期、无效时间、合法闰日测试，以及 HTTP 上传后无批次/无来源残留的端到端测试。

## 是否可进入阶段七

**不可以。** 当前仍能通过账单导入入口写入会破坏后续统计的日期数据；应先修复并复审通过。
