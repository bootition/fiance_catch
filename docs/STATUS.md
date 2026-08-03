# 项目当前状态（单一真相源 / Single Source of Truth）

> **本文件是项目当前状态的唯一权威来源。** 任何建议、结论、验收判断必须以此为准；
> `reports/`、`archive/` 中的历史报告只作为追溯证据，不构成当前结论。
> 修改任何代码/数据/文档后，如影响状态，必须同步更新本文件。

- **最后更新**：2026-08-03
- **更新人**：AI 治理会话（docs-git-governance）

## 当前裁决（Verdict）

| 层面 | 状态 | 依据 |
|---|---|---|
| 产品方向 | ✅ 已获用户确认，正在实施：本地单用户账单驱动个人财务系统（逐笔落库、规则优先人工确认、第一版不接 AI） | `docs/decisions/01_refactor_spec.md` |
| 重构阶段 1 | ✅ 复审通过：P1/P2 修复已验证；生产 `init_db()` 后 `/` 返回维护页（200），旧路由已禁用（404） | `docs/reports/02_phase1_fix_review_2026-08-01.md`（approved） |
| 重构阶段 2 | ✅ 修复复审通过：单事务原子导入、失败回滚/重传、空单号拒绝和零金额保留均已验证 | `docs/reports/04_phase2_fix_review_2026-08-01.md` |
| 重构阶段 3 | ✅ 最终红队复审通过：旧 v2 空规则可安全隔离，保留有效规则并升级至 v3；高风险待办隔离、空规则防御、批次计数同步和已有库升级均已验证 | `docs/reports/08_phase3_final_red_team_review_2026-08-01.md` |
| 重构阶段 4 | ✅ 最终红队复审通过：退款写入已收敛至受约束服务；普通来源无法伪造退款，来源一对一、候选余额与跨期净额规则均已验证 | `docs/reports/11_phase4_final_red_team_review_2026-08-01.md` |
| 重构阶段 5 | ✅ 最终红队复审通过：页面和仓储层均安全处理已退款记录删除；规则观察期、退款统计聚合和编辑约束均已验证 | `docs/reports/14_phase5_final_red_team_review_2026-08-01.md` |
| 重构阶段 6 | 🔄 二次红队复审未通过：损坏微信文件已安全处理，但结构合法且交易时间非法的账单仍会写入坏来源流水 | `docs/reports/17_phase6_second_red_team_review_2026-08-01.md` |
| 当前产品面 | ✅ 全新 v2 页面已上线（旧路由已下线）；维护页与 status 路由已移除 | `app/main.py`、`templates/base.html` |
| 测试基线 | ⚠️ 直接 `pytest` 238 项通过，但未覆盖账单行无效日期/时间的解析与无残留要求 | `docs/reports/17_phase6_second_red_team_review_2026-08-01.md` |

（✅=已通过 🔄=进行中 ⏳=待执行）

## 已知剩余缺口（诚实披露）

1. 重构阶段 6 当前阻塞：结构合法但交易时间非法的账单可写入坏来源流水，后续会污染账本日期和统计。详见 `reports/17_phase6_second_red_team_review_2026-08-01.md`。
2. 重构规格中的待定项：in-memory 批量删除令牌是否替换（见 `decisions/03` 跟进候选）。
2. **正式库已完成重置**（2026-08-01 15:00，备份 `ledger.sqlite-20260801-150000.bak`、`ledger.sqlite-20260801-150040.bak`）；schema 已升级至 version 4（raw_type + 规则 CHECK + refund 唯一约束已验证）；待用户从新页面重新导入账单。
3. 若未来需要逐条追溯阶段 2 遗留空规则的隔离来源，可记录规则 ID/字段/原因；当前仅持久化隔离数量，不阻塞阶段 3。
4. 若未来需要逐条追溯 v4 迁移清理的多重退款关联，可记录退款来源、保留与隔离关联的 ID；当前仅持久化清理数量，不阻塞阶段 4。
5. 阶段 5 若需在导入历史展示无来源单号异常数，应持久化 `invalid_count`，或明确将其归入 `skipped_count`；当前仅由 `ImportResult` 返回。

## 进行中的工作

- 账单驱动重构：阶段 6 待验证账单行日期/时间；修复并最终红队复审通过后才可进入阶段七/交付验收

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
