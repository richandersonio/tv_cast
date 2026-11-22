"""YouTube video downloading."""

import hashlib
import json
import os
import re
import threading
from typing import Any

from .config import YOUTUBE_CACHE_DIR
from .conversion import is_cached


class _DownloadCancelled(Exception):
    """Internal sentinel raised when a download is cancelled cooperatively."""


def _sanitize_video_id(video_id: Any, fallback_url: str) -> str:
    """Sanitize a video identifier so it is safe as a single filename component."""
    raw = str(video_id) if video_id is not None else ""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", raw).strip("_")
    if not cleaned:
        cleaned = hashlib.sha256(fallback_url.encode("utf-8")).hexdigest()[:16]
    return cleaned


def download_youtube(
    url: str,
    cancel_event: threading.Event | None = None,
) -> str | None:
    """Download YouTube video and return local path."""
    import yt_dlp

    if cancel_event is not None and cancel_event.is_set():
        print("\n❌ Download cancelled")
        return None

    os.makedirs(YOUTUBE_CACHE_DIR, exist_ok=True)

    print("📺 Fetching YouTube video info...")

    base_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(base_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if not isinstance(info, dict):
                print("❌ Failed to get video info: empty response")
                return None

            video_id = _sanitize_video_id(info.get("id"), url)
            title = info.get("title", "Unknown")
            duration = info.get("duration", 0)

            print(f"   Title: {title}")
            if duration:
                mins, secs = divmod(duration, 60)
                print(f"   Duration: {int(mins)}:{int(secs):02d}")

            info_path = os.path.join(YOUTUBE_CACHE_DIR, f"{video_id}.info.json")
            with open(info_path, "w", encoding="utf-8") as f:
                json.dump({"id": video_id, "title": title, "duration": duration}, f)

        except Exception as e:
            print(f"❌ Failed to get video info: {e}")
            return None

    output_path = os.path.join(YOUTUBE_CACHE_DIR, f"{video_id}.mp4")
    if os.path.exists(output_path):
        print("⚡ Using cached download")
        return output_path

    if cancel_event is not None and cancel_event.is_set():
        print("\n❌ Download cancelled")
        return None

    print("⬇️  Downloading (best quality up to 1080p)...")

    def progress_hook(d: dict[str, Any]) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise _DownloadCancelled
        if d["status"] == "downloading":
            percent = d.get("_percent_str", "?%").strip()
            speed = d.get("_speed_str", "?").strip()
            print(f"\r   {percent} at {speed}    ", end="", flush=True)
        elif d["status"] == "finished":
            print("\r   ✅ Download complete!          ")

    # Escape literal '%' characters so yt-dlp does not interpret them as template fields.
    safe_outtmpl = output_path.replace("%", "%%")

    ydl_opts = {
        **base_opts,
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "outtmpl": safe_outtmpl,
        "progress_hooks": [progress_hook],
        "merge_output_format": "mp4",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return output_path
    except (_DownloadCancelled, KeyboardInterrupt):
        print("\n❌ Download cancelled")
        return None
    except Exception as e:
        print(f"\n❌ Download failed: {e}")
        return None


def find_cached_youtube_videos() -> list[dict[str, Any]]:
    """Find all cached YouTube videos with their titles."""
    if not os.path.exists(YOUTUBE_CACHE_DIR):
        return []

    videos = []
    for file in os.listdir(YOUTUBE_CACHE_DIR):
        if file.endswith(".mp4"):
            file_path = os.path.join(YOUTUBE_CACHE_DIR, file)
            size = os.path.getsize(file_path)
            video_id = file.removesuffix(".mp4")

            title = None
            info_file = os.path.join(YOUTUBE_CACHE_DIR, f"{video_id}.info.json")
            if os.path.exists(info_file):
                try:
                    with open(info_file, encoding="utf-8") as f:
                        info = json.load(f)
                        title = info.get("title", video_id)
                except (json.JSONDecodeError, OSError):
                    pass

            if not title:
                title = video_id

            hls_cached, _ = is_cached(file_path)

            videos.append(
                {
                    "id": video_id,
                    "title": title,
                    "path": file_path,
                    "size": size,
                    "hls_cached": hls_cached,
                }
            )

    videos.sort(key=lambda x: x["title"].lower())
    return videos
