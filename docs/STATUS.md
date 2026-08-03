# 项目当前状态（单一真相源 / Single Source of Truth）

> **本文件是项目当前状态的唯一权威来源。** 任何建议、结论、验收判断必须以此为准；
> `reports/`、`archive/` 中的历史报告只作为追溯证据，不构成当前结论。
> 修改任何代码/数据/文档后，如影响状态，必须同步更新本文件。

- **最后更新**：2026-08-03
- **更新人**：阶段七红队审查修复会话

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
| 重构阶段 7 | 🔄 红队审查未通过 → 修复已完成：高风险待办在 /inbox 逐笔处理（退款候选关联、提现逐笔选用途、人际/中性定性）；流水详情页 + 编辑 + manual_edit 审计；撤销阻塞项明细展示；e2e 全部改经 HTTP。待最终复审 | `docs/reports/20_phase7_red_team_review_2026-08-03.md` + 本会话修复 |
| 当前产品面 | ✅ 全新 v2 页面已上线（旧路由已下线）；维护页与 status 路由已移除 | `app/main.py`、`templates/base.html` |
| 测试基线 | ✅ 直接 `pytest` 258 项通过（新增 5 项 HTTP 端到端：退款关联全流程、提现定性/非法用途、人际定性、详情页与编辑审计；验收测试不再直接调用领域服务） | `tests/test_e2e.py` |

（✅=已通过 🔄=进行中 ⏳=待执行）

## 已知剩余缺口（诚实披露）

1. 重构规格中的待定项：in-memory 批量删除令牌是否替换（见 `decisions/03` 跟进候选）。
2. **正式库已完成重置**（2026-08-01 15:00，备份 `ledger.sqlite-20260801-150000.bak`、`ledger.sqlite-20260801-150040.bak`）；schema 已升级至 version 5（阶段 7 审计事件类型扩展）；待用户从新页面重新导入账单。
3. 若未来需要逐条追溯阶段 2 遗留空规则的隔离来源，可记录规则 ID/字段/原因；当前仅持久化隔离数量，不阻塞阶段 3。
4. 若未来需要逐条追溯 v4 迁移清理的多重退款关联，可记录退款来源、保留与隔离关联的 ID；当前仅持久化清理数量，不阻塞阶段 4。
5. 阶段 5 若需在导入历史展示无来源单号异常数，应持久化 `invalid_count`，或明确将其归入 `skipped_count`；当前仅由 `ImportResult` 返回。
6. 阶段 7 修复已完成（P0 高风险逐笔处理、P1 流水详情/编辑审计、P1 撤销阻塞明细、文档同步），待最终红队复审通过后方可真实账单首次导入和发布。

## 进行中的工作

- 账单驱动重构：阶段 1 至阶段 6 最终红队复审均通过；阶段七红队审查未通过，修复已完成（高风险待办闭环、流水追溯、撤销阻塞项展示、e2e 全 HTTP 化），待最终红队复审。

## 当前有效文档（Current Truth）

| 文档 | 用途 | 注意 |
|---|---|---|
| `docs/STATUS.md` | 本文件：当前状态唯一权威 | 每次状态变化必须更新 |
| `docs/decisions/01_refactor_spec.md` | 账单驱动重构规格（验收合同，活文档） | 等待用户确认后实施 |
| `docs/decisions/02_architecture.md` | 当前架构说明（活文档） | 重构实施后需更新 |
| `docs/decisions/03_engineering_history.md` | 工程历史与稳定决策 | historical：仅追溯 |

## 已被取代的结论（Superseded，禁止引用为当前结论）

- `docs/archive/2026-06-07_alipay-import-discussion.md`（2026-06-07）→ 早期"按周合并入账、AI 先分"设想已被 `docs/decisions/01_refactor_spec.md` 取代（后续 10 轮访谈收敛为逐笔落库、第一版不接 AI）

## 维护规则（写文档的人必须遵守）

1. 状态变化时：更新本文件 → 旧报告 front-matter 标 `superseded` + `superseded-by` → 新文档必须带 front-matter 且 `status: approved`。
2. 报告类文档一律放 `docs/reports/`，命名 `NN_主题_YYYY-MM-DD.md`。
3. 新功能/修订：先改 `decisions/01`（PRD）→ 计划放 `.planning/` → 实施 → 验收报告放 `reports/` → 回写本文件。
4. 会话产物（findings/progress/task_plan）只放 `.planning/`，严禁写入 `docs/`。
5. 机器证据（JSON/hash）只放 `docs/evidence/`。
6. 给 AI 的建议/结论必须附带依据文档路径与 `last-reviewed` 日期。
