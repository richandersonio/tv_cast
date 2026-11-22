from types import SimpleNamespace

from tv_cast import utils
from tv_cast.utils import (
    extract_youtube_video_id,
    format_host_for_url,
    format_size,
    is_valid_ip,
    is_youtube_url,
    ping_host,
)


def test_format_size_uses_human_units():
    assert format_size(42) == "42 B"
    assert format_size(1536) == "1.5 KB"
    assert format_size(2 * 1024 * 1024) == "2.0 MB"
    assert format_size(3 * 1024 * 1024 * 1024) == "3.0 GB"


def test_is_youtube_url_accepts_common_video_urls():
    assert is_youtube_url("https://www.youtube.com/watch?v=vKQi3bBA1y8")
    assert is_youtube_url("https://m.youtube.com/watch?v=vKQi3bBA1y8")
    assert is_youtube_url("https://youtu.be/vKQi3bBA1y8")
    assert is_youtube_url("https://youtube.com/shorts/example")
    assert is_youtube_url("https://youtube.com/live/example")


def test_is_youtube_url_rejects_non_youtube_and_spoofed_urls():
    assert not is_youtube_url("https://example.com/watch?v=vKQi3bBA1y8")
    assert not is_youtube_url("/Users/example/video.mp4")
    assert not is_youtube_url("https://evil.example/?q=youtube.com/watch?v=abc")
    assert not is_youtube_url("https://notyoutube.com/watch?v=abc")
    assert not is_youtube_url("file:///youtube.com/watch?v=abc")
    assert not is_youtube_url("https://youtu.be/")
    assert not is_youtube_url("")


def test_extract_youtube_video_id():
    assert (
        extract_youtube_video_id("https://www.youtube.com/watch?v=TESTVIDEO01")
        == "TESTVIDEO01"
    )
    assert (
        extract_youtube_video_id("https://youtu.be/TESTVIDEO01?t=10") == "TESTVIDEO01"
    )
    assert (
        extract_youtube_video_id("https://youtube.com/shorts/TESTVIDEO01")
        == "TESTVIDEO01"
    )
    assert (
        extract_youtube_video_id("https://youtube.com/live/TESTVIDEO01")
        == "TESTVIDEO01"
    )
    assert extract_youtube_video_id("https://example.com/watch?v=TESTVIDEO01") is None
    assert extract_youtube_video_id("https://youtube.com/watch") is None


def test_is_valid_ip():
    assert is_valid_ip("192.168.1.50")
    assert is_valid_ip("::1")
    assert not is_valid_ip("not-an-ip")
    assert not is_valid_ip("192.168.1.999")
    assert not is_valid_ip("192.168.1.50/dmr")


def test_format_host_for_url_brackets_ipv6():
    assert format_host_for_url("192.168.1.50") == "192.168.1.50"
    assert format_host_for_url("::1") == "[::1]"
    assert format_host_for_url("fe80::1") == "[fe80::1]"
    assert format_host_for_url("tv.local") == "tv.local"


def test_ping_host_uses_platform_appropriate_timeout_units(monkeypatch):
    seen_cmds = []

    def fake_run(cmd, **kwargs):
        seen_cmds.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(utils.subprocess, "run", fake_run)

    monkeypatch.setattr(utils.platform, "system", lambda: "Darwin")
    assert ping_host("192.168.1.10", timeout=0.5)
    assert seen_cmds[-1] == ["ping", "-c", "1", "-W", "500", "192.168.1.10"]

    monkeypatch.setattr(utils.platform, "system", lambda: "Linux")
    assert ping_host("192.168.1.10", timeout=0.5)
    assert seen_cmds[-1] == ["ping", "-c", "1", "-W", "1", "192.168.1.10"]

    monkeypatch.setattr(utils.platform, "system", lambda: "Windows")
    assert ping_host("192.168.1.10", timeout=0.5)
    assert seen_cmds[-1] == ["ping", "-n", "1", "-w", "500", "192.168.1.10"]
