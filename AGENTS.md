# AGENTS.md — 项目智能体规则

本文件为 AI 助手（opencode/Codex 等）在本仓库工作的强制规则。

## 项目一句话

本地单用户账本 Web 应用：FastAPI + Jinja2 + HTMX + SQLite（`python -m uvicorn app.main:app --reload`）；测试 `PYTHONPATH=. pytest`。重构方向：账单驱动个人财务系统（规格见 `docs/decisions/01_refactor_spec.md`，实施前需用户确认）。

## 文档规则（强制，防止"读到过期结论"）

### 1. 必读顺序（任何任务开始时）

1. `docs/STATUS.md` — **当前状态唯一权威**，含当前裁决、剩余缺口、进行中工作
2. `docs/README.md` — 文档地图与生命周期规则
3. 任务相关代码/文档

### 2. 文档状态语义（front-matter `status` 字段）

| 状态 | 含义 | 处理 |
|---|---|---|
| `approved` | 当前有效 | 可作建议依据 |
| `superseded` | 已被取代（`superseded-by` 指向新文档） | **禁止**引用为当前结论；仅追溯用 |
| `historical` | 历史决策 | 同上 |
| `archived` | 已归档 | 默认不读 |

### 3. 禁止行为

- ❌ 引用 `docs/archive/`、`docs/evidence/` 内容作为结论依据（证据 ≠ 结论）
- ❌ 引用 `docs/decisions/03_engineering_history.md` 中的跟进候选作为已确定事项
- ❌ 把会话产物（findings/progress/task_plan）写入 `docs/` —— 一律放 `.planning/<date>-<topic>/`
- ❌ 修改归档/历史文档正文（只可改 front-matter 状态字段）

### 4. 必须行为

- ✅ 给用户建议时标注依据文档路径 + `last-reviewed` 日期
- ✅ 新文档必须带 front-matter（status/category/last-reviewed）
- ✅ 结论/状态变化时：更新 `docs/STATUS.md` → 新报告写 `docs/reports/` → 旧文档 front-matter 标 `superseded` + `superseded-by`
- ✅ 机器证据（JSON/hash 等）只放 `docs/evidence/`
- ✅ 重构相关讨论必须与 `docs/decisions/01_refactor_spec.md` 保持一致，变更规格先改文档

## Git 纪律（强制，防止工作丢失）

### 1. 提交时机

- ✅ **会话结束前**：若工作区有未提交变更，必须提交（这是默认动作，不再等待用户明确要求）
- ✅ **里程碑完成时**：每完成一个可独立验证的阶段立即提交
- ✅ 提交前先 `git status`，确认没有误入 .data/、构建产物、证据目录等被 ignore 的文件
- ❌ 禁止跨主题打包提交；按主题拆分（docs/ feat/ fix/ chore/）

### 2. 提交边界（哪些永不提交）

- `.data/`（正式数据库）、`*.bat`、`.omo/`、`.opencode/`、`.planning/`（会话产物）、`.pytest_cache/`、`__pycache__/`、`docs/evidence/evidence-s0|s1/` —— 见 `.gitignore`，如有遗漏先补 `.gitignore` 而非硬提交

### 3. Push 纪律

- ✅ 每个会话的提交完成后 **必须 `git push`**（remote 已配置：`origin` → `github.com/bootition/fiance_catch.git`）
- ✅ 推送前 `git fetch` 检查远程是否有新提交，有冲突先解决再推
- ✅ 重要历史分支/事故基线打 tag 并推送
- ⚠️ **网络失败必须如实告知**：push 失败（忘记开梯子、`Failed to connect to github.com`、认证失败等）时，**禁止**说"已推送/已提交完成"；必须明确告知用户「push 失败 + 原因 + 当前状态（提交在本地但未上远程）」，并提示重试（`git push`），直到 `git ls-remote origin` 确认远程已更新

### 4. 提交消息风格

`feat:` / `fix:` / `chore:` / `docs:` / `refactor:` 前缀 + 中文摘要 + 可选要点列表。

## 常用命令

- 运行：`python -m uvicorn app.main:app --reload`
- 测试：`PYTHONPATH=. pytest`
- 文档校验：`powershell -File C:\Users\qhdjxgm\.codex\skills\docs-git-governance\scripts\validate-frontmatter.ps1 -DocsRoot docs`

## 工作区边界

- `.data/` — 正式数据库（本地文件；不进 git，备份靠手动复制）
- `.planning/` — 会话计划与进度（AI 私有工作区，永不提交）
- `docs/evidence/` — 证据只增不改
- 账单样本在项目外（`D:\Mr.Q\掌控经济\消费记录`），含敏感财务信息，只做本地分析，不得写入 git 或文档
