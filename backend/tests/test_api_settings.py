"""Settings routes: local files and fakes only; no provider or device contact."""

import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from visto.adapters.api.service import ExecutionService, WebSettings
from visto.adapters.api.settings import SettingsRoutesConfig
from visto.adapters.settings.store import SettingsStore
from visto.application.ports import PortError
from visto.composition import create_app
from visto.home import VistoHome

LOCAL = {"X-Visto": "local-console"}
IOS_BODY = {
    "team_id": "TEAM000000",
    "signing_id": "Example Signing",
    "wda_bundle_id": "example.wda.runner",
    "prebuilt_wda": False,
    "wda_derived_data": "",
}


@pytest.fixture
def home():
    root = Path(__file__).resolve().parents[2] / ".work" / ("settings-api-" + uuid4().hex)
    root.mkdir(parents=True)
    yield VistoHome(root)
    shutil.rmtree(root)


@pytest.fixture
def host():
    return SimpleNamespace(version="1.0.0", probes=[], providers=[], outcome=None)


def make_client(home, host, *, ios):
    service = ExecutionService(WebSettings(home.data, SettingsStore(home), home.leases))

    async def prober(key, model, timeout_ms, provider):
        host.probes.append((key, model, timeout_ms))
        host.providers.append(provider)
        if host.outcome is not None:
            raise PortError(host.outcome)
        return {"id": model}

    app = create_app(service)
    app.state.settings_host = SettingsRoutesConfig(
        ios_supported=ios,
        wda_project=lambda: home.root / "wda-project",
        xcuitest_version=lambda: host.version,
        prober=prober,
    )
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def client(home, host):
    with make_client(home, host, ios=False) as c:
        yield c


@pytest.fixture
def client_non_ios(client):
    return client


@pytest.fixture
def client_ios(home, host):
    with make_client(home, host, ios=True) as c:
        yield c


def test_key_is_write_only(client):
    r = client.put("/api/settings/key", json={"api_key": "sk-test-abcd9876"}, headers=LOCAL)
    assert r.json() == {"configured": True, "last4": "9876", "storage": "file"}
    for path in ("/api/settings", "/api/readiness"):
        assert "sk-test-abcd9876" not in client.get(path).text
    assert client.get("/api/settings").json()["key"]["last4"] == "9876"
    deleted = client.delete("/api/settings/key", headers=LOCAL)
    assert deleted.json() == {"configured": False, "last4": None, "storage": "file"}


def test_settings_writes_require_local_console(client):
    for call in (
        lambda **kw: client.put("/api/settings/key", json={"api_key": "sk-x"}, **kw),
        lambda **kw: client.delete("/api/settings/key", **kw),
        lambda **kw: client.put("/api/settings", json={"price_refresh": True}, **kw),
        lambda **kw: client.post("/api/settings/key/verify", **kw),
    ):
        assert call().status_code == 403
        assert call(headers={**LOCAL, "Origin": "http://evil.example"}).status_code == 403


def test_key_validation_error_never_echoes_value(client):
    r = client.put("/api/settings/key", json={"api_key": "sk bad SPACE"}, headers=LOCAL)
    assert r.status_code == 422 and "SPACE" not in r.text
    r = client.put("/api/settings/key", json={"api_key": 5, "extra": "SECRET"}, headers=LOCAL)
    assert r.status_code == 422 and "SECRET" not in r.text


def test_ios_hidden_and_rejected_off_mac(client_non_ios):
    body = client_non_ios.get("/api/settings").json()
    assert body["ios"] is None and body["ios_supported"] is False
    assert body["wda_project"] is None and body["wda_rebuild_needed"] is False
    r = client_non_ios.put(
        "/api/settings", json={"price_refresh": True, "ios": IOS_BODY}, headers=LOCAL
    )
    assert r.status_code == 422 and "not available" in r.json()["detail"]


def test_settings_roundtrip_and_strict_body(client_ios):
    r = client_ios.put(
        "/api/settings", json={"price_refresh": False, "ios": IOS_BODY}, headers=LOCAL
    )
    assert r.status_code == 200
    body = client_ios.get("/api/settings").json()
    assert body["price_refresh"] is False and body["ios"] == IOS_BODY
    assert body["wda_project"].endswith("wda-project")
    bad = client_ios.put("/api/settings", json={"price_refresh": True, "x": 1}, headers=LOCAL)
    assert bad.status_code == 422
    kept = client_ios.put("/api/settings", json={"price_refresh": True}, headers=LOCAL).json()
    assert kept["ios"] == IOS_BODY


def test_wda_rebuild_needed_after_driver_change(client_ios, host):
    client_ios.put("/api/settings", json={"price_refresh": True, "ios": IOS_BODY}, headers=LOCAL)
    assert client_ios.get("/api/settings").json()["wda_rebuild_needed"] is False
    host.version = "99.0.0"
    assert client_ios.get("/api/settings").json()["wda_rebuild_needed"] is True


def test_settings_read_reports_store_error(client, home):
    home.root.mkdir(parents=True, exist_ok=True)
    home.settings_file.write_text("{broken")
    r = client.get("/api/settings")
    assert r.status_code == 503 and "broken" not in r.text
    put = client.put("/api/settings", json={"price_refresh": True}, headers=LOCAL)
    assert put.status_code == 503


def test_verify_without_key_makes_no_probe(client, host):
    assert client.post("/api/settings/key/verify", headers=LOCAL).json() == {"result": "missing"}
    assert host.probes == []


@pytest.mark.parametrize(
    ("code", "result"),
    [
        (None, "ok"),
        ("provider: http_401", "unauthorized"),
        ("provider: http_404", "model_unavailable"),
        ("provider: timeout", "unreachable"),
        ("provider: http_500", "unreachable"),
    ],
)
def test_verify_maps_probe_outcomes(client, host, code, result):
    client.put("/api/settings/key", json={"api_key": "sk-test-abcd9876"}, headers=LOCAL)
    host.outcome = code
    r = client.post("/api/settings/key/verify", headers=LOCAL)
    assert r.json() == {"result": result}
    assert host.probes == [("sk-test-abcd9876", "gpt-6-luna", 10000)]


def test_providers_listed_with_their_own_key_status(client, home):
    SettingsStore(home).set_api_key("sk-openai-0001")
    body = client.get("/api/settings").json()
    assert body["provider"] == "openai"
    assert body["key"] == {"configured": True, "last4": "0001", "storage": "file"}
    listed = {entry["id"]: entry for entry in body["providers"]}
    assert set(listed) == {"openai", "anthropic", "gemini"}
    assert listed["anthropic"]["key"]["configured"] is False
    assert listed["gemini"]["default_model"] == listed["gemini"]["models"][0]
    assert "sk-openai-0001" not in str(body)


def test_switching_provider_keeps_every_stored_key(client, home):
    store = SettingsStore(home)
    store.set_api_key("sk-openai-0001")
    put = client.put(
        "/api/settings/key", json={"api_key": "sk-ant-0002", "provider": "anthropic"}, headers=LOCAL
    )
    assert put.json()["last4"] == "0002"
    switched = client.put(
        "/api/settings", json={"price_refresh": True, "provider": "anthropic"}, headers=LOCAL
    )
    assert switched.json()["provider"] == "anthropic"
    assert switched.json()["key"]["last4"] == "0002"
    assert store.api_key("openai") == "sk-openai-0001"
    readiness = client.get("/api/readiness").json()
    assert readiness["provider"] == "anthropic" and readiness["provider_key_configured"]
    assert readiness["models"][0] == readiness["model"] == "claude-opus-5-5"
    # Dictation keeps using the OpenAI key, whichever provider is active.
    assert readiness["dictation_available"] is True
    # Key routes without a provider act on the active one.
    client.delete("/api/settings/key", headers=LOCAL)
    assert store.api_key("anthropic") == ""
    assert store.api_key("openai") == "sk-openai-0001"


def test_unknown_provider_is_rejected_without_writing(client, home):
    for response in (
        client.put("/api/settings", json={"price_refresh": True, "provider": "x"}, headers=LOCAL),
        client.put("/api/settings/key", json={"api_key": "k", "provider": "x"}, headers=LOCAL),
        client.delete("/api/settings/key?provider=x", headers=LOCAL),
        client.post("/api/settings/key/verify?provider=x", headers=LOCAL),
    ):
        assert response.status_code == 422
    assert not home.secrets_file.exists() and not home.settings_file.exists()


def test_verify_probes_the_named_provider_default_model(client, home, host):
    SettingsStore(home).set_api_key("gemini-key-0003", "gemini")
    response = client.post("/api/settings/key/verify?provider=gemini", headers=LOCAL)
    assert response.json() == {"result": "ok"}
    assert host.probes[-1][:2] == ("gemini-key-0003", "gemini-4-argon")
    assert host.providers[-1] == "gemini"
