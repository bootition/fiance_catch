from importlib import import_module


def test_router_support_modules_expose_expected_helpers():
    settings_access = import_module("app.router_support.settings_access")
    request_parsing = import_module("app.router_support.request_parsing")
    validation = import_module("app.validation")
    navigation = import_module("app.router_support.navigation")
    importing_shared = import_module("app.router_support.importing_shared")
    bulk_delete_shared = import_module("app.router_support.bulk_delete_shared")

    assert hasattr(settings_access, "current_settings")
    assert hasattr(settings_access, "configure_settings")
    assert hasattr(request_parsing, "_resolve_range")
    assert hasattr(validation, "validate_iso_date")
    assert hasattr(navigation, "_build_secondary_page_context")
    assert hasattr(navigation, "_cleanup_url")
    assert hasattr(importing_shared, "_is_valid_import_batch_id")
    assert hasattr(importing_shared, "_issue_bulk_delete_token")
    assert hasattr(bulk_delete_shared, "_build_bulk_delete_filters")
    assert hasattr(bulk_delete_shared, "_is_empty_bulk_delete_filters")
