---
title: Inbox 滚动性能优化（高风险区分页 + 渲染优化）
status: approved
category: reports
created: 2026-08-27
last-reviewed: 2026-08-27
---

# 26 Inbox 滚动性能优化

**日期**：2026-08-27
**触发**：用户反馈 `/inbox` 修复 HTMX 局部刷新后，**上下滚动仍卡顿闪烁**。
**性质**：渲染性能问题（区别于报告 25 的"提交后整页重载"），本次为滚动性能专项修复。业务规则零变更。

## 1. 根因（量化诊断）

真实库 `/inbox` 首屏实际渲染规模（修复前实测）：

| 指标 | 数值 |
|---|---|
| 页面 HTML | 576 KB |
| 高风险卡片（.card-inner） | 243 个（每条含完整 form） |
| 表单（form） | 376 个 |
| 下拉框（select） | 573 个 |
| CSS 渐变背景 | 2 层 radial-gradient + 1 层 linear-gradient |

**主因**：573 个原生 `<select>` 控件 + 243 个嵌套表单卡片一次性全渲染，浏览器每次滚动都要对数千节点做布局与重绘；叠加多层径向渐变与大量阴影，滚动持续重绘 → 卡顿闪烁。

## 2. 修复

### A. 高风险区分页（治本）
- 后端 `_high_risk_items` 增加 `LIMIT/OFFSET` 分页（每页 20 条），只对当前页退款项计算候选；`_inbox_context` 带 `risk_page/risk_total/risk_total_pages` 并做越界回退（当前页处理空后自动回退有效页）
- 新增 `GET /inbox/high-risk?page=N`：高风险区翻页局部刷新（仅返回 section）
- 三个 POST 带 `risk_page` 隐藏字段，处理返回后停留在当前页
- 新增 `_risk_item()`：单条待办查询，供候选刷新路由复用（不再整表扫描）
- 模板 `_high_risk_section.html` 加分页导航（上一页/下一页 + 第 x/y 页 · 共 N 条）

### B. CSS 渲染优化（辅助）
- body 背景去掉 2 层 radial-gradient，仅保留 1 层 linear-gradient（降低滚动重绘成本）
- `.card-inner` 加 `content-visibility: auto; contain-intrinsic-size: auto 150px`（浏览器跳过屏幕外卡片渲染）

## 3. 验证

- **测试**：`pytest` **274 passed**（273 基线 + 新增 `test_inbox_high_risk_pagination`），无回归
- **真实库实测**（`.data/ledger.sqlite`，243 条高风险待办）：
  - 第 1 页高风险卡片 **243 → 20**，select **573 → 394**
  - 分页导航正确显示「共 243 条 · 第 1/13 页」
  - 翻页 `GET /inbox/high-risk?page=2` 返回 20 条局部片段（非整页）

## 4. 结论

**通过。** 滚动卡顿主因（243 卡片 + 573 控件一次性渲染）已通过分页消除；叠加渐变简化与 content-visibility，滚动渲染负担显著下降。业务规则与既有交互（局部刷新、退款候选刷新）均保持不变。

## 5. 后续可选（若仍觉分类区滚动不够顺）

- 分类区当前 198 行 × 2 select 一次性渲染，可继续做分页或「按平台/方向折叠 + 搜索」进一步降 DOM
- htmx 库建议本地化（当前 CDN 依赖，离线不可用）
