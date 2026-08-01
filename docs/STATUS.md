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
| 重构阶段 1 | 🔄 修复复审未通过：原迁移重置与一致性备份缺陷已修复；但生产 v2 库上的现有首页 `/` 返回 HTTP 500 | `docs/reports/02_phase1_fix_review_2026-08-01.md` |
| 当前产品面（未重构前） | ❌ 正式库已重置为 v2，旧表已移除；旧路由 `/` `/review` `/cleanup` 仍依赖旧表，当前不可用 | `docs/reports/02_phase1_fix_review_2026-08-01.md` |
| 测试基线 | ⚠️ 直接 `pytest` 114 项通过，但路由测试显式初始化旧 schema，未覆盖生产 v2 schema，遗漏首页 HTTP 500 | `docs/reports/02_phase1_fix_review_2026-08-01.md` |

（✅=已通过 🔄=进行中 ⏳=待执行 ⚠️=已知限制）

## 已知剩余缺口（诚实披露）

1. 重构阶段 1 当前阻塞：正式 v2 库上旧首页 `/` 返回 HTTP 500；需新增 v2 可用首页/维护页或提前实现最小新入口，并添加生产初始化路由测试。详见 `reports/02_phase1_fix_review_2026-08-01.md`。
2. 重构阶段 2~6 未实施：解析器（支付宝 CSV/微信 XLSX）、规则与观察期、退款匹配、页面重建、端到端测试（见 `decisions/01` §7）。
3. 重构规格中的待定项：in-memory 批量删除令牌是否替换（见 `decisions/03` 跟进候选）。

## 进行中的工作

- 账单驱动重构：原阶段 1 迁移缺陷已修复，但 v2 库上的应用入口不可用；修复并复审通过后，才能进入阶段 2（账单解析与批次导入）

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
