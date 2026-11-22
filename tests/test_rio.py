import os
import shutil

import pytest

from tv_cast import cli, conversion, rio


def test_create_dancing_rio_video_rejects_non_positive_duration():
    assert rio.create_dancing_rio_video(duration=0) is None
    assert rio.create_dancing_rio_video(duration=-5) is None


def test_create_dancing_rio_video_handles_missing_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(rio, "IMAGE_CACHE_DIR", str(tmp_path))

    def raise_missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg missing")

    monkeypatch.setattr(rio.subprocess, "run", raise_missing)
    assert rio.create_dancing_rio_video(duration=2) is None


def test_cli_parses_rio_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["--rio", "-d", "20"])
    assert args.rio is True
    assert args.duration == 20

    live_args = parser.parse_args(["--rio-live", "-d", "15"])
    assert live_args.rio_live is True
    assert live_args.duration == 15


def test_build_dancing_rio_live_hls_cmd_uses_realtime_rolling_flags(tmp_path):
    cmd = rio.build_dancing_rio_live_hls_cmd(str(tmp_path / "assets"), str(tmp_path / "hls"))
    assert "-re" in cmd
    assert "hls" in cmd
    assert "delete_segments+independent_segments+temp_file" in cmd
    assert cmd[-1].endswith("playlist.m3u8")


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe required for dancing RIO integration test",
)
def test_create_dancing_rio_video_integration(tmp_path, monkeypatch):
    monkeypatch.setattr(rio, "IMAGE_CACHE_DIR", str(tmp_path))

    video_path = rio.create_dancing_rio_video(duration=2)
    assert video_path is not None
    assert os.path.exists(video_path)

    codec, duration = conversion.check_video_codec(video_path)
    assert codec == "h264"
    assert duration >= 1.5

    # Second call should reuse cached MP4
    assert rio.create_dancing_rio_video(duration=2) == video_path
