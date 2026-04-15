"""Unit tests for the configuration resolver (task003 U25–U27)."""
from __future__ import annotations

import os

import pytest

from skilltool.config import Config


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    # Point XDG_CONFIG_HOME at a private dir so the suite can't see the
    # developer's real ~/.config/skilltool/config.toml, and clear any
    # SKILLTOOL_* env vars that might leak in from the outer shell.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for var in (
        "SKILLTOOL_REGISTRY",
        "SKILLTOOL_TOKEN",
        "SKILLTOOL_TRANSPORT",
        "SKILLTOOL_SSH_HOST",
        "SKILLTOOL_SSH_USER",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def _write_config(tmp_path, toml: str) -> None:
    dir_ = tmp_path / "skilltool"
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "config.toml").write_text(toml, encoding="utf-8")


def test_env_transport_wins(monkeypatch):
    # U25: env var forces ssh even if the file says http.
    monkeypatch.setenv("SKILLTOOL_TRANSPORT", "ssh")
    cfg = Config.load()
    assert cfg.transport == "ssh"
    assert cfg.transport_source == "env"


def test_file_transport_when_env_unset(tmp_path):
    # U26: config.toml picks transport when no env is set.
    _write_config(tmp_path, 'transport = "ssh"\nregistry = "http://a:8765"\n')
    cfg = Config.load()
    assert cfg.transport == "ssh"
    assert cfg.transport_source == "file"


def test_default_transport_is_http(monkeypatch):
    # U27: with nothing set, transport falls back to "http".
    # Skip the localhost auto-probe — it would poke port 8765 in the
    # dev environment and is orthogonal to the transport default.
    monkeypatch.setattr(
        "skilltool.config._localhost_registry_running", lambda: False
    )
    cfg = Config.load()
    assert cfg.transport == "http"
    assert cfg.transport_source == "default"


def test_ssh_host_env_overrides_file(tmp_path, monkeypatch):
    _write_config(tmp_path, 'transport = "ssh"\nssh_host = "100.0.0.1"\n')
    monkeypatch.setenv("SKILLTOOL_SSH_HOST", "100.64.99.99")
    cfg = Config.load()
    assert cfg.ssh_host == "100.64.99.99"
    assert cfg.ssh_host_source == "env"


def test_ssh_user_defaults_to_skilltool(monkeypatch):
    monkeypatch.setattr(
        "skilltool.config._localhost_registry_running", lambda: False
    )
    cfg = Config.load()
    assert cfg.ssh_user == "skilltool"
    assert cfg.ssh_user_source == "default"


def test_localhost_autodetect_flips_registry_to_local(monkeypatch):
    # When nothing is set but localhost:8765 answers, registry auto-pins.
    monkeypatch.setattr(
        "skilltool.config._localhost_registry_running", lambda: True
    )
    cfg = Config.load()
    assert cfg.registry == "http://localhost:8765"
    assert cfg.registry_source == "auto"


def test_env_registry_beats_localhost_autodetect(monkeypatch):
    monkeypatch.setattr(
        "skilltool.config._localhost_registry_running", lambda: True
    )
    monkeypatch.setenv("SKILLTOOL_REGISTRY", "http://100.64.0.9:8765")
    cfg = Config.load()
    assert cfg.registry == "http://100.64.0.9:8765"
    assert cfg.registry_source == "env"
