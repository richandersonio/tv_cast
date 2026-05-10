import json

from tv_cast import config


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
