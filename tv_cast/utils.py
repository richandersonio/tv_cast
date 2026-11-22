"""Utility functions."""

import ipaddress
import math
import platform
import re
import signal
import socket
import subprocess
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse


def get_local_ip() -> str:
    """Get local IP address."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
        except OSError:
            return '127.0.0.1'


def get_local_subnet() -> str:
    """Get the local subnet for scanning."""
    local_ip = get_local_ip()
    parts = local_ip.split('.')
    return f"{parts[0]}.{parts[1]}.{parts[2]}"


def is_valid_ip(ip: str) -> bool:
    """Return True if the string is a valid IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def format_host_for_url(host: str) -> str:
    """Format an IP or hostname for URL interpolation, bracketing IPv6 literals."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host
    return f"[{host}]" if isinstance(addr, ipaddress.IPv6Address) else host


def format_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def check_port(ip: str, port: int, timeout: float = 0.3) -> bool:
    """Check if a port is open on a host (IPv4 or IPv6)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def ping_host(ip: str, timeout: float = 0.3) -> bool:
    """Check if a host is reachable via ping."""
    system = platform.system().lower()
    if system == "windows":
        cmd = ["ping", "-n", "1", "-w", str(max(1, int(timeout * 1000))), ip]
    elif system == "darwin":
        # macOS ping -W expects milliseconds
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout * 1000))), ip]
    else:
        # Linux iputils ping -W expects whole seconds
        cmd = ["ping", "-c", "1", "-W", str(max(1, math.ceil(timeout))), ip]

    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=timeout + 1.0, check=False)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def get_hostname(ip: str) -> str | None:
    """Try to resolve hostname for an IP."""
    try:
        hostname, _, _ = socket.gethostbyaddr(ip)
        return hostname
    except OSError:
        return None


_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")


def extract_youtube_video_id(url: str) -> str | None:
    """Extract the YouTube video ID from a valid YouTube URL, or return None."""
    if not is_youtube_url(url):
        return None

    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""

    candidate: str | None = None
    if host == "youtu.be":
        candidate = path.strip("/").split("/")[0]
    elif path == "/watch":
        values = parse_qs(parsed.query).get("v")
        candidate = values[0] if values else None
    elif path.startswith(("/shorts/", "/live/")):
        parts = [p for p in path.split("/") if p]
        candidate = parts[1] if len(parts) >= 2 else None

    if candidate and _YT_ID_RE.match(candidate):
        return candidate
    return None


def is_youtube_url(url: str) -> bool:
    """Check if URL is a valid YouTube video URL."""
    if not url:
        return False

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False

    if parsed.scheme.lower() not in ("http", "https"):
        return False

    host = (parsed.hostname or "").lower()
    path = parsed.path or ""

    if host == "youtu.be":
        return bool(path.strip("/"))

    if host == "youtube.com" or host.endswith(".youtube.com"):
        return path == "/watch" or path.startswith(("/shorts/", "/live/"))

    return False


def prompt_input(prompt: str = "") -> str:
    """Read a line from stdin so Ctrl+C raises KeyboardInterrupt, even inside asyncio.run().

    asyncio's runner replaces the SIGINT handler with one that only cancels the main
    task, which a blocking input() call never observes. Temporarily restoring Python's
    default handler makes Ctrl+C interrupt the prompt immediately.
    """
    if threading.current_thread() is not threading.main_thread():
        return input(prompt)

    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        return input(prompt)
    finally:
        signal.signal(signal.SIGINT, previous)


def get_device_icon(device: dict[str, Any]) -> str:
    """Get an icon for a device based on its type."""
    device_type = device.get('type', 'unknown')
    icons = {
        'dlna': '📺',
        'airplay': '🍎',
        'chromecast': '📺',
        'spotify': '🎵',
        'homekit': '🏠',
        'apple': '🍎',
        'apple-mobile': '📱',
        'apple-tv': '📺',
        'homepod': '🔊',
        'mac': '💻',
        'airport': '📡',
        'android': '🤖',
        'phone': '📱',
        'samsung': '📱',
        'google': '🔍',
        'amazon': '📦',
        'roku': '📺',
        'sony': '📺',
        'lg': '📺',
        'tesla': '⚡',
        'raspberry-pi': '🍓',
        'file-share': '📁',
        'printer': '🖨️',
        'computer': '💻',
        'windows': '🪟',
        'linux': '🐧',
        'bsd': '😈',
        'nas': '💾',
        'synology': '💾',
        'plex': '🎬',
        'media': '🎬',
        'camera': '📷',
        'router': '📡',
        'iot-hub': '🔌',
        'web': '🌐',
        'unknown': '❓',
    }
    return icons.get(device_type, '📱')
