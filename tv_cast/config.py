"""Configuration management and constants."""

import contextlib
import json
import os
import tempfile
from typing import Any

# Settings
HTTP_PORT = 8765
CONFIG_DIR = os.path.expanduser("~/.tv_cast")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
HLS_CACHE_DIR = os.path.join(CONFIG_DIR, "hls_cache")
YOUTUBE_CACHE_DIR = os.path.join(CONFIG_DIR, "youtube")
IMAGE_CACHE_DIR = os.path.join(CONFIG_DIR, "image_videos")
FFMPEG_THREADS = 0  # 0 = let ffmpeg/libx264 auto-detect optimal thread count

# Sample content
SAMPLE_YOUTUBE_URL = "https://youtube.com/watch?v=yvsoeyqCIU8"
SAMPLE_YOUTUBE_TITLE = "Sample: Mo Holiday - Nov 27th 2017"

# App info
APP_VERSION = "0.1.0"
APP_DATE = "Dec 27th, 2025"

# Global state (mutable)
_state = {
    "current_device": None,
    "discovered_devices": [],
}


def get_current_device() -> dict[str, Any] | None:
    """Get current selected device."""
    return _state["current_device"]


_PRESERVED_DEVICE_KEYS = ("samsung_token", "installed_apps")


def set_current_device(device: dict[str, Any] | None) -> None:
    """Set current selected device, preserving pairing metadata for the same IP."""
    prev = _state["current_device"]
    if (
        isinstance(device, dict)
        and isinstance(prev, dict)
        and device.get("ip")
        and device.get("ip") == prev.get("ip")
    ):
        for key in _PRESERVED_DEVICE_KEYS:
            if key in prev and key not in device:
                device[key] = prev[key]
    _state["current_device"] = device


def get_discovered_devices() -> list[dict[str, Any]]:
    """Get list of discovered devices."""
    return _state["discovered_devices"]


def set_discovered_devices(devices: list[dict[str, Any]]) -> None:
    """Set list of discovered devices."""
    _state["discovered_devices"] = devices


def make_json_serializable(obj: Any) -> Any:
    """Convert an object to be JSON serializable."""
    if obj is None:
        return None
    elif isinstance(obj, (str, int, float, bool)):
        return obj
    elif isinstance(obj, bytes):
        return obj.decode("utf-8", errors="ignore")
    elif isinstance(obj, dict):
        return {str(k): make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [make_json_serializable(item) for item in obj]
    elif hasattr(obj, "__dict__"):
        return make_json_serializable(obj.__dict__)
    else:
        try:
            return str(obj)
        except Exception:
            return None


def load_config() -> None:
    """Load saved configuration."""
    os.makedirs(CONFIG_DIR, exist_ok=True)

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                config = json.load(f)
                _state["current_device"] = config.get("device")
                _state["discovered_devices"] = config.get("discovered_devices", [])
        except (json.JSONDecodeError, OSError):
            pass


def save_config() -> None:
    """Save current configuration with owner-only permissions."""
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass

    config = {
        "device": make_json_serializable(_state["current_device"]),
        "discovered_devices": make_json_serializable(_state["discovered_devices"]),
    }
    # Write to a temp file and atomically replace, so an interrupted save never
    # leaves a truncated config (which would lose the TV pairing token).
    fd, tmp_path = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=CONFIG_DIR)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, CONFIG_FILE)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
        raise
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


def save_discovered_devices(devices: list[dict[str, Any]]) -> None:
    """Save discovered devices to config."""
    existing_ips = {d.get("ip") for d in _state["discovered_devices"] if d.get("ip")}

    for device in devices:
        ip = device.get("ip")
        if ip and ip in existing_ips:
            for i, existing in enumerate(_state["discovered_devices"]):
                if existing.get("ip") == ip:
                    _state["discovered_devices"][i] = device
                    break
        else:
            _state["discovered_devices"].append(device)
            if ip:
                existing_ips.add(ip)

    save_config()


def forget_device() -> None:
    """Forget/unpair the current device."""
    _state["current_device"] = None
    save_config()
    print("🔌 Device forgotten")
