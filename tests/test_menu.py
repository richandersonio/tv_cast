"""Tests for the interactive menus, driven by scripted keyboard input."""

import builtins

import pytest

from tv_cast import config, conversion, menu

TV = {"name": "Living Room TV", "ip": "192.168.1.50", "castable": True}


@pytest.fixture(autouse=True)
def isolated_menu(tmp_path, monkeypatch):
    """Run menus in an empty temp dir with isolated config and caches."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path / ".cfg"))
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / ".cfg" / "config.json"))
    monkeypatch.setattr(menu, "YOUTUBE_CACHE_DIR", str(tmp_path / ".yt"))
    monkeypatch.setattr(conversion, "IMAGE_CACHE_DIR", str(tmp_path / ".img"))
    monkeypatch.setattr(menu, "count_hls_cache_entries", lambda: 0)
    monkeypatch.setattr(menu, "find_cached_youtube_videos", list)
    monkeypatch.setattr(menu, "is_cached", lambda _: (False, ""))
    old_device = config.get_current_device()
    old_devices = config.get_discovered_devices()
    config.set_current_device(None)
    config.set_discovered_devices([])
    yield tmp_path
    config.set_current_device(old_device)
    config.set_discovered_devices(old_devices)


def script_input(monkeypatch, *answers):
    """Feed answers to input(); raise EOFError once they run out."""
    it = iter(answers)

    def fake_input(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(builtins, "input", fake_input)


def recorder(calls, name, result=None):
    def fake(*args, **kwargs):
        calls.append((name, args, kwargs))
        return result

    return fake


def async_recorder(calls, name, result=None):
    async def fake(*args, **kwargs):
        calls.append((name, args, kwargs))
        return result

    return fake


# --- main menu -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        (["1"], "PLAYBACK"),
        (["2"], "DEVICES"),
        (["q"], "QUIT"),
        ([""], "QUIT"),
        (["3", "zzz", "1"], "PLAYBACK"),  # 3 is hidden when the cache is empty
    ],
)
def test_main_menu_choices(monkeypatch, answers, expected):
    script_input(monkeypatch, *answers)
    assert menu.main_menu() == expected


def test_main_menu_offers_clear_cache_when_cached(isolated_menu, monkeypatch, capsys):
    (isolated_menu / ".yt").mkdir()
    (isolated_menu / ".yt" / "a.mp4").write_bytes(b"x")
    (isolated_menu / ".img").mkdir()
    (isolated_menu / ".img" / "b.mp4").write_bytes(b"x")
    monkeypatch.setattr(menu, "count_hls_cache_entries", lambda: 1)
    config.set_current_device(dict(TV))
    script_input(monkeypatch, "3")

    assert menu.main_menu() == "CLEAR_CACHE"
    out = capsys.readouterr().out
    assert "Clear Cache (3 cached)" in out
    assert "Device: Living Room TV" in out


# --- submenus ------------------------------------------------------------------


def test_youtube_submenu(monkeypatch, capsys):
    script_input(monkeypatch, "1")
    assert menu.select_youtube_submenu() == config.SAMPLE_YOUTUBE_URL

    script_input(monkeypatch, "x", "p", "not a url", "p", "https://youtu.be/abc123")
    assert menu.select_youtube_submenu() == "https://youtu.be/abc123"
    out = capsys.readouterr().out
    assert "Please enter 1, p, or b" in out
    assert "Invalid YouTube URL" in out

    script_input(monkeypatch, "b")
    assert menu.select_youtube_submenu() is None


def test_image_submenu_without_local_images(isolated_menu, monkeypatch, capsys):
    doc = isolated_menu / "notes.txt"
    doc.write_text("x", encoding="utf-8")
    script_input(monkeypatch, "7", "p", "missing.png", "p", str(doc), "b")
    assert menu.select_image_submenu() is None
    out = capsys.readouterr().out
    assert "no images in current directory" in out
    assert "Please enter p or b" in out
    assert "File not found" in out
    assert "Not a supported image format" in out

    photo = isolated_menu / "elsewhere.png"
    photo.write_bytes(b"x")
    script_input(monkeypatch, "p", str(photo))
    assert menu.select_image_submenu() == f"IMAGE:{photo}"


def test_image_submenu_selects_listed_image(isolated_menu, monkeypatch, capsys):
    (isolated_menu / "a.jpg").write_bytes(b"x")
    (isolated_menu / "b.png").write_bytes(b"xx")
    script_input(monkeypatch, "abc", "9", "2")
    assert menu.select_image_submenu() == "IMAGE:b.png"
    out = capsys.readouterr().out
    assert "Please enter a valid option" in out
    assert "Please enter 1-2, p, or b" in out


# --- playback selection -----------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("b", "BACK"), ("q", "BACK"), ("0", "STOP"), ("r", "RIO"), ("l", "RIO_LIVE")],
)
def test_interactive_select_menu_keys(monkeypatch, answer, expected):
    script_input(monkeypatch, answer)
    assert menu.interactive_select() == expected


def test_interactive_select_lists_media_and_picks_by_number(
    isolated_menu, monkeypatch, capsys
):
    (isolated_menu / "movie.mp4").write_bytes(b"x")
    (isolated_menu / "photo.png").write_bytes(b"x")
    monkeypatch.setattr(
        menu,
        "find_cached_youtube_videos",
        lambda: [
            {"title": "T" * 60, "path": "/yt/a.mp4", "size": 10, "hls_cached": True}
        ],
    )
    config.set_current_device(dict(TV))

    script_input(monkeypatch, "1")
    assert menu.interactive_select() == "movie.mp4"
    out = capsys.readouterr().out
    assert "Local Videos" in out
    assert "Cached YouTube Videos" in out
    assert "T" * 45 + "..." in out
    assert "Device: Living Room TV" in out

    script_input(monkeypatch, "2")
    assert menu.interactive_select() == "/yt/a.mp4"

    script_input(monkeypatch, "nope", "42", "3")
    assert menu.interactive_select() == "IMAGE:photo.png"
    out = capsys.readouterr().out
    assert "Please enter 1-3 or a menu option" in out


def test_interactive_select_submenus_and_empty_listing(monkeypatch, capsys):
    monkeypatch.setattr(menu, "select_youtube_submenu", lambda: None)
    monkeypatch.setattr(menu, "select_image_submenu", lambda: "IMAGE:x.png")
    script_input(monkeypatch, "5", "y", "i")
    assert menu.interactive_select() == "IMAGE:x.png"
    out = capsys.readouterr().out
    assert "(no media found)" in out
    assert "Please enter a valid option" in out

    monkeypatch.setattr(menu, "select_youtube_submenu", lambda: "https://youtu.be/x")
    script_input(monkeypatch, "y")
    assert menu.interactive_select() == "https://youtu.be/x"


# --- playback loop ----------------------------------------------------------------


def test_playback_menu_loop_requires_device(monkeypatch, capsys):
    selections = iter(["STOP", "movie.mp4", "BACK"])
    monkeypatch.setattr(menu, "interactive_select", lambda: next(selections))
    menu.playback_menu_loop()
    out = capsys.readouterr().out
    assert "❌ No device selected\n" in out
    assert "Please select a device first" in out


def test_playback_menu_loop_dispatches_each_selection(monkeypatch, capsys):
    config.set_current_device(dict(TV))
    calls = []
    selections = iter(
        ["STOP", "RIO", "RIO", "RIO_LIVE", "IMAGE:a.png", "IMAGE:b.png", "v.mp4"]
    )

    def next_selection():
        try:
            return next(selections)
        except StopIteration:
            return "BACK"

    rio_results = iter([None, "/tmp/rio.mp4"])
    image_results = iter([None, "/tmp/img.mp4"])
    monkeypatch.setattr(menu, "interactive_select", next_selection)
    monkeypatch.setattr(menu, "stop_playback", async_recorder(calls, "stop"))
    monkeypatch.setattr(menu, "cast_video", async_recorder(calls, "cast"))
    monkeypatch.setattr(menu, "cast_live_rio", async_recorder(calls, "live"))
    monkeypatch.setattr(
        menu, "create_dancing_rio_video", lambda duration: next(rio_results)
    )
    monkeypatch.setattr(
        menu, "image_to_video", lambda path, duration: next(image_results)
    )

    menu.playback_menu_loop()
    out = capsys.readouterr().out
    assert "Failed to generate dancing RIO animation" in out
    assert "Failed to convert image" in out
    assert calls == [
        ("stop", (), {}),
        ("cast", ("/tmp/rio.mp4",), {"duration": 17}),
        ("live", (), {"duration": 0}),
        ("cast", ("/tmp/img.mp4",), {"duration": 12}),
        ("cast", ("v.mp4",), {}),
    ]


def test_playback_menu_loop_survives_keyboard_interrupt(monkeypatch):
    selections = iter(["INTERRUPT", "BACK"])

    def next_selection():
        choice = next(selections)
        if choice == "INTERRUPT":
            raise KeyboardInterrupt
        return choice

    monkeypatch.setattr(menu, "interactive_select", next_selection)
    menu.playback_menu_loop()


# --- device menu -------------------------------------------------------------------


async def test_device_menu_quick_scan_selects_tv(monkeypatch, capsys):
    other = {"name": "Bedroom TV", "ip": "192.168.1.51", "castable": True}
    monkeypatch.setattr(
        menu, "discover_dlna_devices", async_recorder([], "scan", [dict(TV), other])
    )
    script_input(monkeypatch, "1", "2", "b")

    assert await menu.device_menu() == "BACK"
    assert config.get_current_device()["name"] == "Bedroom TV"
    assert "Found 2 TV(s)" in capsys.readouterr().out


async def test_device_menu_quick_scan_no_results_and_bad_selection(monkeypatch, capsys):
    results = iter([[], [dict(TV)]])

    async def fake_scan(timeout):
        return next(results)

    monkeypatch.setattr(menu, "discover_dlna_devices", fake_scan)
    script_input(monkeypatch, "1", "1", "not-a-number", "zzz", "q")

    assert await menu.device_menu() == "BACK"
    out = capsys.readouterr().out
    assert "No TVs found" in out
    assert "Please enter a valid option" in out
    assert config.get_current_device() is None


async def test_device_menu_deep_scan_and_forget(monkeypatch, capsys):
    phone = {"name": "Phone", "ip": "192.168.1.60", "castable": False}
    monkeypatch.setattr(
        menu, "discover_all_devices", async_recorder([], "deep", [phone, dict(TV)])
    )
    script_input(monkeypatch, "2", "1", "3")

    assert await menu.device_menu() == "BACK"  # EOF after scripted answers
    out = capsys.readouterr().out
    assert "Cast-capable devices" in out
    assert "Device forgotten" in out
    assert config.get_current_device() is None


async def test_device_menu_deep_scan_cancel_keeps_current(monkeypatch, capsys):
    config.set_current_device(dict(TV))
    other = {"name": "Bedroom TV", "ip": "192.168.1.51", "castable": True}
    monkeypatch.setattr(
        menu, "discover_all_devices", async_recorder([], "deep", [other])
    )
    script_input(monkeypatch, "2", "0", "b")

    assert await menu.device_menu() == "BACK"
    assert config.get_current_device()["name"] == "Living Room TV"
    assert "Current: Living Room TV (192.168.1.50)" in capsys.readouterr().out
