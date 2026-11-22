import os
import shutil
import subprocess
import sys
import threading

import pytest

from tv_cast import conversion


def test_is_cached_requires_endlist_and_segments(tmp_path, monkeypatch):
    hls_cache = tmp_path / "hls"
    monkeypatch.setattr(conversion, "HLS_CACHE_DIR", str(hls_cache))

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video-content")

    cache_dir = conversion.get_cache_dir(str(video))
    os.makedirs(cache_dir, exist_ok=True)
    playlist = os.path.join(cache_dir, "playlist.m3u8")
    segment = os.path.join(cache_dir, "segment000.ts")

    # Partial playlist without #EXT-X-ENDLIST should NOT count as cached.
    with open(playlist, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n#EXTINF:4.0,\nsegment000.ts\n")
    with open(segment, "wb") as f:
        f.write(b"ts")

    cached, _ = conversion.is_cached(str(video))
    assert not cached

    # Complete playlist with #EXT-X-ENDLIST and segment counts as cached.
    with open(playlist, "a", encoding="utf-8") as f:
        f.write("#EXT-X-ENDLIST\n")

    cached, returned_dir = conversion.is_cached(str(video))
    assert cached
    assert returned_dir == cache_dir


def test_missing_ffmpeg_and_ffprobe_handled_gracefully(tmp_path, monkeypatch):
    monkeypatch.setattr(conversion, "HLS_CACHE_DIR", str(tmp_path / "hls"))
    monkeypatch.setattr(conversion, "IMAGE_CACHE_DIR", str(tmp_path / "images"))

    def raise_missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg not installed")

    monkeypatch.setattr(conversion.subprocess, "run", raise_missing)
    monkeypatch.setattr(conversion.subprocess, "Popen", raise_missing)

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    img = tmp_path / "photo.png"
    img.write_bytes(b"png")

    assert conversion.check_video_codec(str(video)) == ("", 0.0)
    assert conversion.convert_to_hls(str(video)) is None
    assert conversion.image_to_video(str(img), duration=5) is None


def test_convert_to_hls_drains_large_stderr_without_deadlock(tmp_path, monkeypatch):
    monkeypatch.setattr(conversion, "HLS_CACHE_DIR", str(tmp_path / "hls"))
    monkeypatch.setattr(conversion, "check_video_codec", lambda _: ("h264", 4.0))

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")

    real_popen = subprocess.Popen

    def fake_popen(cmd, **kwargs):
        # Last arg in ffmpeg command is the temporary playlist path.
        playlist_path = cmd[-1]
        tmp_dir = os.path.dirname(playlist_path)
        script = (
            "import os, sys\n"
            "for i in range(4000):\n"
            "    sys.stderr.write('x' * 80 + '\\n')\n"
            "sys.stderr.flush()\n"
            "sys.stdout.write('out_time=00:00:02.000000\\n')\n"
            "sys.stdout.write('out_time=00:00:04.000000\\n')\n"
            "sys.stdout.flush()\n"
            f"open({playlist_path!r}, 'w').write('#EXTM3U\\n#EXTINF:4.0,\\nsegment000.ts\\n#EXT-X-ENDLIST\\n')\n"
            f"open(os.path.join({tmp_dir!r}, 'segment000.ts'), 'wb').write(b'ts')\n"
        )
        return real_popen([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(conversion.subprocess, "Popen", fake_popen)

    playlist_path = conversion.convert_to_hls(str(video))
    assert playlist_path is not None
    assert os.path.exists(playlist_path)
    assert conversion.is_cached(str(video))[0]


def test_image_to_video_rejects_non_positive_duration(tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"png")
    assert conversion.image_to_video(str(img), duration=0) is None
    assert conversion.image_to_video(str(img), duration=-3) is None


def test_count_hls_cache_entries_ignores_temp_dirs(tmp_path, monkeypatch):
    hls = tmp_path / "hls"
    hls.mkdir()
    (hls / "video_123_456").mkdir()
    (hls / "video_123_456.tmp.9999").mkdir()

    monkeypatch.setattr(conversion, "HLS_CACHE_DIR", str(hls))
    assert conversion.count_hls_cache_entries() == 1


def test_convert_to_hls_cancels_cleanly_via_cancel_event(tmp_path, monkeypatch):
    hls = tmp_path / "hls"
    monkeypatch.setattr(conversion, "HLS_CACHE_DIR", str(hls))
    monkeypatch.setattr(conversion, "check_video_codec", lambda _: ("h264", 10.0))

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")

    real_popen = subprocess.Popen
    started = threading.Event()

    def fake_popen(cmd, **kwargs):
        started.set()
        script = "import time\ntime.sleep(30)\n"
        return real_popen([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(conversion.subprocess, "Popen", fake_popen)

    cancel_event = threading.Event()

    def trigger_cancel():
        started.wait(timeout=2.0)
        cancel_event.set()

    canceller = threading.Thread(target=trigger_cancel, daemon=True)
    canceller.start()

    result = conversion.convert_to_hls(str(video), cancel_event=cancel_event)
    canceller.join(timeout=2.0)

    assert result is None
    assert conversion.count_hls_cache_entries() == 0
    assert list(hls.iterdir()) == []


def test_clear_cache_removes_all_cache_dirs(tmp_path, monkeypatch):
    hls = tmp_path / "hls"
    yt = tmp_path / "yt"
    img = tmp_path / "img"
    for d in (hls, yt, img):
        d.mkdir()
    (hls / "entry").mkdir()
    (yt / "abc.mp4").write_bytes(b"mp4")
    (img / "photo.mp4").write_bytes(b"mp4")

    monkeypatch.setattr(conversion, "HLS_CACHE_DIR", str(hls))
    monkeypatch.setattr(conversion, "YOUTUBE_CACHE_DIR", str(yt))
    monkeypatch.setattr(conversion, "IMAGE_CACHE_DIR", str(img))

    conversion.clear_cache()

    assert not hls.exists()
    assert not yt.exists()
    assert not img.exists()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe required for integration test",
)
def test_real_ffmpeg_hls_and_image_integration(tmp_path, monkeypatch):
    monkeypatch.setattr(conversion, "HLS_CACHE_DIR", str(tmp_path / "hls"))
    monkeypatch.setattr(conversion, "IMAGE_CACHE_DIR", str(tmp_path / "images"))

    # 1. Generate synthetic 1-second H.264 MP4 and test fast-remux HLS conversion + caching.
    h264_mp4 = tmp_path / "sample_h264.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", "1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(h264_mp4),
        ],
        check=True,
        capture_output=True,
    )

    codec, duration = conversion.check_video_codec(str(h264_mp4))
    assert codec == "h264"
    assert duration > 0.5

    playlist = conversion.convert_to_hls(str(h264_mp4))
    assert playlist is not None
    assert os.path.exists(playlist)
    with open(playlist, encoding="utf-8") as f:
        assert "#EXT-X-ENDLIST" in f.read()

    # Second call should hit cache cleanly.
    cached, _ = conversion.is_cached(str(h264_mp4))
    assert cached
    assert conversion.convert_to_hls(str(h264_mp4)) == playlist

    # 2. Generate synthetic non-H.264 video (mpeg2video) and test transcode path.
    mpeg2_vid = tmp_path / "sample_mpeg2.ts"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=green:s=320x240:d=1",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", "1",
            "-c:v", "mpeg2video",
            "-c:a", "mp2",
            str(mpeg2_vid),
        ],
        check=True,
        capture_output=True,
    )
    transcoded_playlist = conversion.convert_to_hls(str(mpeg2_vid))
    assert transcoded_playlist is not None
    assert os.path.exists(transcoded_playlist)

    # 3. Generate synthetic PNG and test image_to_video.
    png = tmp_path / "sample.png"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=320x240:d=1",
            "-frames:v", "1",
            str(png),
        ],
        check=True,
        capture_output=True,
    )
    img_video = conversion.image_to_video(str(png), duration=1)
    assert img_video is not None
    assert os.path.exists(img_video)
    assert conversion.image_to_video(str(png), duration=1) == img_video
