import builtins
import signal

import pytest

from tv_cast import __main__ as main_mod
from tv_cast import cli, config, menu


def test_find_video_and_image_files_respect_directory_argument(tmp_path, monkeypatch):
    monkeypatch.setattr(menu, "is_cached", lambda _: (False, "/tmp/cache"))

    (tmp_path / "movie.mp4").write_bytes(b"12345")
    (tmp_path / "photo.png").write_bytes(b"6789")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")

    videos = menu.find_video_files(str(tmp_path))
    images = menu.find_image_files(str(tmp_path))

    assert videos == [("movie.mp4", 5, False)]
    assert images == [("photo.png", 4)]


@pytest.mark.asyncio
async def test_menus_exit_cleanly_on_eof(monkeypatch):
    def raise_eof(_prompt=""):
        raise EOFError

    monkeypatch.setattr(builtins, "input", raise_eof)

    assert menu.main_menu() == "QUIT"
    assert menu.interactive_select() == "BACK"
    assert menu.select_youtube_submenu() is None
    assert menu.select_image_submenu() is None
    assert await menu.device_menu() == "BACK"


def test_cli_validates_device_ip_and_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "config.json"))
    old = config.get_current_device()
    config.set_current_device(None)

    try:
        assert cli.set_device_by_ip("not-an-ip") is False
        assert config.get_current_device() is None

        assert cli.set_device_by_ip("192.168.1.99") is True
        assert config.get_current_device()["ip"] == "192.168.1.99"

        parser = cli.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["video.mp4", "-d", "-5"])
        args = parser.parse_args(["video.mp4", "-d", "15"])
        assert args.duration == 15
    finally:
        config.set_current_device(old)


def test_main_preserves_default_sigint_behavior(monkeypatch):
    registered_signals = []

    def fake_signal(sig, handler):
        registered_signals.append((sig, handler))

    monkeypatch.setattr(main_mod.signal, "signal", fake_signal)
    monkeypatch.setattr(main_mod.atexit, "register", lambda fn: None)
    monkeypatch.setattr(main_mod, "run_cli", lambda: None)

    main_mod.main()

    # SIGINT must NOT be overridden with sys.exit; only SIGTERM is registered.
    sigs = [s for s, _ in registered_signals]
    assert signal.SIGINT not in sigs
    assert signal.SIGTERM in sigs
