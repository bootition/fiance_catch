---
title: Inbox UX 优化验收（HTMX 局部刷新 / 退款候选刷新 / 界面紧凑化）
status: approved
category: reports
created: 2026-08-27
last-reviewed: 2026-08-27
---

# 25 Inbox UX 优化验收

**日期**：2026-08-27
**触发**：用户反馈 `/inbox` 三个交互问题——① 上下滚动闪烁加载、界面难看；② 定性后页面跳回顶部；③ 退款待办无按钮无指引。
**范围**：`/inbox` 页面交互重构 + 视觉紧凑化。不改变任何业务规则（退款仍须关联已入账原消费、高风险逐笔处理、分类区批量确认等语义不变）。

## 1. 根因（诚实披露）

| 用户现象 | 根因 |
|---|---|
| 滚动闪烁/重新加载 | base.html 加载了 htmx 1.9.12 但页面**零 `hx-*` 使用**；所有表单均为传统整页 POST → 每次提交整页重渲染（1756 条数据），肉眼即"闪一下" |
| 定性后跳回顶部 | 同一根因：整页重载后浏览器滚动位置归零 |
| 退款待办无按钮 | 90 天窗口内无候选原消费时（常见于原消费还在分类区未确认），页面仅显示一行提示文字，无可操作项与后续指引 |

## 2. 改动清单

### 模板（templates/）
- 新增 `_risk_card.html`：单条高风险卡片（含退款候选表单 / 提现 / 定性表单），卡片带 `id="risk-{review_id}"`
- 新增 `_high_risk_section.html`：高风险区 section（`id="high-risk-section"`），顶部渲染 flash 提示
- 新增 `_category_section.html`：分类区 section（`id="category-section"`），顶部渲染 flash
- 重写 `inbox.html`：改为 include 两个 partial + datalist
- `base.html`：导航「待确认」计数包 `<span id="nav-pending-count">`，支持 `hx-swap-oob` 更新

### 后端（app/routers/inbox.py）
- 新增 `_section_response()`：渲染局部片段 + 追加待确认计数 OOB 片段
- 三个 POST 路由（`/inbox/confirm`、`/inbox/refund/link`、`/inbox/resolve`）改为返回对应 section 片段（flash 保留在片段内，兼容既有测试断言）
- 新增 `GET /inbox/refund-candidates/{review_id}`：单条退款卡片刷新（重新查询 90 天候选）；待办已处理则返回空片段（htmx 替换即移除卡片）

### 前端交互
- 所有表单加 `hx-post` + `hx-target`（section 或单卡）+ `hx-swap="outerHTML"`
- 提交按钮 `hx-disabled-elt="this"` 防双击重复提交
- 退款无候选：明确文案（"常见原因：这笔退款对应的消费还在分类区未确认"）+「刷新候选」按钮（hx-get 单卡刷新）

### 样式（static/app.css）
- 卡片/表格紧凑化（padding、控件统一 36px 高、表格行密度、hover）
- 新增 `.card-inner`（风险卡片）、`.flash-note`（局部刷新提示）、`.refund-hint`（无候选指引）、`.radio-row`（退款候选单选行）
- 分类区操作行 `nowrap` 不换行；小屏回退换行

## 3. 验证

- **测试**：`pytest` **273 passed**（原 270 基线 + 新增 `tests/test_inbox_ux.py` 3 项），无回归
  - 新增覆盖：confirm/resolve 返回局部片段（非整页、含 OOB 计数）、退款无候选指引与刷新路由、确认原消费后刷新候选出现、关联后刷新返回空片段
- **真实库冒烟**（`.data/ledger.sqlite`）：GET /inbox 200（含刷新候选按钮）；无效 review_id 的 resolve 安全失败（"处理失败"）；refund-candidates 对已处理/不存在待办返回空片段
- **兼容性**：既有测试对 POST 响应文本的断言（"已确认 2 项"/"已定性"/"已关联退款"）全部保留通过

## 4. 结论

**通过。** 三个用户反馈问题均闭环：闪烁与跳顶消除（局部刷新 + 滚动位置自然保持）；退款无候选时给出明确指引与刷新入口。业务规则零变更，测试基线提升至 273。

## 5. 后续可选优化（不在本次范围）

- htmx 依赖走本地静态文件（当前 CDN，离线不可用），可下载至 static/ 并改 base.html 引用
- 分类区大表可加分页/搜索（当前 1512 条分组一次渲染，随处理量下降会缓解）
- DeprecationWarning（fastapi/starlette `asyncio.iscoroutinefunction`，Python 3.16 移除）建议升级 fastapi 前记录为技术债
