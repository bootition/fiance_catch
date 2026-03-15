---
title: Progress Log
created: 2026-02-25
---

## 2026-02-25
- Initialized planning files: task_plan.md, findings.md, progress.md
- Ran planning catchup script (no output)
- Clarified: desktop-first, manual entry, local database
- Picked app form: local web app (localhost)
- Confirmed required fields: date, income/expense, amount, category, note
- Selected implementation route: FastAPI + Jinja + HTMX + SQLite
- Wrote design doc: docs/plans/2026-02-25-local-ledger-webapp-design.md
- Wrote implementation plan: docs/plans/2026-02-25-local-ledger-webapp-implementation-plan.md
- User selected execution option 2 (new parallel session with executing-plans)

## Commands Run
| Time | Command | Result |
|---|---|---|
| 2026-03-15 | `git status --short --branch` in `.worktrees/main-py-refactor-batch-a` | Failed first with `detected dubious ownership`; succeeded after adding `safe.directory` |
| 2026-03-15 | `py -3.14 -m venv .venv` in `.worktrees/main-py-refactor-batch-a` | Failed in restricted env during `ensurepip` |
| 2026-03-15 | `py -3.14 -m venv .venv` + `python -m pip install -r requirements.txt` + `python -m pytest -q` in `.worktrees/main-py-refactor-batch-a` | Succeeded outside sandbox; baseline `26 passed` |
| 2026-03-15 | Added worktree `.vscode/settings.json` and ignore rules | Helps VS Code/Copilot reuse the existing `.venv` and keeps local environment artifacts out of git |
| 2026-03-15 | Tested and reverted worktree `pytest.ini` basetemp override | Reverted because shared worktree temp folders can become unreadable across different execution identities |
| 2026-03-15 | Expanded `.vscode/settings.json` | Added interpreter discovery and search/file excludes for `.venv` and `.tmp` |
| 2026-03-15 | Attempted cleanup of stale `.tmp` subdirectories with elevated delete / ownership commands | Unsuccessful; directories remain permission-stuck and should be treated as disposable environment residue |
| 2026-03-15 | Refactor planning check against current workspace | Determined the real refactor baseline is the current dirty workspace, not the older `.worktrees/main-py-refactor-batch-a` code |
| 2026-03-15 | Created `app/i18n.py` and `app/templates_core.py`; rewired `app/main.py` | Completed the first low-risk `main.py` refactor slice |
| 2026-03-15 | `python -m pytest tests/test_main_routes.py -q -k "review or import or transaction or export" --basetemp=.pytest-run-main-a` | Passed: `32 passed, 14 deselected` |
| 2026-03-15 | `python -m pytest -q --basetemp=.pytest-run-full-a` | Passed: `74 passed` |
| 2026-03-15 | Moved `TRANSLATIONS` into `app/i18n.py` | Reduced `main.py` size and centralized translation data |
| 2026-03-15 | Added `app/services/alipay_parser.py` and rewired parser call sites | Completed the second `main.py` refactor slice without changing route behavior |
| 2026-03-15 | `python -m pytest tests/test_main_routes.py -q -k "import" --basetemp=.pytest-run-import-b` | Passed: `20 passed, 26 deselected` |
| 2026-03-15 | `python -m pytest -q --basetemp=.pytest-run-full-d` | Passed: `74 passed` after parser extraction |
| 2026-03-15 | Added `tests/test_router_modules.py` before router extraction | Initial red run failed with `ModuleNotFoundError` for the planned router modules |
| 2026-03-15 | Added `app/router_common.py` plus `app/routers/{ledger,review,accounts,importing,bulk_delete}.py`; reduced `app/main.py` to app assembly | Completed the next `main.py` refactor batch across all requested router groups |
| 2026-03-15 | `python -m py_compile app/main.py app/router_common.py app/routers/*.py` via the existing venv interpreter | Passed after fixing one migrated string literal in `app/routers/ledger.py` |
| 2026-03-15 | `python -m pytest tests/test_router_modules.py -q --basetemp=.pytest-run-router-green-1` via the existing venv interpreter | Passed: `3 passed` |
| 2026-03-15 | Attempted focused `tests/test_main_routes.py` runs inside the restricted sandbox | Blocked by known `tmp_path`/`basetemp` permission errors during fixture setup and pytest cleanup |
| 2026-03-15 | `python -m pytest tests/test_main_routes.py -q -k "review or accounts or import or export or transactions" --basetemp=.pytest-run-main-refactor-7` outside sandbox | Passed: `28 passed, 18 deselected` |
| 2026-03-15 | `python -m pytest tests -q --basetemp=.pytest-run-full-e2` outside sandbox | Passed: `77 passed` after router extraction batch |
| 2026-03-15 | Investigated `app/router_common.py` for structural cleanup | Confirmed the file now spans multiple concern groups and can be split without changing route behavior |
| 2026-03-15 | Wrote a small cleanup plan into planning files | Next pass will split shared helpers by boundary and move router-specific helpers down into their owning routers |
| 2026-03-15 | Added `tests/test_router_support_modules.py` before cleanup implementation | Initial red run failed with `ModuleNotFoundError: No module named 'app.router_support'` |
| 2026-03-15 | Added `app/router_support/{settings_access,request_parsing,navigation,importing_shared,bulk_delete_shared}.py` | Split the shared helper surface into smaller modules |
| 2026-03-15 | Moved ledger-only helpers into `app/routers/ledger.py` and review-only helpers into `app/routers/review.py` | Reduced cross-router helper sprawl without changing route contracts |
| 2026-03-15 | Reduced `app/router_common.py` to a compatibility shim and rewired importing/bulk-delete routers to the new support modules | Completed the small structural cleanup pass |
| 2026-03-15 | `python -m py_compile app/router_common.py app/router_support/*.py app/routers/{ledger,review,importing,bulk_delete}.py` | Passed before regression runs |
| 2026-03-15 | `python -m pytest tests/test_router_support_modules.py tests/test_router_modules.py -q --basetemp=.pytest-run-router-common-green-1` | Passed: `4 passed` |
| 2026-03-15 | `python -m pytest tests/test_main_routes.py -q -k "review or import or transactions or export or bulk_delete" --basetemp=.pytest-run-router-common-green-2` | Passed: `32 passed, 14 deselected` |
| 2026-03-15 | `python -m pytest tests -q --basetemp=.pytest-run-router-common-cleanup` | Failed once: existing test caught default-note regression in `app/routers/ledger.py` |
| 2026-03-15 | Restored the default empty-note value in `app/routers/ledger.py` to `无` | Fixed the only regression introduced during the cleanup |
| 2026-03-15 | `python -m pytest tests/test_main_routes.py -q -k "note_empty_or_missing_defaults_to_wu or transactions or export" --basetemp=.pytest-run-router-common-fix-1` | Passed: `6 passed, 40 deselected` |
| 2026-03-15 | `python -m pytest tests -q --basetemp=.pytest-run-router-common-cleanup-2` | Passed: `78 passed` after router_common cleanup |
| 2026-03-15 | Read required skills plus edit-transaction target files | Captured the short design choice, A/B/C investigation split, and an executable plan for the next feature batch |
| 2026-03-15 | Chose the edit interaction model | Selected HTMX row-fragment editing over a separate page or fully inline table editing to preserve current ledger refresh behavior with minimal scope |
| 2026-03-15 | Added failing tests in `tests/test_repo.py` and `tests/test_main_routes.py` before implementation | Initial red run first failed at collection because `update_txn` did not exist yet, which confirmed the new repo capability was actually under test |
| 2026-03-15 | Implemented repo update path plus ledger edit routes/templates | Added `get_txn`, `update_txn`, row/edit fragment routes, update submit handling, and split transaction row templates |
| 2026-03-15 | `python -m pytest tests/test_repo.py tests/test_main_routes.py -q -k "update_txn or edit_transaction or update_transaction" --basetemp=.pytest-run-edit-transaction-green-2` | Passed outside sandbox: `5 passed, 47 deselected` after the initial basetemp cleanup issue was bypassed in the real user environment |
| 2026-03-15 | `python -m pytest tests/test_repo.py tests/test_repo_accounts.py tests/test_repo_summary.py -q --basetemp=.pytest-run-edit-transaction-repo` | Passed outside sandbox: `6 passed` |
| 2026-03-15 | `python -m pytest tests/test_main_routes.py -q --basetemp=.pytest-run-edit-transaction-routes` | Failed once: existing route coverage caught the default-note regression because the code wrote `鏃?` while the contract remained `无` |
| 2026-03-15 | Corrected `DEFAULT_NOTE` in `app/routers/ledger.py` and aligned the new edit test expectation | Fixed the only edit-transaction regression uncovered by the required route suite |
| 2026-03-15 | `python -m pytest tests/test_repo.py tests/test_main_routes.py -q -k "update_txn or edit_transaction or update_transaction" --basetemp=.pytest-run-edit-transaction-green-3` | Passed outside sandbox: `5 passed, 47 deselected` after the note-default fix |
| 2026-03-15 | `python -m pytest tests/test_main_routes.py -q --basetemp=.pytest-run-edit-transaction-routes-2` | Passed outside sandbox: `50 passed` |
| 2026-03-15 | `python -m pytest tests -q --basetemp=.pytest-run-edit-transaction-full-2` | Passed outside sandbox: `83 passed` |
| 2026-03-15 | Updated phase table in `task_plan.md` | Marked Phase 3/4 complete and moved Phase 5 to wrap-up tracking |
| 2026-03-15 | `git worktree list` + `git remote -v` | Confirmed temporary worktree exists and `origin` is configured |
| 2026-03-15 | `git worktree remove .worktrees/main-py-refactor-batch-a; git worktree prune; git status --short --branch; git push origin main` | Push succeeded (`main -> origin/main`); worktree removal was blocked because the temporary worktree contains local changes |
| 2026-03-15 | `git -C .worktrees/main-py-refactor-batch-a status --short` | Found only `.gitignore` modified and untracked `.vscode/`; deferred force-removal for safety |
| 2026-03-15 | Final wrap-up status update in `task_plan.md` | Marked Phase 5 complete with note: optional worktree cleanup pending explicit confirmation |
| 2026-03-15 | `git worktree remove --force .worktrees/main-py-refactor-batch-a; git worktree prune; git worktree list; git status --short --branch` | Git metadata cleanup succeeded (`git worktree list` shows only main); filesystem deletion reported non-empty directory |
| 2026-03-15 | `Remove-Item ... ; git worktree prune; git worktree list; git status --short --branch` | Main repository remained clean; filesystem residue still present under `.worktrees/main-py-refactor-batch-a` |
| 2026-03-15 | `cmd /c "rmdir /s /q .worktrees\\main-py-refactor-batch-a"` + verification | Removal failed on permission-denied `.tmp` subdirectories |
| 2026-03-15 | `takeown` + `icacls` + `rmdir` retry | Blocked by missing ownership privilege for current user; residue remains but no active Git worktree reference |
