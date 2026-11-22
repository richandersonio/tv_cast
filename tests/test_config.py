import json

import pytest

from tv_cast import config


@pytest.fixture(autouse=True)
def reset_config_state():
    old_device = config.get_current_device()
    old_discovered = list(config.get_discovered_devices())
    config.set_current_device(None)
    config.set_discovered_devices([])
    yield
    config.set_current_device(old_device)
    config.set_discovered_devices(old_discovered)


def test_save_and_load_config_round_trip(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_FILE", str(config_file))

    device = {
        "name": "Living Room TV",
        "ip": "192.168.1.50",
        "location": b"http://192.168.1.50:9197/dmr",
    }
    config.set_current_device(device)
    config.set_discovered_devices([device])

    config.save_config()

    config.set_current_device(None)
    config.set_discovered_devices([])
    config.load_config()

    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["device"]["location"] == "http://192.168.1.50:9197/dmr"
    assert config.get_current_device()["name"] == "Living Room TV"
    assert config.get_discovered_devices()[0]["ip"] == "192.168.1.50"


def test_save_discovered_devices_updates_existing_ip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "config.json"))

    config.set_current_device(None)
    config.set_discovered_devices([{"name": "Old", "ip": "192.168.1.20"}])

    config.save_discovered_devices([{"name": "New", "ip": "192.168.1.20"}])

    assert config.get_discovered_devices() == [{"name": "New", "ip": "192.168.1.20"}]


def test_save_discovered_devices_deduplicates_within_single_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "config.json"))

    config.save_discovered_devices([
        {"name": "First", "ip": "192.168.1.30"},
        {"name": "Second", "ip": "192.168.1.30"},
    ])

    assert config.get_discovered_devices() == [{"name": "Second", "ip": "192.168.1.30"}]


def test_forget_device_clears_current_device(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "config.json"))

    config.set_current_device({"name": "TV", "ip": "192.168.1.50"})
    config.forget_device()

    assert config.get_current_device() is None


def test_set_current_device_preserves_pairing_metadata_for_same_ip(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "cfg"
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(config, "CONFIG_FILE", str(cfg_file))

    config.set_current_device({
        "name": "TV",
        "ip": "192.168.1.50",
        "samsung_token": "tok-abc",
        "installed_apps": {"111299001912": "YouTube"},
    })
    # Re-selecting same IP from a rescan must retain token & installed_apps
    config.set_current_device({"name": "TV Renamed", "ip": "192.168.1.50"})
    current = config.get_current_device()
    assert current["name"] == "TV Renamed"
    assert current["samsung_token"] == "tok-abc"
    assert current["installed_apps"] == {"111299001912": "YouTube"}

    config.save_config()
    assert (cfg_file.stat().st_mode & 0o777) == 0o600
    assert (cfg_dir.stat().st_mode & 0o777) == 0o700


def test_save_config_is_atomic_when_write_fails(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_FILE", str(cfg_file))

    config.set_current_device({"name": "TV", "ip": "192.168.1.50", "samsung_token": "tok-abc"})
    config.set_discovered_devices([])
    config.save_config()
    original = cfg_file.read_text(encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(config.json, "dump", boom)
    config.set_current_device({"name": "Other", "ip": "192.168.1.51"})
    with pytest.raises(OSError):
        config.save_config()

    # The previous config (and its pairing token) survives, and no temp files are left.
    assert cfg_file.read_text(encoding="utf-8") == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json"]
