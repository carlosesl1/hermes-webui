"""New panels copy follows the fork's canonical English + optional pt policy."""
from tests.i18n_runtime import assert_locale_contract

KEYS = [
    "insights_period_7d", "insights_period_30d", "insights_period_90d", "insights_period_365d",
    "insights_custom_range", "insights_start_date", "insights_end_date",
    "insights_footer_range", "insights_range_fallback", "provider_quota_usage_limit",
    "settings_label_chat_todos_in_chat", "settings_desc_chat_todos_in_chat",
    "settings_desc_workspace_todos_tab", "remote_image_open", "remote_image_reason",
    "settings_note_workspace_todos_tab_disabled", "todos_tray_summary_active",
    "todos_tray_summary_done", "composer_attach_options", "composer_attach_photo",
    "composer_attach_files", "media_preview_unauthorized", "media_preview_forbidden",
    "media_preview_not_found", "pb_bindings_title", "pb_bindings_menu", "pb_close",
    "pb_field_workspaces", "pb_field_model", "pb_none_inherit", "pb_no_workspaces",
    "pb_no_options", "pb_mark_default", "pb_set_default", "pb_set_default_title",
    "pb_unbind_ws_title", "pb_add_workspace_placeholder", "pb_type_path",
    "pb_enter_ws_path", "pb_add", "pb_add_workspace_title", "pb_add_workspace_message",
    "pb_workspace_already_bound", "pb_cancel", "pb_save", "pb_updated", "pb_update_failed",
]


def test_new_panels_copy_resolves_in_every_locale_with_real_fallback():
    locales = assert_locale_contract(KEYS)
    assert len(locales) == 15
    assert locales["en"]["provider_quota_usage_limit"] == "Usage limit"
    assert locales["pt"]["provider_quota_usage_limit"] == "Limite de uso"
    assert locales["en"]["todos_tray_summary_active"] == "2 active · 3 total"
    assert locales["pt"]["todos_tray_summary_active"] == "2 ativas · 3 no total"
    assert locales["en"]["pb_bindings_title"] == "Project settings — 2"
    for locale, values in locales.items():
        for key in KEYS:
            assert values[key] and values[key] != key, (locale, key)
            # assert_locale_contract verified native text OR exact English fallback,
            # including interpolation; native translations must not be overwritten.
        assert values["insights_footer_range"].count("{start}") == 1
        assert values["insights_footer_range"].count("{end}") == 1
        assert values["insights_range_fallback"].count("{days}") == 1
