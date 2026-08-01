# 文档地图（Documentation Map）

本目录按 **docs-as-code 规范**（Diátaxis 四象限 + ADR + ISO/IEC 26511 生命周期）组织。
**阅读顺序：先读 `STATUS.md`，再按需进入各分类。** 历史文档一律不构成当前结论。

## 目录结构

| 路径 | 类型 | 内容 | 是否当前有效 |
|---|---|---|---|
| `STATUS.md` | 状态 | **当前状态唯一权威**（Verdict、剩余缺口、进行中工作） | ✅ 是 |
| `decisions/` | 决策 | 重构规格、架构说明、工程历史 | 01/02 ✅；03 historical |
| `reports/` | 报告 | 审计/验收/审查等事件型快照（NN_主题_YYYY-MM-DD.md） | 仅最新 ✅；其余 superseded |
| `runbooks/` | 手册 | 运维/证据保全操作手册 | ✅ 是 |
| `contracts/` | 合同 | 正式签署的设计合同 | ✅ 是 |
| `evidence/` | 证据 | 机器生成的证据 JSON、事故证据 | 证据，非结论 |
| `archive/` | 归档 | 已废弃/完成的研究快照、执行计划 | ❌ 否（只读） |

## 文档生命周期（Lifecycle）

```
draft → approved → superseded → archived
```

| 状态 | 含义 | AI/读者处理方式 |
|---|---|---|
| `approved` | 当前有效结论 | 可作建议依据 |
| `superseded` | 已被更新文档取代（见 front-matter `superseded-by`） | 仅作追溯证据，**不得**作为当前结论 |
| `historical` | 已完成使命的历史决策（不再演进） | 同上 |
| `archived` | 已归档 | 不读（除非追溯历史） |

每个文档头部 YAML front-matter 示例：

```yaml
---
title: <标题>
status: superseded        # approved | superseded | historical | archived
category: reports         # decisions | reports | runbooks | contracts | archive
created: YYYY-MM-DD       # 可选
last-reviewed: YYYY-MM-DD # 必填：最后核验日期
supersedes: ...           # 可选：取代了谁
superseded-by: ...        # 可选：被谁取代（相对 docs/ 的路径）
---
```

## 当前有效的文档（Current Truth）

- `STATUS.md` — 当前状态唯一权威
- `decisions/01_refactor_spec.md` — 账单驱动重构规格（验收合同，活文档；等待用户确认后实施）
- `decisions/02_architecture.md` — 当前架构说明
- `decisions/03_engineering_history.md` — 工程历史（historical，仅追溯）
- `runbooks/`、`contracts/` — 手册与合同（暂无）

## 新文档流程（Checklist）

1. 判断类型：合同/PRD → `decisions/`；事件报告 → `reports/`；操作手册 → `runbooks/`；证据 → `evidence/`；其余 → 会话产物放 `.planning/`
2. 加 front-matter（status/category/last-reviewed，必要时 supersedes/superseded-by）
3. 若产生或取代结论 → 更新 `STATUS.md`
4. 若旧文档被取代 → 改其 `status: superseded` + `superseded-by`（永不删除）

## 迁移说明（如适用）

本次治理迁移（2026-08-01）旧路径 → 新路径对照：

| 旧路径 | 新路径 |
|---|---|
| `docs/refactor-spec.md` | `docs/decisions/01_refactor_spec.md` |
| `docs/architecture.md` | `docs/decisions/02_architecture.md` |
| `docs/history.md` | `docs/decisions/03_engineering_history.md` |
| `docs/discussions/alipay-import-discussion.md` | `docs/archive/2026-06-07_alipay-import-discussion.md` |
| `findings.md`（根目录） | `.planning/2026-08-01-refactor/findings.md`（不入 git） |
| `progress.md`（根目录） | `.planning/2026-08-01-refactor/progress.md`（不入 git） |
| `task_plan.md`（根目录） | `.planning/2026-08-01-refactor/task_plan.md`（不入 git） |
