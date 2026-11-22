import json
import os
import sys
import threading
from types import SimpleNamespace

from tv_cast import youtube


def test_sanitize_video_id_prevents_path_traversal():
    cleaned = youtube._sanitize_video_id("../../etc/passwd", "https://youtu.be/abc")
    assert "/" not in cleaned
    assert ".." not in cleaned
    assert cleaned

    fallback = youtube._sanitize_video_id("///", "https://youtu.be/abc")
    assert len(fallback) == 16
    assert fallback.isalnum()


def test_download_youtube_sanitizes_id_and_escapes_percent_in_outtmpl(tmp_path, monkeypatch):
    yt_dir = tmp_path / "cache%100"
    monkeypatch.setattr(youtube, "YOUTUBE_CACHE_DIR", str(yt_dir))

    captured_opts = []

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts
            captured_opts.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            return {"id": "../evil/id", "title": "Safe Title", "duration": 65}

        def download(self, urls):
            out = self.opts["outtmpl"].replace("%%", "%")
            with open(out, "wb") as f:
                f.write(b"mp4-bytes")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))

    result = youtube.download_youtube("https://youtu.be/abc123")
    assert result is not None
    assert os.path.commonpath([str(yt_dir), os.path.abspath(result)]) == str(yt_dir)
    assert os.path.exists(result)
    assert "%%100" in captured_opts[1]["outtmpl"]


def test_download_youtube_cancels_cleanly_via_cancel_event(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_CACHE_DIR", str(tmp_path))

    cancel_event = threading.Event()

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            return {"id": "abc123", "title": "Long Video", "duration": 600}

        def download(self, urls):
            cancel_event.set()
            for hook in self.opts.get("progress_hooks", []):
                hook({"status": "downloading", "_percent_str": "1%", "_speed_str": "1MiB/s"})
            raise AssertionError("progress hook should have aborted download")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))

    assert youtube.download_youtube("https://youtu.be/abc123", cancel_event=cancel_event) is None


def test_find_cached_youtube_videos_uses_removesuffix(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(youtube, "is_cached", lambda _: (True, "/tmp/hls"))

    video_id = "clip.mp4_extra"
    mp4 = tmp_path / f"{video_id}.mp4"
    mp4.write_bytes(b"video")
    info = tmp_path / f"{video_id}.info.json"
    info.write_text(json.dumps({"id": video_id, "title": "My Clip"}), encoding="utf-8")

    results = youtube.find_cached_youtube_videos()
    assert len(results) == 1
    assert results[0]["id"] == video_id
    assert results[0]["title"] == "My Clip"
    assert results[0]["hls_cached"] is True
