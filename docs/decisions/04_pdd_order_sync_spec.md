---
title: 拼多多订单同步与回填规格（长期功能草案）
status: draft
category: decisions
created: 2026-08-28
last-reviewed: 2026-08-28
---

# 拼多多订单同步与回填规格（长期功能草案）

> 本草案由 2026-08-28 一次性抓取/回填实践提炼，供用户确认后转为 `approved` 并实施。
> 未获用户确认前，本文件不构成当前结论；当前结论仍以 `docs/STATUS.md` 与 `docs/decisions/01_refactor_spec.md` 为准。

## 1. 背景与目标

账本已导入支付宝/微信真实账单。其中拼多多流水只有 `商户单号XP...`，无商品名称，导致待确认支出无法有效分类；拼多多买家侧没有官方订单导出接口，只能使用可审计的网页端抓取工具。

长期目标：把“拼多多订单抓取 → 商品信息匹配 → 安全回填 → 退款/取消订单利用”做成可重复、可审计、可回滚的本地功能。

成功标准：

1. 用户登录拼多多网页版后，系统能用固定版本、已审计的工具抓取订单，并生成安全审计报告。
2. 订单商品信息以独立富化表关联到账本流水，不修改原始账单字段；Inbox/流水/搜索/规则均可使用富化文本。
3. 匹配过程可解释、可人工复核；高置信自动回填，其余保留候选。
4. 退款成功/交易已取消订单被持久化，并用于增强既有退款匹配，最终仍遵守“退款金额严格等于原消费金额、同平台”的既有口径。
5. 每次写库前自动备份；所有写操作事务化、留审计、可回滚。

## 2. 边界

- 仅限用户本人拼多多账号、本人本地账本；不提供云同步、不多账号。
- 不绕过风控/验证码；不自动下单、不触碰支付密码。
- 不保存 cookie 明文、不保存上传的支付账单原文件（沿用现有口径）。
- 第一版不做 App 端自动抓取；只支持网页版订单列表 + 用户主动登录。
- 第一版不自动确认消费分类；只提供商品信息与候选，分类仍由用户在 Inbox 按现有流程确认。
- 第一版不新增 AI 分类。

## 3. 数据获取

### 3.1 工具与版本锁定

- 基于开源 MIT 脚本 `asd13006/pinduoduo-order-export`，固定在 commit `94a7842`。
- 本地安全补丁必须保持：`unsafeWindow→window`、禁用 jsDelivr CDN、放慢滚动节奏。
- 每次升级脚本前重新静态审查，并记录：仓库、commit、blob sha、文件 sha256、补丁 sha256、审查结论。

### 3.2 采集方式

- CLI 采集器（Python + Playwright）打开 `https://mobile.pinduoduo.com/orders.html`，用户在隔离浏览器 profile 中扫码/短信登录。
- 拦截订单 API 响应并保存 JSONL 原始快照到 `.data/pdd/raw/`（`.data/` 已整体 gitignore）。
- 同一运行保存：网络请求审计日志、运行元数据、脚本哈希、异常记录。
- 分页节奏默认：2.5s/步、连续 20 步无新订单或到达目标日期才停、单轮硬上限 180s。
- 支持增量：默认全量刷新；通过 `order_sn` 去重，记录每次运行覆盖的最早/最晚订单时间。

### 3.3 安全红线

- cookie 只从用户浏览器 profile 只读载入内存，不打印、不写入数据文件。
- 网络审计中只允许拼多多系域名（`*.pinduoduo.com`、`*.yangkeduo.com`、`*.pddpic.com`、`*.pddugc.com` 等白名单）；出现其他外发域名时运行判定失败并阻止导入。
- 不用 CDN；不执行远程脚本。
- 采集后提示用户登出并可选清理隔离 profile。

## 4. 数据设计（schema v8 草案）

在现有 v7 基础上新增以下表，**不修改** `source_transactions` 的原始字段。

| 表 | 用途 | 关键字段 |
|---|---|---|
| `pdd_sync_runs` | 每次抓取/导入运行 | run_id, source, script_sha256, started_at, finished_at, raw_count, order_count, security_ok, report_path |
| `pdd_orders` | 标准化订单 | order_sn 唯一, parent_order_sn, order_type, order_time, pay_time, display_amount_cents, order_amount_cents, discount_amount_cents, status_text, mall_name, goods_json, raw_path, fetched_run_id |
| `pdd_order_enrichments` | 支出流水与订单的匹配 | source_transaction_id 唯一, product_desc, method, confidence, status(active/manual_review), created_at, updated_at |
| `pdd_order_enrichment_items` | 合并支付明细 | enrichment_id, order_sn, amount_cents |
| `pdd_refund_order_links` | 退款流水 ↔ 退款订单 | refund_source_transaction_id 唯一, order_sn, match_method, confidence, created_at |

富化展示与规则匹配时，按 `source_transaction_id` 优先取 `pdd_order_enrichments.product_desc`，原始 `source_transactions.item_desc` 只在无富化时展示。

## 5. 支出匹配规则

对 `review_queue.reason='unmatched'`、方向为支出的拼多多流水：

1. **exact_unique**：订单实付金额与账单金额相等，且时间差 ≤2h、候选唯一 → 高置信自动回填。
2. **exact_best**：同金额多候选，时间最近者与次近者差距 >1h → 高置信；否则人工。
3. **subset_unique / subset_best**：合并支付，若干订单金额之和等于账单金额，且订单下单时间聚类接近账单时间；组合唯一或明显最优 → 高置信；否则人工。
4. **external_discount**：账单备注含 `已优惠¥X`，订单金额可能大于账单金额；用“订单金额-优惠金额=账单金额”扩展匹配，高置信需唯一。
5. 未匹配/低置信进入人工复核页，不得自动写库。
6. `交易已取消` 订单不参与支出匹配；`退款成功` 订单仍可作为“已付款后退款”的证据参与。

## 6. 退款与取消订单

- 采集时持久化订单状态：`交易成功`、`退款成功`、`交易已取消`、`已提货` 等。
- 退款流水匹配：对 `refund_pending` 流水，按“金额相等 + 订单状态为退款成功 + 时间窗（默认 60 天，按历史命中分布调整）”匹配唯一订单；存在多候选时保留人工。
- 微信账单中常见成对记录（原支出状态变“已全额退款” + 平台退款收入）应链接到同一个订单，防止双重处理。
- **不直接生成账本退款冲销**：沿用既有约束——原消费必须先确认入账为 `ledger_entries`，退款金额必须严格等于原消费金额且同平台；订单号链接作为 95/100 分的高强度证据，增强现有 `find_refund_candidates`，唯一候选才自动冲销，其余人工。
- `交易已取消`：默认不冲销任何账单；如存在对应已付款流水，列入异常清单人工处理。

## 7. 页面与交互

- 新增 `/pdd` 页面：显示最近同步运行、订单数、覆盖时间、匹配率、安全审计状态；提供“导入抓取结果”与“生成匹配草稿”。
- Inbox 分类区与高风险区：在商品说明处显示富化商品名（不改变原字段），搜索同时命中富化文本。
- 流水详情：显示订单号、商品、规格、店铺、状态、匹配置信度。
- 匹配复核页：按 high/medium/low/none 过滤，展示候选订单，允许“确认采用 / 换候选 / 忽略”。
- 退款候选页：显示关联订单证据与既有候选，沿用现有退款操作闭环。

## 8. 迁移与安全

- 现有 211 笔一次性回填（已写入 `item_desc`，原始值存于 `note` 与 `pdd_enrich_audit`）：实施 v8 时先回灌 `pdd_order_enrichments`，再决定是否恢复原始 `item_desc`。恢复前必须确认富化展示、搜索、规则、退款匹配均已切换到富化表。
- 每次写库前自动备份 `.data/ledger.sqlite`，写操作单事务，失败回滚。
- 富化/退款链接的建立与撤销均写 `entry_audit_events`（或等价审计表）。
- 回滚脚本/审计行保留，禁止直接删除历史订单快照；清理只允许删临时网络日志且需确认。

## 9. 实施阶段（待用户确认后执行）

1. schema v8 迁移 + 仓储层 + 单元测试。
2. PDD CLI 采集器模块化（从一次性脚本提取） + 安全审计输出。
3. 订单导入/标准化/去重 + 支出匹配引擎 + 样本测试。
4. `/pdd` 页面 + Inbox/详情/搜索富化展示 + 匹配复核页。
5. 退款订单链接 + 既有退款匹配增强 + 回归测试。
6. 一次性回填数据迁移（211 笔）与对账。
7. 全量红队复审 + 验收报告 + `docs/STATUS.md` 回写。

## 10. 验收条件

- 用固定样本与固定脚本版本可重复得到：350 笔订单、211 笔高置信支出匹配、25/26 退款流水唯一候选。
- 重复导入不产生重复 `pdd_orders`/`pdd_order_enrichments`。
- 原始 `source_transactions` 字段保持不变（迁移完成后）。
- 网络审计无第三方域名；cookie 不落盘；脚本哈希与白名单校验生效。
- 低置信/未匹配记录必须人工确认后才生效；可回滚。
- `pytest` 全部通过，且新增 PDD 同步/匹配/退款测试。
