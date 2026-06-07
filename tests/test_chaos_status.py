import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    # Set TESTING before importing app so that the lifespan seed() is skipped.
    # Use monkeypatch so the env var is cleaned up automatically after each test.
    monkeypatch.setenv("TESTING", "1")
    from mock_site.backend.main import app
    with TestClient(app) as c:
        yield c


def test_chaos_status_disabled_by_default(client, tmp_path, monkeypatch):
    monkeypatch.setattr("mock_site.backend.chaos.CHAOS_CONFIG_PATH", tmp_path / "nonexistent.yaml")
    r = client.get("/chaos/status")
    assert r.status_code == 200
    assert r.json() == {"enabled": False, "selector_chaos": False}


def test_chaos_status_enabled(client, tmp_path, monkeypatch):
    cfg = tmp_path / "chaos.yaml"
    cfg.write_text("chaos:\n  enabled: true\n  rules: []\n")
    monkeypatch.setattr("mock_site.backend.chaos.CHAOS_CONFIG_PATH", cfg)
    r = client.get("/chaos/status")
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "selector_chaos": False}


def test_chaos_status_selector_chaos_enabled(client, tmp_path, monkeypatch):
    cfg = tmp_path / "chaos.yaml"
    cfg.write_text("chaos:\n  enabled: false\n  rules: []\nselector_chaos:\n  enabled: true\n")
    monkeypatch.setattr("mock_site.backend.chaos.CHAOS_CONFIG_PATH", cfg)
    r = client.get("/chaos/status")
    assert r.status_code == 200
    assert r.json() == {"enabled": False, "selector_chaos": True}
