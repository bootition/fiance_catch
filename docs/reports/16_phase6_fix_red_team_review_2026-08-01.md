---
title: 重构阶段 6 修复红队复审报告
status: approved
category: reports
created: 2026-08-01
last-reviewed: 2026-08-01
supersedes: reports/15_phase6_red_team_review_2026-08-01.md
---

# 重构阶段 6 修复红队复审报告

## 结论

**不通过。**

原始表单篡改漏洞已修复：非法日期、平台、规则字段/类型和无效支付宝内容都不会写库或返回 500，上传临时文件会清理。红队进一步发现损坏的微信 XLSX 仍返回 HTTP 500，因为路由没有捕获 `openpyxl`/ZIP 解析异常。

审查依据：`docs/decisions/01_refactor_spec.md`（`last-reviewed: 2026-08-01`）第 7.6、8 节。

## 已通过的修复验证

- 非法平台、坏支付宝 CSV、伪装 XLSX、非法规则字段均返回 HTTP 200 错误页，且没有批次或账本残留。
- 非法 ISO 日期及无效日历日期返回 HTTP 400，账本无新增记录。
- 非法交易类型/金额不写库。
- 上传失败后临时目录仅保留 SQLite 文件，上传临时文件已清理。
- `pytest`：236 passed。

## Finding

### P2：损坏微信 XLSX 上传返回 HTTP 500

**位置：** `app/routers/imports.py:64-72`、`app/importing/wechat.py:78`

上传路由只捕获 `ValueError` 和 `FileNotFoundError`。微信解析使用 `openpyxl.load_workbook()`，损坏/不完整/伪造的 `.xlsx` 会抛 `zipfile.BadZipFile` 或 openpyxl 异常，不属于当前捕获范围。

红队复现：

```text
POST /imports/new
platform=wechat
file=bad.xlsx，内容为非 ZIP 字节
```

结果：HTTP 500。数据库没有批次残留，但用户无法得到可处理的导入失败提示。

**修复要求：** 在解析器将已知工作簿/ZIP 异常转换为 `ValueError`，或在上传路由捕获受限的解析异常集合并渲染错误页；不要使用宽泛 `except Exception` 掩盖编程错误。新增页面级端到端测试验证损坏微信文件 HTTP 200/400、无批次残留、临时文件清理。

## 阶段 6 重新通过条件

1. 修复损坏微信 XLSX 的错误转换与用户提示。
2. 新增 HTTP 端到端回归测试。
3. 直接执行 `pytest` 并通过，再进行最终红队复审。
