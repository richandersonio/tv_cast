"""Tests for the command-line interface: dispatch, status output, and CLI commands."""

import logging

import pytest

from tv_cast import cli, config, conversion

TV = {"name": "Living Room TV", "ip": "192.168.1.50", "castable": True}


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point config at a temp dir and restore global device state afterwards."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(cli, "YOUTUBE_CACHE_DIR", str(tmp_path / "youtube"))
    monkeypatch.setattr(conversion, "IMAGE_CACHE_DIR", str(tmp_path / "images"))
    old_device = config.get_current_device()
    old_devices = config.get_discovered_devices()
    config.set_current_device(None)
    config.set_discovered_devices([])
    yield tmp_path
    config.set_current_device(old_device)
    config.set_discovered_devices(old_devices)


def _record(calls, name, result=None):
    def fake(*args, **kwargs):
        calls.append((name, args, kwargs))
        return result

    return fake


def _record_async(calls, name, result=None):
    async def fake(*args, **kwargs):
        calls.append((name, args, kwargs))
        return result

    return fake


# --- version / verbose -------------------------------------------------------


def test_version_flag_prints_package_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.run_cli(["--version"])
    assert exc.value.code == 0
    assert config.APP_VERSION in capsys.readouterr().out


def test_verbose_flag_enables_debug_logging(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli.logging, "basicConfig", lambda **kw: seen.update(level=kw["level"])
    )
    monkeypatch.setattr(cli, "show_status", lambda: None)
    cli.run_cli(["--status", "--verbose"])
    assert seen["level"] == logging.DEBUG

    cli.run_cli(["--status"])
    assert seen["level"] == logging.WARNING


# --- dispatch ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "target", "is_async"),
    [
        (["--status"], "show_status", False),
        (["--list-devices"], "list_devices_cli", False),
        (["--forget"], "forget_device", False),
        (["--clear-cache"], "clear_cache", False),
        (["--scan"], "scan_devices_cli", True),
        (["--scan-all"], "scan_all_devices_cli", True),
        (["--tv-state"], "show_tv_state_cli", True),
        (["--tv-apps"], "list_tv_apps_cli", True),
        (["--select-device"], "device_menu", True),
        (["--stop"], "stop_playback", True),
        ([], "run_interactive", False),
    ],
)
def test_run_cli_dispatches_each_command(monkeypatch, argv, target, is_async):
    calls = []
    fake = _record_async(calls, target) if is_async else _record(calls, target)
    monkeypatch.setattr(cli, target, fake)
    cli.run_cli(argv)
    assert [c[0] for c in calls] == [target]


def test_run_cli_device_flag_selects_device():
    cli.run_cli(["--device", "192.168.1.77"])
    assert config.get_current_device()["ip"] == "192.168.1.77"


def test_run_cli_media_commands_pass_duration_and_restore(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "cast_image_cli", _record(calls, "image"))
    monkeypatch.setattr(cli, "cast_rio_cli", _record(calls, "rio"))
    monkeypatch.setattr(cli, "cast_rio_live_cli", _record(calls, "rio_live"))

    cli.run_cli(["--image", "a.png"])
    cli.run_cli(["--rio", "--no-restore"])
    cli.run_cli(["--rio-live", "-d", "30"])

    assert calls == [
        ("image", ("a.png", 10), {"restore": True}),
        ("rio", (15,), {"restore": False}),
        ("rio_live", (30,), {"restore": True}),
    ]


def test_run_cli_video_requires_device(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "cast_video", _record_async(calls, "cast"))
    cli.run_cli(["movie.mp4"])
    assert calls == []
    assert "No device selected" in capsys.readouterr().out

    config.set_current_device(dict(TV))
    cli.run_cli(["movie.mp4", "-d", "5"])
    assert calls == [("cast", ("movie.mp4", 5), {"restore": True})]


# --- status / device listing ---------------------------------------------------


def test_show_status_without_device_and_empty_cache(monkeypatch, capsys):
    monkeypatch.setattr(cli, "count_hls_cache_entries", lambda: 0)
    cli.show_status()
    out = capsys.readouterr().out
    assert "No device selected" in out
    assert "HLS conversions: 0" in out
    assert "--clear-cache" not in out


def test_show_status_with_device_and_cache_counts(isolated_config, monkeypatch, capsys):
    yt = isolated_config / "youtube"
    img = isolated_config / "images"
    yt.mkdir()
    img.mkdir()
    (yt / "abc.mp4").write_bytes(b"x")
    (yt / "abc.info.json").write_text("{}", encoding="utf-8")
    (img / "photo.mp4").write_bytes(b"x")
    (img / "rio_dancing_15.mp4").write_bytes(b"x")
    (img / "photo.tmp.123.mp4").write_bytes(b"x")  # unfinished; must be ignored
    monkeypatch.setattr(cli, "count_hls_cache_entries", lambda: 2)
    config.set_current_device({**TV, "location": "http://192.168.1.50:9197/dmr"})

    cli.show_status()
    out = capsys.readouterr().out
    assert "Current device: Living Room TV" in out
    assert "DLNA: http://192.168.1.50:9197/dmr" in out
    assert "HLS conversions: 2" in out
    assert "YouTube downloads: 1" in out
    assert "Image videos: 1" in out
    assert "RIO animations: 1" in out
    assert "--clear-cache" in out


def test_list_devices_cli_groups_castable_and_marks_selection(capsys):
    cli.list_devices_cli()
    assert "No devices discovered yet" in capsys.readouterr().out

    config.set_discovered_devices(
        [dict(TV), {"name": "Phone", "ip": "192.168.1.60", "castable": False}]
    )
    config.set_current_device(dict(TV))
    cli.list_devices_cli()
    out = capsys.readouterr().out
    assert "Cast-capable devices" in out
    assert "Living Room TV ← selected" in out
    assert "Other devices" in out
    assert "Phone" in out


def test_set_device_by_ip_reuses_discovered_device():
    config.set_discovered_devices([dict(TV)])
    assert cli.set_device_by_ip(" 192.168.1.50 ") is True
    assert config.get_current_device()["name"] == "Living Room TV"


# --- scanning ----------------------------------------------------------------


async def test_scan_devices_cli_saves_found_devices(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "discover_dlna_devices", _record_async([], "scan", [dict(TV)])
    )
    await cli.scan_devices_cli()
    assert "Found 1 TV(s)" in capsys.readouterr().out
    assert config.get_discovered_devices()[0]["ip"] == TV["ip"]

    monkeypatch.setattr(cli, "discover_dlna_devices", _record_async([], "scan", []))
    await cli.scan_devices_cli()
    assert "No TVs found" in capsys.readouterr().out


async def test_scan_all_devices_cli_reports_castable_count(monkeypatch, capsys):
    devices = [dict(TV), {"name": "Phone", "ip": "192.168.1.60", "castable": False}]
    monkeypatch.setattr(cli, "discover_all_devices", _record_async([], "scan", devices))
    await cli.scan_all_devices_cli()
    assert "Found 1 castable device(s)" in capsys.readouterr().out
    assert len(config.get_discovered_devices()) == 2


# --- image / rio casting -------------------------------------------------------


def test_cast_image_cli_validates_inputs(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "image_to_video", _record(calls, "convert", None))

    cli.cast_image_cli("photo.png", 10)
    assert "No device selected" in capsys.readouterr().out

    config.set_current_device(dict(TV))
    cli.cast_image_cli(str(tmp_path / "missing.png"), 10)
    assert "Image not found" in capsys.readouterr().out

    doc = tmp_path / "notes.txt"
    doc.write_text("x", encoding="utf-8")
    cli.cast_image_cli(str(doc), 10)
    assert "Unsupported image format: .txt" in capsys.readouterr().out

    photo = tmp_path / "photo.PNG"
    photo.write_bytes(b"x")
    cli.cast_image_cli(str(photo), 10)
    assert "Failed to convert image" in capsys.readouterr().out
    assert len(calls) == 1


def test_cast_image_cli_casts_converted_video(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "image_to_video", lambda path, duration: "/tmp/img.mp4")
    monkeypatch.setattr(cli, "cast_video", _record_async(calls, "cast"))
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"x")
    config.set_current_device(dict(TV))

    cli.cast_image_cli(str(photo), 10, restore=False)
    assert calls == [("cast", ("/tmp/img.mp4",), {"duration": 12, "restore": False})]


def test_cast_rio_cli_paths(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "cast_video", _record_async(calls, "cast"))
    monkeypatch.setattr(cli, "cast_live_rio", _record_async(calls, "live"))

    cli.cast_rio_cli(15)
    cli.cast_rio_live_cli(0)
    assert capsys.readouterr().out.count("No device selected") == 2

    config.set_current_device(dict(TV))
    monkeypatch.setattr(cli, "create_dancing_rio_video", lambda duration: None)
    cli.cast_rio_cli(15)
    assert "Failed to generate" in capsys.readouterr().out

    monkeypatch.setattr(
        cli, "create_dancing_rio_video", lambda duration: "/tmp/rio.mp4"
    )
    cli.cast_rio_cli(15)
    cli.cast_rio_live_cli(0, restore=False)
    assert calls == [
        ("cast", ("/tmp/rio.mp4",), {"duration": 17, "restore": True}),
        ("live", (), {"duration": 0, "restore": False}),
    ]


# --- TV state / apps -------------------------------------------------------------


async def test_show_tv_state_cli_formats_snapshot(monkeypatch, capsys):
    await cli.show_tv_state_cli()
    assert "No device selected" in capsys.readouterr().out

    config.set_current_device({**TV, "installed_apps": {"111": "YouTube"}})
    seen = {}

    async def fake_get_tv_state(ip, av_transport=None, extra_apps=None):
        seen.update(ip=ip, extra_apps=extra_apps)
        return "SNAPSHOT"

    monkeypatch.setattr(cli, "get_av_transport", _record_async([], "av", (None, None)))
    monkeypatch.setattr(cli, "get_tv_state", fake_get_tv_state)
    monkeypatch.setattr(cli, "format_tv_state", lambda s: f"formatted {s}")

    await cli.show_tv_state_cli()
    assert "formatted SNAPSHOT" in capsys.readouterr().out
    assert seen == {"ip": TV["ip"], "extra_apps": {"111": "YouTube"}}


async def test_list_tv_apps_cli_merges_websocket_and_rest(monkeypatch, capsys):
    await cli.list_tv_apps_cli()
    assert "No device selected" in capsys.readouterr().out

    config.set_current_device(dict(TV))
    monkeypatch.setattr(
        cli,
        "pair_and_fetch_installed_apps",
        _record_async([], "pair", ("tok-1", {"222": "Netflix"})),
    )
    monkeypatch.setattr(
        cli,
        "discover_installed_apps_via_rest",
        _record_async([], "rest", [("111", "YouTube", True, True)]),
    )

    await cli.list_tv_apps_cli()
    out = capsys.readouterr().out
    assert "2 found" in out
    assert "YouTube ← foreground" in out
    assert "Netflix" in out
    device = config.get_current_device()
    assert device["samsung_token"] == "tok-1"
    assert device["installed_apps"] == {"222": "Netflix", "111": "YouTube"}


async def test_list_tv_apps_cli_falls_back_to_rest_when_pairing_fails(
    monkeypatch, capsys
):
    config.set_current_device(dict(TV))

    async def failing_pair(ip, token=None):
        raise TimeoutError

    monkeypatch.setattr(cli, "pair_and_fetch_installed_apps", failing_pair)
    monkeypatch.setattr(
        cli, "discover_installed_apps_via_rest", _record_async([], "rest", [])
    )
    await cli.list_tv_apps_cli()
    out = capsys.readouterr().out
    assert "WebSocket app query skipped (TimeoutError)" in out
    assert "No installed apps detected" in out

    monkeypatch.setattr(
        cli,
        "discover_installed_apps_via_rest",
        _record_async([], "rest", [("111", "YouTube", True, False)]),
    )
    await cli.list_tv_apps_cli()
    out = capsys.readouterr().out
    assert "local REST API" in out
    assert "YouTube (running)" in out


# --- interactive loop ------------------------------------------------------------


def test_run_interactive_routes_menu_choices(monkeypatch):
    choices = iter(["PLAYBACK", "DEVICES", "CLEAR_CACHE", "INTERRUPT", "QUIT"])
    calls = []

    def fake_main_menu():
        choice = next(choices)
        if choice == "INTERRUPT":
            raise KeyboardInterrupt
        return choice

    monkeypatch.setattr(cli, "main_menu", fake_main_menu)
    monkeypatch.setattr(cli, "playback_menu_loop", _record(calls, "playback"))
    monkeypatch.setattr(cli, "device_menu", _record_async(calls, "devices"))
    monkeypatch.setattr(cli, "clear_cache", _record(calls, "clear"))

    cli.run_interactive()
    assert [c[0] for c in calls] == ["playback", "devices", "clear"]
