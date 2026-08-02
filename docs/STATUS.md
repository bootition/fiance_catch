# 项目当前状态（单一真相源 / Single Source of Truth）

> **本文件是项目当前状态的唯一权威来源。** 任何建议、结论、验收判断必须以此为准；
> `reports/`、`archive/` 中的历史报告只作为追溯证据，不构成当前结论。
> 修改任何代码/数据/文档后，如影响状态，必须同步更新本文件。

- **最后更新**：2026-08-01
- **更新人**：AI 治理会话（docs-git-governance）

## 当前裁决（Verdict）

| 层面 | 状态 | 依据 |
|---|---|---|
| 产品方向 | ✅ 已获用户确认，正在实施：本地单用户账单驱动个人财务系统（逐笔落库、规则优先人工确认、第一版不接 AI） | `docs/decisions/01_refactor_spec.md` |
| 重构阶段 1 | ✅ 复审通过：P1/P2 修复已验证；生产 `init_db()` 后 `/` 返回维护页（200），旧路由已禁用（404） | `docs/reports/02_phase1_fix_review_2026-08-01.md`（approved） |
| 重构阶段 2 | ✅ 修复复审通过：单事务原子导入、失败回滚/重传、空单号拒绝和零金额保留均已验证 | `docs/reports/04_phase2_fix_review_2026-08-01.md` |
| 重构阶段 3 | ✅ 最终红队复审通过：旧 v2 空规则可安全隔离，保留有效规则并升级至 v3；高风险待办隔离、空规则防御、批次计数同步和已有库升级均已验证 | `docs/reports/08_phase3_final_red_team_review_2026-08-01.md` |
| 重构阶段 4 | ✅ 红队审查通过：P1-A 关联入口拒绝非退款来源（集中退款状态识别 + 必须有 refund_pending 待办）；P1-B refund_links.refund_source_id 唯一约束（schema v4 重建表清理多重链接脏数据，可追溯）；P2 候选过滤无剩余退款余额消费 | `docs/decisions/01_refactor_spec.md` §2.1/§2.3/§3.6/§5/§6/§7.4、`docs/reports/09_phase4_red_team_review_2026-08-01.md` + 本会话修复 |
| 当前产品面 | ✅ `/` 为 v2 迁移状态维护页；旧页面路由已下线，阶段 5 重建 | `app/routers/status.py` |
| 测试基线 | ✅ 直接 `pytest` 194 项通过（新增红队回归 8 项：普通收入/消费/调拨/无待办拒绝、重复关联拒绝、schema 唯一约束、全额退款排除候选、v4 迁移清理脏数据；正式库已升级 v4 实测验证） | `tests/test_red_team_phase4.py` |

（✅=已通过 🔄=进行中 ⏳=待执行）

## 已知剩余缺口（诚实披露）

1. 重构阶段 5~6 未实施：页面重建（概览/导入/待确认/流水/规则/批次）、简易补账、端到端测试（见 `decisions/01` §7）。
2. **正式库已完成重置**（2026-08-01 15:00，备份 `ledger.sqlite-20260801-150000.bak`、`ledger.sqlite-20260801-150040.bak`）；旧页面在阶段 5 重建前不可用，当前仅提供 `/` 维护状态页；正式库 schema 已升级至 version 4（raw_type + 规则 CHECK + refund 唯一约束已验证）。
3. 重构规格中的待定项：in-memory 批量删除令牌是否替换（见 `decisions/03` 跟进候选）。
4. 阶段 3 边界说明：退款行已入队（refund_pending）但匹配原消费属阶段 4；提现/人际的受约束逐笔确认命令保留到阶段 4；页面（概览/待确认/规则/批次）属阶段 5。
5. 阶段 5 若需在导入历史展示无来源单号异常数，应持久化 `invalid_count`，或明确将其归入 `skipped_count`；当前仅由 `ImportResult` 返回。
6. 若未来需要逐条追溯阶段 2 遗留空规则的隔离来源，可记录规则 ID/字段/原因；当前仅持久化隔离数量，不阻塞阶段 3。

## 进行中的工作

- 账单驱动重构：阶段 4 红队审查通过（2026-08-01，`docs/reports/09_phase4_red_team_review_2026-08-01.md`）；下一步阶段 5（重建页面：概览/导入/待确认/流水/规则/批次 + 简易补账），待用户安排

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
