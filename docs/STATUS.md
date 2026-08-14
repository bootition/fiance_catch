# 项目当前状态（单一真相源 / Single Source of Truth）

> **本文件是项目当前状态的唯一权威来源。** 任何建议、结论、验收判断必须以此为准；
> `reports/`、`archive/` 中的历史报告只作为追溯证据，不构成当前结论。
> 修改任何代码/数据/文档后，如影响状态，必须同步更新本文件。

- **最后更新**：2026-08-14
- **更新人**：全量红队复审

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
| 测试基线 | ✅ 直接 `pytest` 265 项通过（含红队复审新增 6 项回归）；高风险处理、编辑/撤销、自动规则入账至流水详情的 HTTP 闭环已验证 | `docs/reports/24_full_red_team_review_2026-08-14.md` |

（✅=已通过 🔄=进行中 ⏳=待执行）

## 已知剩余缺口（诚实披露）

1. 重构规格中的待定项：in-memory 批量删除令牌是否替换（见 `decisions/03` 跟进候选）。
2. **正式库已完成重置**（2026-08-01 15:00，备份 `ledger.sqlite-20260801-150000.bak`、`ledger.sqlite-20260801-150040.bak`）；schema 已升级至 version 5（阶段 7 审计事件类型扩展）；待用户从新页面重新导入账单。
3. 若未来需要逐条追溯阶段 2 遗留空规则的隔离来源，可记录规则 ID/字段/原因；当前仅持久化隔离数量，不阻塞阶段 3。
4. 若未来需要逐条追溯 v4 迁移清理的多重退款关联，可记录退款来源、保留与隔离关联的 ID；当前仅持久化清理数量，不阻塞阶段 4。
5. 阶段 5 若需在导入历史展示无来源单号异常数，应持久化 `invalid_count`，或明确将其归入 `skipped_count`；当前仅由 `ImportResult` 返回。
6. 真实账单导入已完成并验收（2026-08-14 复核通过，见 `reports/23_*`）；剩余 1756 条待确认（未匹配 1512、人际转账 109、中性资金流 66、退款 64、提现 5；红队复审回迁 28 条商家收款码误判，见 `reports/24_*`）需用户在 `/inbox` 逐批人工处理。处理完成前，正式统计为空是预期状态，不宣称"账已对平/已正式发布"。

## 进行中的工作

- 用户验收：真实账单已导入并通过验收复核；当前等待用户在 `/inbox` 处理 1756 条待确认（分类确认、退款关联、提现用途、人际/中性资金流定性），随后核对月度统计并生成用户验收报告。

## 当前有效文档（Current Truth）

| 文档 | 用途 | 注意 |
|---|---|---|
| `docs/STATUS.md` | 本文件：当前状态唯一权威 | 每次状态变化必须更新 |
| `docs/decisions/01_refactor_spec.md` | 账单驱动重构规格（验收合同，活文档） | 等待用户确认后实施 |
| `docs/decisions/02_architecture.md` | 当前架构说明（活文档） | 重构实施后需更新 |
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
