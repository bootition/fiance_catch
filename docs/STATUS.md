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
| 重构阶段 2 | 🔄 审查未通过：解析、状态筛选、去重与真实账单冒烟通过；文件导入缺少事务原子性，失败会遗留部分批次且无法安全重试 | `docs/reports/03_phase2_review_2026-08-01.md` |
| 当前产品面 | ✅ `/` 为 v2 迁移状态维护页；旧页面路由已下线，阶段 5 重建 | `app/routers/status.py` |
| 测试基线 | ⚠️ 直接 `pytest` 135 项通过，真实账单冒烟通过；未覆盖导入中途失败的原子回滚、空交易号和零金额成功交易 | `docs/reports/03_phase2_review_2026-08-01.md` |

（✅=已通过 🔄=进行中 ⏳=待执行）

## 已知剩余缺口（诚实披露）

1. 重构阶段 2 当前阻塞：单文件导入不是原子事务，失败会遗留部分流水和不完整批次；须补回滚/重试、空交易号及零金额处理。详见 `reports/03_phase2_review_2026-08-01.md`。
2. 重构阶段 3~6 未实施：规则与观察期、待确认队列、退款候选匹配、页面重建、端到端测试（见 `decisions/01` §7）。
2. **正式库已完成重置**（2026-08-01 15:00，备份 `ledger.sqlite-20260801-150000.bak`、`ledger.sqlite-20260801-150040.bak`）；旧页面在阶段 5 重建前不可用，当前仅提供 `/` 维护状态页。
3. 重构规格中的待定项：in-memory 批量删除令牌是否替换（见 `decisions/03` 跟进候选）。
4. 阶段 2 边界说明：解析后的入账/待确认决策（规则匹配）属阶段 3；退款行已识别入库但退款匹配关联属阶段 4；导入 UI 属阶段 5。

## 进行中的工作

- 账单驱动重构：阶段 2 待修复导入原子性和异常来源流水处理；重新审查通过后，才能进入阶段 3（规则、观察期、待确认队列、批量确认）

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
