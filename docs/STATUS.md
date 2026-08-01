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
| 重构阶段 1 | ✅ 审查通过：P1（备份后重置为干净新模型）与 P2（SQLite 一致性备份）已修复 | `docs/reports/01_phase1_review_2026-08-01.md`（approved）+ 本会话修复 |
| 当前产品面（未重构前） | ⚠️ 活动库将重置为干净新模型（旧表移除）；旧页面 `/` `/review` `/cleanup` 在阶段 5 重建前对新库不可用 | `docs/decisions/01_refactor_spec.md` §5 |
| 测试基线 | ✅ 直接 `pytest` 114 项通过（含重置迁移、半迁移遗留、WAL 一致性备份测试） | `pytest.ini` |

（✅=已通过 🔄=进行中 ⏳=待执行 ⚠️=已知限制）

## 已知剩余缺口（诚实披露）

1. 重构阶段 2~6 未实施：解析器（支付宝 CSV/微信 XLSX）、规则与观察期、退款匹配、页面重建、端到端测试（见 `decisions/01` §7）。
2. **正式库 `.data/ledger.sqlite` 尚未执行重置**：下次启动应用时自动一致性备份（`ledger.sqlite-<ts>.bak`）并移除旧表；旧页面在阶段 5 重建前对新库返回错误，属预期中间态。
3. 重构规格中的待定项：`import_sessions`/`import_rows`/`category_rules` 遗留表（已随重置迁移移除）、in-memory 批量删除令牌是否替换（见 `decisions/03` 跟进候选）。

## 进行中的工作

- 账单驱动重构：阶段 1 审查通过（2026-08-01，`docs/reports/01_phase1_review_2026-08-01.md`）；下一步阶段 2（账单解析与批次导入），待用户安排

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
