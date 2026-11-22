"""Video conversion to HLS format."""

import json
import os
import shutil
import subprocess
import threading
from collections import deque

from .config import (
    FFMPEG_THREADS,
    HLS_CACHE_DIR,
    IMAGE_CACHE_DIR,
    YOUTUBE_CACHE_DIR,
)

# Filename prefix of cached dancing RIO animations stored in IMAGE_CACHE_DIR.
RIO_CACHE_PREFIX = "rio_dancing_"


def get_cache_dir(video_path: str) -> str:
    """Get cache directory for a video file."""
    video_name = os.path.basename(video_path)
    video_mtime = os.path.getmtime(video_path)
    video_size = os.path.getsize(video_path)

    cache_key = f"{video_name}_{video_size}_{int(video_mtime)}"
    cache_key = "".join(
        c if c.isalnum() or c in "._-" else "_" for c in cache_key)

    return os.path.join(HLS_CACHE_DIR, cache_key)


def is_cached(video_path: str) -> tuple[bool, str]:
    """Check if video is already converted. Returns (is_cached, cache_dir)."""
    cache_dir = get_cache_dir(video_path)
    playlist_path = os.path.join(cache_dir, "playlist.m3u8")

    if os.path.exists(playlist_path):
        try:
            with open(playlist_path, encoding="utf-8") as f:
                playlist_content = f.read()
        except OSError:
            return False, cache_dir

        if "#EXT-X-ENDLIST" not in playlist_content:
            return False, cache_dir

        segments = [f for f in os.listdir(cache_dir) if f.endswith('.ts')]
        if segments:
            return True, cache_dir

    return False, cache_dir


def check_video_codec(input_file: str) -> tuple[str, float]:
    """Check if video is H.264 and get duration."""
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name:format=duration",
        "-of", "json",
        input_file
    ]
    try:
        result = subprocess.run(
            probe_cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return "", 0.0

    try:
        data = json.loads(result.stdout)
        streams = data.get("streams") or [{}]
        codec = streams[0].get("codec_name", "")
        duration = float(data.get("format", {}).get("duration", 0))
        return codec, duration
    except (json.JSONDecodeError, KeyError, ValueError, IndexError, TypeError):
        return "", 0.0


def _drain_stream(stream, buffer: deque) -> None:
    """Continuously drain a subprocess pipe into a bounded buffer to prevent deadlocks."""
    try:
        for line in stream:
            buffer.append(line)
    except Exception:
        pass


def count_hls_cache_entries() -> int:
    """Count completed HLS cache directories, excluding stale temporary dirs."""
    if not os.path.exists(HLS_CACHE_DIR):
        return 0
    return len([name for name in os.listdir(HLS_CACHE_DIR) if ".tmp." not in name])


def _is_finished_video_file(name: str) -> bool:
    return name.endswith(".mp4") and ".tmp." not in name


def count_image_cache_entries(include_rio: bool = True) -> int:
    """Count finished image videos (and, optionally, cached RIO animations)."""
    if not os.path.exists(IMAGE_CACHE_DIR):
        return 0
    return len([
        name for name in os.listdir(IMAGE_CACHE_DIR)
        if _is_finished_video_file(name)
        and (include_rio or not name.startswith(RIO_CACHE_PREFIX))
    ])


def count_rio_cache_entries() -> int:
    """Count cached dancing RIO animations."""
    if not os.path.exists(IMAGE_CACHE_DIR):
        return 0
    return len([
        name for name in os.listdir(IMAGE_CACHE_DIR)
        if _is_finished_video_file(name) and name.startswith(RIO_CACHE_PREFIX)
    ])


def convert_to_hls(
    input_file: str,
    cancel_event: threading.Event | None = None,
) -> str | None:
    """Convert video to HLS format for Samsung TV compatibility."""
    if cancel_event is not None and cancel_event.is_set():
        print("\n❌ Conversion cancelled")
        return None

    cached, cache_dir = is_cached(input_file)

    if cached:
        print("⚡ Using cached HLS conversion")
        return os.path.join(cache_dir, "playlist.m3u8")

    codec, duration = check_video_codec(input_file)
    can_copy = codec in ("h264", "hevc", "h265")

    os.makedirs(HLS_CACHE_DIR, exist_ok=True)
    tmp_dir = f"{cache_dir}.tmp.{os.getpid()}"
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_playlist_path = os.path.join(tmp_dir, "playlist.m3u8")
    final_playlist_path = os.path.join(cache_dir, "playlist.m3u8")

    if can_copy:
        print(f"🚀 Fast remux to HLS (video is already {codec})...")
        cmd = [
            "ffmpeg", "-y",
            "-i", input_file,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "0",
            "-hls_segment_filename", os.path.join(tmp_dir, "segment%03d.ts"),
            "-progress", "pipe:1",
            tmp_playlist_path,
        ]
    else:
        thread_label = f"{FFMPEG_THREADS} threads" if FFMPEG_THREADS > 0 else "auto threads"
        print(f"🔄 Converting to HLS (using {thread_label}, ultrafast)...")
        cmd = [
            "ffmpeg", "-y",
            "-i", input_file,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-level:v", "4.1",
            "-preset", "ultrafast",
            "-crf", "23",
        ]
        if FFMPEG_THREADS > 0:
            cmd.extend(["-threads", str(FFMPEG_THREADS)])
        cmd.extend([
            "-c:a", "aac",
            "-b:a", "128k",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "0",
            "-hls_segment_filename", os.path.join(tmp_dir, "segment%03d.ts"),
            "-progress", "pipe:1",
            tmp_playlist_path,
        ])

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print("❌ ffmpeg not found. Please install ffmpeg and try again.")
        return None

    stderr_lines: deque[str] = deque(maxlen=200)
    stderr_thread = threading.Thread(
        target=_drain_stream,
        args=(process.stderr, stderr_lines),
        daemon=True,
    )
    stderr_thread.start()

    done_event = threading.Event()
    cancel_watcher: threading.Thread | None = None
    if cancel_event is not None:
        def _watch_cancel() -> None:
            while not done_event.is_set():
                if cancel_event.wait(timeout=0.05):
                    if process.poll() is None:
                        process.kill()
                    return

        cancel_watcher = threading.Thread(target=_watch_cancel, daemon=True)
        cancel_watcher.start()

    last_percent = -1

    try:
        assert process.stdout is not None
        for line in process.stdout:
            if cancel_event is not None and cancel_event.is_set():
                break

            line = line.strip()

            if line.startswith("out_time="):
                try:
                    time_str = line.split("=")[1]
                    parts = time_str.replace("-", "0").split(":")
                    if len(parts) == 3:
                        h, m, s = parts
                        current_seconds = int(h) * 3600 + int(m) * 60 + float(s)

                        if duration > 0:
                            percent = min(
                                100, int((current_seconds / duration) * 100))
                            if percent != last_percent:
                                bar_len = 30
                                filled = int(bar_len * percent / 100)
                                bar = "█" * filled + "░" * (bar_len - filled)
                                print(f"\r   [{bar}] {percent}%",
                                      end="", flush=True)
                                last_percent = percent
                except (ValueError, IndexError):
                    pass
    except KeyboardInterrupt:
        done_event.set()
        process.kill()
        process.wait()
        stderr_thread.join(timeout=1.0)
        if cancel_watcher is not None:
            cancel_watcher.join(timeout=1.0)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print("\n❌ Conversion cancelled")
        return None

    done_event.set()
    if cancel_event is not None and cancel_event.is_set():
        if process.poll() is None:
            process.kill()
        process.wait()
        stderr_thread.join(timeout=1.0)
        if cancel_watcher is not None:
            cancel_watcher.join(timeout=1.0)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print("\n❌ Conversion cancelled")
        return None

    process.wait()
    stderr_thread.join(timeout=2.0)
    if cancel_watcher is not None:
        cancel_watcher.join(timeout=1.0)

    if process.returncode != 0:
        stderr = "".join(stderr_lines)
        print(f"\n❌ Conversion failed: {stderr[-500:]}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)
    os.replace(tmp_dir, cache_dir)

    print("\n   ✅ Done! (cached for next time)")
    return final_playlist_path


def image_to_video(image_path: str, duration: int = 10) -> str | None:
    """Convert an image to a video file for streaming."""
    if duration <= 0:
        print("❌ Image duration must be greater than 0 seconds")
        return None

    image_path = os.path.abspath(image_path)
    image_name = os.path.basename(image_path)

    os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)

    image_mtime = os.path.getmtime(image_path)
    image_size = os.path.getsize(image_path)
    cache_key = f"{image_name}_{image_size}_{int(image_mtime)}_{duration}s"
    cache_key = "".join(
        c if c.isalnum() or c in "._-" else "_" for c in cache_key)
    output_path = os.path.join(IMAGE_CACHE_DIR, f"{cache_key}.mp4")

    if os.path.exists(output_path):
        print("⚡ Using cached image video")
        return output_path

    print(f"🖼️  Converting image to {duration}s video...")

    tmp_output_path = os.path.join(
        IMAGE_CACHE_DIR, f"{cache_key}.{os.getpid()}.tmp.mp4"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-c:v", "libx264",
        "-t", str(duration),
        "-pix_fmt", "yuv420p",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080",
        "-r", "30",
        "-preset", "ultrafast",
        tmp_output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print("❌ ffmpeg not found. Please install ffmpeg and try again.")
        return None

    if result.returncode != 0:
        if os.path.exists(tmp_output_path):
            os.remove(tmp_output_path)
        print(f"❌ Failed to convert image: {result.stderr}")
        return None

    os.replace(tmp_output_path, output_path)
    print(f"   ✅ Created {duration}s video")
    return output_path


def clear_cache() -> None:
    """Clear all cached data (HLS conversions, YouTube downloads, image videos)."""
    cleared = False

    if os.path.exists(HLS_CACHE_DIR):
        count = count_hls_cache_entries()
        shutil.rmtree(HLS_CACHE_DIR)
        print(f"🗑️  Cleared {count} HLS conversion(s)")
        cleared = True

    if os.path.exists(YOUTUBE_CACHE_DIR):
        count = len([f for f in os.listdir(
            YOUTUBE_CACHE_DIR) if f.endswith('.mp4')])
        shutil.rmtree(YOUTUBE_CACHE_DIR)
        if count > 0:
            print(f"🗑️  Cleared {count} YouTube download(s)")
            cleared = True

    if os.path.exists(IMAGE_CACHE_DIR):
        count = count_image_cache_entries()
        shutil.rmtree(IMAGE_CACHE_DIR)
        if count > 0:
            print(f"🗑️  Cleared {count} image video(s)")
            cleared = True

    if not cleared:
        print("📁 Cache is already empty")
