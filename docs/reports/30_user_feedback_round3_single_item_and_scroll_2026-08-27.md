---
title: 用户反馈第三轮修复（分组规则透明化 / 单笔单独处理 / 处理位置保持）
status: approved
category: reports
created: 2026-08-27
last-reviewed: 2026-08-27
---

# 30 用户反馈第三轮修复

**日期**：2026-08-27
**触发**：用户反馈——① 待确认分组合并依据不透明，合并不当时无法单独处理某笔；② 完成一笔归类后页面自动跳到底部，应保持原位置。
**范围**：`/inbox` 分类区交互。业务规则不变。

## 1. 合并规则（现在页面明示）

分类区合并条件：**商户/交易对方 + 平台 + 收支方向** 三项完全相同才会合成一组。
页面分类区顶部已明示该规则，并提示“若合并结果不合适，展开明细后可单笔处理”。

## 2. 单笔单独处理

- 展开某组「查看 N 笔明细」后，每笔都有「单独处理」按钮。
- 点击后按需加载该笔的独立表单（不增加整页 DOM），可选择类型/分类并「确认此笔」。
- 处理结果只影响这一笔：该笔入账离开待确认，同组其余笔仍保持待确认。
- 新增路由：`GET /inbox/item-form/{review_id}`、`POST /inbox/confirm-item`。
- 服务层 `confirm_review_item()`：方向/类型/分类约束与批量确认完全一致；写审计事件并同步批次计数；不创建规则。

## 3. 处理后保持滚动位置

根因：HTMX 局部刷新替换目标区域时，未锚定用户视口；页面高度变化后浏览器把视口甩到底部。

修复：
- `htmx:beforeSwap` 记录目标元素（高风险区/分类区表格/风险卡片）的视口 top 与当前 scrollY。
- `htmx:afterSwap` 按新元素 top 的位移差补偿 `window.scrollTo`，使同一元素保持在原视口位置。
- CSS 对 Inbox 目标区域关闭 `overflow-anchor`，排除浏览器原生滚动锚定干扰。

## 4. 验证

- `PYTHONPATH=. pytest`：**290 passed**（288 基线 + 新增 2 项：组内单笔处理闭环、滚动锚定脚本存在）。
- 正式库副本冒烟：`/inbox` 显示合并规则与单笔入口；`GET /inbox/item-form/{review_id}` 返回单笔表单片段；单笔 POST 只处理目标笔。
- 正式库数据未改动（本轮仅代码与文档）。

## 5. 变更清单

- `app/decisions/confirm.py`：新增 `confirm_review_item()`，抽取方向/分类校验与批次计数同步
- `app/routers/inbox.py`：新增 `GET /inbox/item-form/{review_id}`、`POST /inbox/confirm-item`
- `templates/_category_section.html`：明示合并规则
- `templates/_category_table.html`：明细表增加「单独处理」按需加载入口
- `templates/_single_confirm_form.html`（新增）：单笔确认表单片段
- `templates/inbox.html`：单笔表单类型/分类联动 + 滚动位置锚定脚本
- `static/app.css`：关闭 Inbox 区域 overflow-anchor
- 测试：`tests/test_inbox_ux.py` 新增 2 项
- 文档：PRD、用户使用手册、STATUS 同步
