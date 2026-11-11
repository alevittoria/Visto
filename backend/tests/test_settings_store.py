import os
import stat
from pathlib import Path

import pytest

from visto.adapters.ios.signing import IOSSigning
from visto.adapters.settings.store import KeyStatus, Settings, SettingsError, SettingsStore
from visto.home import VistoHome


def test_missing_files_give_defaults(tmp_path):
    store = SettingsStore(VistoHome(tmp_path))
    assert store.load() == Settings()
    assert store.api_key() == "" and store.key_status() == KeyStatus(False, None)


def test_round_trip_and_key_status(tmp_path):
    store = SettingsStore(VistoHome(tmp_path))
    store.save(Settings(price_refresh=False, ios=IOSSigning(team_id="TEAM123")))
    assert store.load().ios.team_id == "TEAM123" and store.load().price_refresh is False
    assert store.set_api_key("sk-test-abcd1234") == KeyStatus(True, "1234")
    assert store.api_key() == "sk-test-abcd1234"
    assert store.delete_api_key() == KeyStatus(False, None)


def test_home_is_created_on_first_write(tmp_path):
    store = SettingsStore(VistoHome(tmp_path / "new" / "home"))
    store.save(Settings(ios_saved_for_xcuitest="x"))
    assert store.load().ios_saved_for_xcuitest == "x"


def test_set_api_key_normalizes_and_rejects(tmp_path):
    store = SettingsStore(VistoHome(tmp_path))
    store.set_api_key("  sk-test-wxyz\n")
    assert store.api_key() == "sk-test-wxyz"
    for bad in ("", "   ", "sk test", "sk-\ttab", "x" * 8193):
        with pytest.raises(SettingsError) as error:
            store.set_api_key(bad)
        assert bad.strip() == "" or bad.strip() not in str(error.value)
    assert store.api_key() == "sk-test-wxyz"


def test_corrupt_files_raise_sanitized_errors(tmp_path):
    home = VistoHome(tmp_path)
    home.settings_file.write_text('{"format": 1, "price_refresh": "yes-SECRETISH"}')
    with pytest.raises(SettingsError) as error:
        SettingsStore(home).load()
    assert "SECRETISH" not in str(error.value) and "settings" in str(error.value).lower()
    home.secrets_file.write_text("not json sk-leak")
    with pytest.raises(SettingsError) as error:
        SettingsStore(home).api_key()
    assert "sk-leak" not in str(error.value)


def test_newer_format_is_refused(tmp_path):
    home = VistoHome(tmp_path)
    home.settings_file.write_text('{"format": 2}')
    with pytest.raises(SettingsError, match="newer"):
        SettingsStore(home).load()


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_secrets_file_is_owner_only(tmp_path):
    store = SettingsStore(VistoHome(tmp_path))
    store.set_api_key("sk-test-mode")
    assert stat.S_IMODE(VistoHome(tmp_path).secrets_file.stat().st_mode) == 0o600


def test_ios_capabilities_match_previous_mapping():
    signing = IOSSigning("T", "Apple Development", "com.example.wda", True, "~/dd")
    caps = signing.capabilities()
    assert caps["appium:xcodeOrgId"] == "T" and caps["appium:usePrebuiltWDA"] is True
    assert caps["appium:derivedDataPath"] == str(Path("~/dd").expanduser())
    assert caps["appium:xcodeSigningId"] == "Apple Development"
    assert caps["appium:updatedWDABundleId"] == "com.example.wda"
    assert IOSSigning().capabilities() == {}


def test_existing_openai_only_files_stay_valid_and_keep_the_key(tmp_path):
    # Files as written before providers were selectable: no provider field anywhere.
    home = VistoHome(tmp_path)
    home.settings_file.write_text('{"format": 1, "price_refresh": false}', encoding="utf-8")
    home.secrets_file.write_text('{"format": 1, "openai_api_key": "sk-old-9876"}', encoding="utf-8")
    store = SettingsStore(home)
    assert store.load().provider == "openai" and store.load().price_refresh is False
    assert store.active() == ("openai", "sk-old-9876")
    assert store.key_status() == KeyStatus(True, "9876")


def test_each_provider_keeps_its_own_key(tmp_path):
    store = SettingsStore(VistoHome(tmp_path))
    store.set_api_key("sk-openai-0001")
    store.set_api_key("sk-ant-0002", "anthropic")
    store.set_api_key("gem-0003", "gemini")
    assert store.api_key() == "sk-openai-0001"
    assert store.api_key("anthropic") == "sk-ant-0002"
    assert store.delete_api_key("anthropic") == KeyStatus(False, None)
    assert store.api_key("openai") == "sk-openai-0001" and store.api_key("gemini") == "gem-0003"
    store.delete_api_key("openai")
    store.delete_api_key("gemini")
    assert not store._home.secrets_file.exists()


def test_active_provider_round_trips_and_unknown_is_refused(tmp_path):
    store = SettingsStore(VistoHome(tmp_path))
    store.set_api_key("gem-0003", "gemini")
    store.save(Settings(provider="gemini"))
    assert store.active() == ("gemini", "gem-0003")
    with pytest.raises(SettingsError):
        store.save(Settings(provider="unknown"))
    with pytest.raises(SettingsError):
        store.set_api_key("k", "unknown")
    store._home.settings_file.write_text('{"format": 1, "provider": "unknown"}', encoding="utf-8")
    with pytest.raises(SettingsError, match="settings file is invalid"):
        store.load()
