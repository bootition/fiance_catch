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


def test_review_router_exposes_expected_paths():
    review_paths = _route_paths("app.routers.review")
    assert "/review" in review_paths


def test_cleanup_and_bulk_delete_routers_expose_expected_paths():
    cleanup_paths = _route_paths("app.routers.cleanup")
    assert "/cleanup" in cleanup_paths

    bulk_delete_paths = _route_paths("app.routers.bulk_delete")
    assert "/transactions/bulk-delete/preview" in bulk_delete_paths
    assert "/transactions/bulk-delete/execute" in bulk_delete_paths
