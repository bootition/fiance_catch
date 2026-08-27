# 项目当前状态（单一真相源 / Single Source of Truth）

> **本文件是项目当前状态的唯一权威来源。** 任何建议、结论、验收判断必须以此为准；
> `reports/`、`archive/` 中的历史报告只作为追溯证据，不构成当前结论。
> 修改任何代码/数据/文档后，如影响状态，必须同步更新本文件。

- **最后更新**：2026-08-27
- **更新人**：用户反馈第九轮修复（2026-08-27）

## 当前裁决（Verdict）

| 层面 | 状态 | 依据 |
|---|---|---|
| 产品方向 | ✅ 已获用户确认，正在实施：本地单用户账单驱动个人财务系统（逐笔落库、规则优先人工确认、第一版不接 AI） | `docs/decisions/01_refactor_spec.md` |
| 重构阶段 1 | ✅ 复审通过：P1/P2 修复已验证；生产 `init_db()` 后 `/` 返回维护页（200），旧路由已禁用（404） | `docs/reports/02_phase1_fix_review_2026-08-01.md`（approved） |
| 重构阶段 2 | ✅ 修复复审通过：单事务原子导入、失败回滚/重传、空单号拒绝和零金额保留均已验证 | `docs/reports/04_phase2_fix_review_2026-08-01.md` |
| 重构阶段 3 | ✅ 最终红队复审通过：旧 v2 空规则可安全隔离，保留有效规则并升级至 v3；高风险待办隔离、空规则防御、批次计数同步和已有库升级均已验证 | `docs/reports/08_phase3_final_red_team_review_2026-08-01.md` |
| 重构阶段 4 | ✅ 最终红队复审通过：退款写入已收敛至受约束服务；普通来源无法伪造退款，来源一对一、候选余额与跨期净额规则均已验证 | `docs/reports/11_phase4_final_red_team_review_2026-08-01.md` |
| 重构阶段 5 | ✅ 最终红队复审通过：页面和仓储层均安全处理已退款记录删除；规则观察期、退款统计聚合和编辑约束均已验证 | `docs/reports/14_phase5_final_red_team_review_2026-08-01.md` |
| 重构阶段 6 | ✅ 最终红队复审通过：空时间、非法时间/日期和损坏文件均安全拒绝；完全空白行不误伤有效交易；可进入阶段七交付验收 | `docs/reports/19_phase6_final_red_team_review_2026-08-01.md` |
| 重构阶段 7 | ✅ 最终红队复审通过：高风险逐笔处理、流水详情/编辑审计、撤销阻塞明细和自动规则命中证据均可经 HTTP 产品闭环验证；可进入真实账单受控验收 | `docs/reports/22_phase7_final_red_team_review_2026-08-03.md` |
| 真实账单受控导入 | ✅ 验收通过：真实支付宝/微信账单已导入（2026-08-03），指纹与计数逐项一致；重复上传零新增、撤销演练零残留；1756 条待确认等待用户人工处理 | `docs/reports/23_real_bills_controlled_acceptance_2026-08-14.md` |
| 全量红队复审 | ✅ 通过：文档与实现逐项核对（23 号报告结论全部属实）；修复 P1 人际转账"收款"关键词误判（28 条真实流水回迁 unmatched，备份 `ledger.sqlite-20260814-redteam.bak`）、P2 分组不区分收支方向、批量确认审计事件关联错误、概览非法 ym 500；规格与手册 6 处失实表述已对齐 | `docs/reports/24_full_red_team_review_2026-08-14.md` |
| 当前产品面 | ✅ 全新 v2 页面已上线（旧路由已下线）；维护页与 status 路由已移除 | `app/main.py`、`templates/base.html` |
| Inbox UX 优化 | ✅ 通过：HTMX 局部刷新（消除整页重载闪烁与跳顶）、退款无候选指引与候选刷新路由、界面紧凑化；业务规则零变更 | `docs/reports/25_inbox_ux_optimization_2026-08-27.md` |
| Inbox 滚动性能 | ✅ 通过：高风险区分页（243→20/页）+ 分类区分页（198→30/页）与商户搜索，消除快速下滑卡顿；select 573→60、HTML 576KB→70KB；另移除 content-visibility 闪烁元凶、body 渐变改纯色、CSS 加版本号防缓存 | `docs/reports/26_inbox_scroll_performance_2026-08-27.md`、`docs/reports/27_inbox_scroll_performance_phase2_2026-08-27.md` |
| 用户反馈修复 + PRD 复审 | ✅ 通过：搜索回车/商品说明搜索、分组明细与交易时间、正式分类内置、页码与跳页、星号来源说明、流水来源状态筛选全部闭环；PRD 偏差 G1-G5 已修复；业务规则零变更 | `docs/reports/28_user_feedback_and_prd_audit_2026-08-27.md` |
| 测试基线 | ✅ 直接 `pytest` **280 项通过**（275 基线 + 本次新增 5 项回归：搜索/明细/分类选项/页码跳页/流水状态筛选） | `docs/reports/28_user_feedback_and_prd_audit_2026-08-27.md` |
| 用户反馈第二轮修复 | ✅ 通过：方向锁定、流水空参数 422、概览周/月/年、规则平台/方向条件与脱敏规则改写、误操作单笔/规则组退回全部闭环；schema v5→v6，正式库已迁移并备份 | `docs/reports/29_user_feedback_round2_direction_period_rules_reopen_2026-08-27.md` |
| 测试基线（第二轮） | ✅ 直接 `pytest` **288 项通过**（280 基线 + 8 项第二轮回归） | `docs/reports/29_*` |
| 用户反馈第三轮修复 | ✅ 通过：分组合并规则页面明示；组内每笔可单独确认；处理后视口位置保持，不再跳底 | `docs/reports/30_user_feedback_round3_single_item_and_scroll_2026-08-27.md` |
| 测试基线（第三轮） | ✅ 直接 `pytest` **290 项通过**（288 基线 + 2 项第三轮回归） | `docs/reports/30_*` |
| 用户反馈第四轮修复 | ✅ 通过：明细上移；内置交通规则自动区分地铁/单车/骑行/公交；存量 115 笔自动入账出行交通（已备份） | `docs/reports/31_user_feedback_round4_builtin_transport_detail_order_2026-08-27.md` |
| 测试基线（第四轮） | ✅ 直接 `pytest` **293 项通过**（290 基线 + 3 项内置交通规则回归） | `docs/reports/31_*` |
| 用户反馈第五轮修复 | ✅ 通过：交通规则扩展机票/火车票/XX出行；新增医疗健康分类；清空 43 条非 AI 规则，批量确认不再自动生成规则 | `docs/reports/32_user_feedback_round5_medical_traffic_rules_governance_2026-08-27.md` |
| 用户反馈第六轮修复 | ✅ 通过：查出“鸿/跑鞋/完美校园/阿里云”等疑似误归类；历史 bulk_confirm 重过规则，命中 14 条保留、未命中 683 条退回待确认；后续规则自动筛选工具就绪 | `docs/reports/33_user_feedback_round6_suspicious_reopen_rule_sweep_2026-08-27.md` |
| 测试基线（第六轮） | ✅ 直接 `pytest` **296 项通过**（293 基线 + 规则清扫/保留/筛选 3 项） | `docs/reports/33_*` |
| 用户反馈第七轮修复 | ✅ 通过：写入用户已给特征规则 17 条并提升 active；对全部 unmatched 自动筛选 228 笔；剩余 1143 笔规则外订单留待继续总结 | `docs/reports/34_user_feedback_round7_rule_apply_leave_unclassified_2026-08-27.md` |
| 用户反馈第八轮修复 | ✅ 通过：schema v7 支持 raw_type 规则；支付宝收入（闲鱼收款）155 笔全部归入副业收入；收入方向未匹配清零 | `docs/reports/35_user_feedback_round8_alipay_income_rule_2026-08-27.md` |
| 测试基线（第八轮） | ✅ 直接 `pytest` **297 项通过**（296 基线 + raw_type 规则 1 项） | `docs/reports/35_*` |
| 用户反馈第九轮修复 | ✅ 通过：删除“支付宝收入=副业收入”宽规则；改为脱敏买家+商品标题形态识别；工资/报销不会误判 | `docs/reports/36_user_feedback_round9_income_rule_rigor_2026-08-27.md` |

（✅=已通过 🔄=进行中 ⏳=待执行）

## 已知剩余缺口（诚实披露）

1. 重构规格中的待定项：in-memory 批量删除令牌是否替换（见 `decisions/03` 跟进候选）。
2. **正式库已完成重置并重新导入**（2026-08-01 15:00 重置，2026-08-03 完成真实账单导入）；schema 已升级至 **version 6**（规则平台/方向条件 + `bulk_reopen` 审计）；迁移前备份 `.data/ledger.sqlite-20260827-170606.bak`。
3. 若未来需要逐条追溯阶段 2 遗留空规则的隔离来源，可记录规则 ID/字段/原因；当前仅持久化隔离数量，不阻塞阶段 3。
4. 若未来需要逐条追溯 v4 迁移清理的多重退款关联，可记录退款来源、保留与隔离关联的 ID；当前仅持久化清理数量，不阻塞阶段 4。
5. 阶段 5 若需在导入历史展示无来源单号异常数，应持久化 `invalid_count`，或明确将其归入 `skipped_count`；当前仅由 `ImportResult` 返回。
6. 真实账单导入已完成并验收（2026-08-14 复核通过，见 `reports/23_*`）；当前剩余 **1231 条**待确认（未匹配 988、人际转账 108、中性资金流 66、退款 64、提现 5）。支付宝收入（闲鱼收款）155 笔已归入副业收入（备份 `ledger.sqlite-20260827-230030.bak`）。处理完成前，正式统计以 `/inbox` 处理进度为准，不宣称"账已对平/已正式发布"。
7. **待用户确认的数据纠正**：规则 #2 对应的 17 笔收入曾被误确认为“消费·日常三餐”（规则已停用）。系统提供「规则页 → 退回确认流水」纠正入口，未自动改动账本数据；用户处理后核对统计。
9. 流水页 `list_entries_filtered(limit=500)` 会静默截断且无分页提示；待用户处理完 1664 条后可能不够，建议下轮做流水页分页。
10. PRD §3.4“退款调整”：简易补账可选 `refund` 类型，但该记录不参与统计、不关联原消费；需要用户确认口径后再改。
11. htmx 走 CDN，离线不可用（报告 25 已记录）。
12. `pending_count` 为全库口径，与“某月待确认”语义可能混淆（报告 24 已记录）。

## 进行中的工作

- 用户验收：真实账单已导入并通过验收复核；当前等待用户在 `/inbox` 处理 **1231 条**待确认（分类确认、退款关联、提现用途、人际/中性资金流定性），并处理规则 #2 的 17 笔退回重分类，随后核对月度统计并生成用户验收报告。
- 八轮用户反馈修复已完成并写入 `docs/reports/28_*`~`35_*`；会话计划与发现记录在 `.planning/2026-08-27-user-feedback*/`（不入 git）。

## 当前有效文档（Current Truth）

| 文档 | 用途 | 注意 |
|---|---|---|
| `docs/STATUS.md` | 本文件：当前状态唯一权威 | 每次状态变化必须更新 |
| `docs/decisions/01_refactor_spec.md` | 账单驱动重构规格（验收合同，活文档） | 已确认并实施，2026-08-27 增补 Inbox/流水 UX 条款 |
| `docs/decisions/02_architecture.md` | 当前架构说明（活文档） | 2026-08-27 已重写为 v2 当前产品面 |
| `docs/decisions/03_engineering_history.md` | 工程历史与稳定决策 | historical：仅追溯 |
| `docs/runbooks/01_用户使用手册.md` | 用户使用手册：启动、处理待确认、规则、流水、批次、备份 | 按实际页面核对编写 |

## 已被取代的结论（Superseded，禁止引用为当前结论）

- `docs/archive/2026-06-07_alipay-import-discussion.md`（2026-06-07）→ 早期"按周合并入账、AI 先分"设想已被 `docs/decisions/01_refactor_spec.md` 取代（后续 10 轮访谈收敛为逐笔落库、第一版不接 AI）

## 维护规则（写文档的人必须遵守）

1. 状态变化时：更新本文件 → 旧报告 front-matter 标 `superseded` + `superseded-by` → 新文档必须带 front-matter 且 `status: approved`。
2. 报告类文档一律放 `docs/reports/`，命名 `NN_主题_YYYY-MM-DD.md`。
3. 新功能/修订：先改 `decisions/01`（PRD）→ 计划放 `.planning/` → 实施 → 验收报告放 `reports/` → 回写本文件。
4. 会话产物（findings/progress/task_plan）只放 `.planning/`，严禁写入 `docs/`。
5. 机器证据（JSON/hash）只放 `docs/evidence/`。
6. 给 AI 的建议/结论必须附带依据文档路径与 `last-reviewed` 日期。
