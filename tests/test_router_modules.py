from importlib import import_module


def _route_paths(module_name: str) -> set[str]:
    module = import_module(module_name)
    return {route.path for route in module.router.routes}


def test_ledger_router_exposes_expected_paths():
    paths = _route_paths("app.routers.ledger")
    assert "/" in paths
    assert "/transactions" in paths
    assert "/transactions/{txn_id}/delete" in paths
    assert "/export.csv" in paths


def test_review_and_accounts_routers_expose_expected_paths():
    review_paths = _route_paths("app.routers.review")
    assert "/review" in review_paths

    account_paths = _route_paths("app.routers.accounts")
    assert "/accounts" in account_paths
    assert "/accounts/{account_id}/rename" in account_paths
    assert "/accounts/{account_id}/archive" in account_paths
    assert "/accounts/{account_id}/restore" in account_paths
    assert "/accounts/{account_id}/delete" in account_paths


def test_importing_and_bulk_delete_routers_expose_expected_paths():
    importing_paths = _route_paths("app.routers.importing")
    assert "/import" in importing_paths
    assert "/import/alipay/preview" in importing_paths
    assert "/import/preview/{session_id}" in importing_paths
    assert "/import/preview/{session_id}/row/{row_id}" in importing_paths
    assert "/import/preview/{session_id}/bulk-update" in importing_paths
    assert "/import/preview/{session_id}/bulk-delete" in importing_paths
    assert "/import/preview/{session_id}/commit" in importing_paths
    assert "/import/preview/{session_id}/discard" in importing_paths
    assert "/import/alipay" in importing_paths
    assert "/import/batches/{batch_id}/delete" in importing_paths

    bulk_delete_paths = _route_paths("app.routers.bulk_delete")
    assert "/transactions/bulk-delete/preview" in bulk_delete_paths
    assert "/transactions/bulk-delete/execute" in bulk_delete_paths
