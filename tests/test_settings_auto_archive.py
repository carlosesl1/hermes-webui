"""Installation policy is explicit, persisted, and strict at the API boundary."""
import json
import pytest

@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    from api import config
    path = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", path)
    return config, path

def test_policy_defaults_off_and_roundtrips(settings_file):
    config, path = settings_file
    assert config.load_settings()["auto_archive_days"] == 0
    for days in [30, 1, 3650, 0]:
        assert config.save_settings({"auto_archive_days": days})["auto_archive_days"] == days
        assert json.loads(path.read_text())["auto_archive_days"] == days
        assert config.load_settings()["auto_archive_days"] == days

@pytest.mark.parametrize("value", [True, False, 1.5, "30", None, -1, 3651, [], {}])
def test_invalid_policy_cannot_change_saved_value(settings_file, value):
    config, path = settings_file
    config.save_settings({"auto_archive_days": 90})
    with pytest.raises(ValueError, match="auto_archive_days"):
        config.save_settings({"auto_archive_days": value, "bot_name": "must not save"})
    assert config.load_settings()["auto_archive_days"] == 90
    assert config.load_settings()["bot_name"] != "must not save"

@pytest.mark.parametrize("value", [True, "30", -2, 3651])
def test_invalid_on_disk_policy_is_disabled(settings_file, value):
    config, path = settings_file
    path.write_text(json.dumps({"auto_archive_days": value}))
    assert config.load_settings()["auto_archive_days"] == 0
