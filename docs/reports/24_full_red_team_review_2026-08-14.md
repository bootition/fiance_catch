---
status: approved
category: reports
last-reviewed: 2026-08-14
---

# 24 全量红队复审（代码层 + 数据层）

**日期**：2026-08-14
**范围**：对当前代码库与正式数据库（`.data/ledger.sqlite`）做一次完整红队复审，逐一核对现有文档（STATUS.md、23 号验收报告、用户使用手册、重构规格、各阶段报告结论）与实际实现/数据是否一致；验证并修复发现的真实缺陷；复跑全部测试。
**方法**：本人逐文件审查全部应用代码与测试，用正式库只读查询验证每个疑点；复核结果直接入库，不依赖任何子代理结论。

## 1. 结论摘要

| 级别 | 数量 | 状态 |
|---|---|---|
| P0（数据损坏/安全问题） | 0 | — |
| P1（真实缺陷，影响正确使用） | 1 | ✅ 已修复并回迁数据 |
| P2（明显缺陷/审计可追溯性） | 3 | ✅ 已修复 |
| 文档与实现不符（P1-P3） | 6 | ✅ 已修正文档或实现 |
| 新增回归测试 | 6 | ✅ 265 项全部通过 |

**最终判定：通过。** 23 号验收报告的结论全部属实；发现并闭环了 1 个数据层误分类缺陷（28 条真实流水）与 3 个 P2 缺陷；文档与实现已重新对齐。

## 2. 文档一致性核对结果

### 2.1 核对为"属实"的文档结论（抽查验证，未发现失实）

- **23 号验收报告**（`docs/reports/23_real_bills_controlled_acceptance_2026-08-14.md`）8 项结论逐项验证属实：259 项测试基线、schema v5、导入指纹与计数、重复上传零新增、撤销演练零残留、1756 条待确认等。
- **STATUS.md 各阶段裁决**（阶段 1-7 复审通过）抽查复验属实；"维护页与 status 路由已移除"属实（`app/main.py`）。
- **规格 01 主体**：判定顺序（退款→提现→人际→不计收支→消费/收入）、去重、批次报告、审计事件、退款候选、统计口径等与实现一致。
- **使用手册主体**：启动、导入、规则、批次撤销、备份、处理顺序（先分类后退款）等与实现一致。

### 2.2 发现并修正的文档失实项

| # | 位置 | 原描述 | 实际情况 | 处理 |
|---|---|---|---|---|
| D1 | 使用手册 §4 删除 | "已关联的退款会被清掉关联" | 已关联退款的记录会被**拒绝删除**（`ledger_repo.delete_ledger_entry` 抛 ValueError），退款关联无法经删除解绑 | 修正手册 |
| D2 | 使用手册 FAQ 退款候选 | "跨平台不互配" | `matching.py` 候选查询**不区分平台**，跨平台同商户会出现在候选中 | 修正手册 |
| D3 | 使用手册 §3 分组 | "同一商户若同时有收/支两种方向，分开看每组的方向再确认" | 旧实现不区分方向，同一组可混合收支；本次已按方向拆分分组并在表格显示方向列 | 修正手册 + 实现 |
| D4 | 规格 §3.2 | "支持一次上传支付宝 CSV 和微信 XLSX" | 实际为单文件上传，两平台分别上传 | 修正规格 |
| D5 | 规格 §3.3 | "按商户/商品相似项分组，支持多选并批量指定分类" | 实际为逐组（单选商户）批量确认，非多选 | 修正规格 |
| D6 | 规格 §2.1 | "人际转账、红包、收款一律停在待确认" | 支出方向"收钱码/扫码收款"是商家收款码支付，按该字样一律判人际会造成误分类（见 P1） | 补充规格说明 |

## 3. 新发现缺陷与修复

### P1：人际转账关键词"收款"方向性误判（数据层缺陷，已回迁）

**发现**：`engine._is_person_transfer` 对 `item_desc+counterparty` 做无方向关键词匹配，任何方向含"收款"即判为人际转账停入高风险区。正式库实测 **28 条**商家消费被误分（收钱码收款 22 条 + 收款方备注:二维码收款 6 条），用户将被迫逐笔定性这些普通消费。

**修复**：
1. 关键词方向化：`收款` 仅收入方向命中；支出方向匹配 `(收钱码|二维码|扫码)收款` 明确排除；`红包` 仅非支出方向命中（支出方向红包多为促销抵扣描述）；`转账` 保留全方向。规格 §2.1 同步补充说明。
2. 数据回迁：28 条 pending 项 `person_transfer → unmatched`（suggested_type=consumption），修复前先备份 `ledger.sqlite-20260814-redteam.bak`。校验：残留 0 条；收入方向"收款"1 条保持人际不变。
3. 队列构成变化：待确认 1756 不变；unmatched 1484→1512，person_transfer 137→109（其余 66 中性/64 退款/5 提现不变）。

**回归测试**：`test_engine_merchant_collect_expense_is_not_person_transfer`、`test_engine_income_collect_is_person_transfer`、`test_engine_expense_transfer_still_person_transfer`。

### P2-1：分类区分组不区分收支方向

同商户收入与支出混在一组（正式库实测 1 组 5 条混向），批量确认会迫使两类交易打成同一类型。

**修复**：分组键改为（商户 × 平台 × 收支方向）；`Group`/`group_review_items` 增加 direction；`confirm_group` 增加 `direction` 参数（默认 "expense"，向后兼容既有调用）；待确认页表格新增"方向"列，确认表单携带 direction 隐藏字段。规格 §3.3、手册 §3 同步更新。回归测试：`test_group_review_items_splits_by_direction`，并适配 e2e 测试的方向感知。

### P2-2：批量确认审计事件关联错误、不可追溯

`confirm_group` 的 `bulk_confirm` 审计事件把 `ref_batch_id` 错写成首个待办项的 `source_id`，且无 `ref_ledger_id`/`ref_rule_id`——该事件在流水详情（按 ref_ledger_id 查）与规则命中历史（按 ref_rule_id 查）中**均不可见**，违反规格 §4"批量确认最小审计记录"的可追溯意图。

**修复**：每条入账记录写独立 `bulk_confirm` 事件，正确携带 `ref_ledger_id`、`ref_batch_id`（真实批次）、`ref_rule_id`（本次建议的观察规则）。回归测试：`test_confirm_group_audit_events_have_correct_refs`。正式库尚无历史批量确认事件，无需回迁审计数据。

### P2-3：概览页非法月份参数 500

`GET /?ym=abc` 实测抛 `ValueError: Invalid month` 导致 500。**修复**：`_safe_ym` 校验 `YYYY-MM` 格式与月份范围，非法值回退默认月。回归测试：`test_index_ignores_invalid_ym`。

## 4. 安全与稳健性抽查

- **模板自动转义**：Starlette `Jinja2Templates` 源码确认 `autoescape=True` 默认开启，账单原文（商户名/商品说明/备注）注入 XSS 风险低。
- **表单无效输入**：既有 e2e 覆盖（非法日期补账不落库不 500 等）复跑通过。
- **金额解析**：`parse_amount_to_cents`（手工补账）拒绝超 2 位小数与负数；`amount_to_cents`（导入）对超 2 位小数静默量化四舍五入——记录为 P3 一致性建议，未改（真实账单均为 2 位小数）。
- **日期解析**：`normalize_occurred_at` 对非法/不存在的日历日期整批拒绝，坏日期永不落库（阶段 6 结论复验属实）。

## 5. 测试与冒烟结果

- `PYTHONPATH=. pytest`：**265 passed**（基线 259 + 新增回归 6）。
- 正式库只读冒烟：`/`、`/inbox`、`/transactions`、`/rules`、`/imports` 均 200；`/?ym=abc` 200（修复生效）；待确认页含"方向"列与 direction 隐藏字段。

## 6. 遗留观察项（P3，非阻塞，不构成发布风险）

1. `pending_count` 在概览/导入历史为全库口径，与"某月待办"语义可能混淆。
2. `REASON_TRAVEL` 常量未使用；旅游规则命中不计数、不提示（设计使然，但规则页命中证据为零）。
3. `amount_to_cents`（导入）与 `parse_amount_to_cents`（手工）小数位策略不一致。
4. 导入批次处理中途失败（非解析阶段）无恢复入口，需重新上传（去重兜底）。
5. 规则 `confirm_count` 统计字段未在批量确认时累计。

## 7. 依据与追溯

- 修复代码：`app/decisions/engine.py`、`app/decisions/confirm.py`、`app/routers/inbox.py`、`app/routers/overview.py`、`templates/inbox.html`
- 回归测试：`tests/test_decisions.py`、`tests/test_v2_routes.py`、`tests/test_e2e.py`（适配方向分组）
- 数据回迁脚本（会话产物，不入库）：`.planning/2026-08-14-redteam/repair_person_misroute.py`
- 生产数据备份：`.data/ledger.sqlite-20260814-redteam.bak`（修复前）
- 规格变更：`docs/decisions/01_refactor_spec.md`（§2.1/§3.2/§3.3，last-reviewed 2026-08-14）
- 手册修正：`docs/runbooks/01_用户使用手册.md`（§2.3/§3/§4/FAQ）
