"""Video casting to Samsung TVs via DLNA."""

import asyncio
import contextlib
import functools
import html
import http.server
import logging
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from collections import deque
from typing import Any
from urllib.parse import urljoin, urlparse

from async_upnp_client.aiohttp import AiohttpRequester
from async_upnp_client.client_factory import UpnpFactory

from .config import (
    HTTP_PORT,
    get_current_device,
    save_config,
    save_discovered_devices,
    set_current_device,
)
from .conversion import convert_to_hls
from .discovery import discover_dlna_devices
from .rio import build_dancing_rio_live_hls_cmd, render_rio_assets
from .tv_state import (
    TVStateSnapshot,
    get_tv_state,
    play_youtube_on_tv,
    restore_tv_state,
    stop_youtube_on_tv,
)
from .utils import (
    check_port,
    extract_youtube_video_id,
    format_host_for_url,
    get_local_ip,
    is_youtube_url,
    prompt_input,
)
from .youtube import download_youtube

logger = logging.getLogger(__name__)

_ALLOWED_HLS_EXTENSIONS = frozenset({".m3u8", ".ts"})
_active_playback_control_url: str | None = None


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that serves only HLS files from its configured directory."""

    extensions_map: dict[str, str] = {  # noqa: RUF012 - mirrors the base-class attribute
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".m3u8": "application/vnd.apple.mpegurl",
        ".ts": "video/MP2T",
    }

    def log_message(self, format: str, *args: Any) -> None:
        if args:
            print(f"   📥 TV: {args[0]}")

    def end_headers(self) -> None:
        # SimpleHTTPRequestHandler does not implement HTTP Range / 206 responses.
        self.send_header("Accept-Ranges", "none")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def list_directory(self, path: str | os.PathLike[str]) -> None:
        self.send_error(403, "Directory listing not allowed")
        return None

    def send_head(self):
        path = self.translate_path(self.path)
        root = os.path.realpath(self.directory)
        real_path = os.path.realpath(path)

        if os.path.commonpath([root, real_path]) != root:
            self.send_error(403, "Forbidden")
            return None

        if os.path.isdir(real_path):
            self.send_error(403, "Directory listing not allowed")
            return None

        _, ext = os.path.splitext(real_path.lower())
        if ext not in _ALLOWED_HLS_EXTENSIONS:
            self.send_error(403, "Forbidden")
            return None

        return super().send_head()


class ReusableThreadingHTTPServer(http.server.ThreadingHTTPServer):
    """Multi-threaded HTTP server with immediate socket reuse."""

    allow_reuse_address = True
    daemon_threads = True


def _uncancel_current_task() -> None:
    """Clear cancellation state on the current asyncio task after deliberately handling cancellation."""
    task = asyncio.current_task()
    if task is not None:
        task.uncancel()


def _resolve_control_url(device: dict[str, Any]) -> str:
    """Determine the AVTransport SOAP control URL for a device."""
    saved_control_url = device.get("control_url")
    if saved_control_url:
        return str(saved_control_url)

    location = device.get("location")
    if not location:
        host = format_host_for_url(device["ip"])
        port = device.get("port", 9197)
        location = f"http://{host}:{port}/dmr"

    parsed = urlparse(location)
    scheme = parsed.scheme or "http"
    default_port = 443 if scheme == "https" else 80
    port = parsed.port or default_port
    host = format_host_for_url(parsed.hostname or "")
    return f"{scheme}://{host}:{port}/upnp/control/AVTransport1"


def _is_interactive(interactive: bool | None) -> bool:
    if interactive is not None:
        return interactive
    return sys.stdin.isatty()


async def get_av_transport(
    interactive: bool | None = None,
    max_attempts: int = 2,
) -> tuple[Any | None, str | None]:
    """Connect to selected device and get AVTransport service."""
    allow_prompt = _is_interactive(interactive)

    for attempt in range(max(1, max_attempts)):
        current_device = get_current_device()

        if not current_device:
            print("❌ No device selected. Use 'd' to select a device.")
            return None, None

        ip = current_device["ip"]
        device_name = current_device.get("name", ip)

        print(f"   Checking if {ip} is reachable...")

        test_ports = [9197, 8001, 8002, 7676, 7678, 80]
        saved_port = current_device.get("port")
        if isinstance(saved_port, int) and saved_port not in test_ports:
            test_ports.insert(0, saved_port)

        port_checks = await asyncio.gather(
            *(asyncio.to_thread(check_port, ip, port, 1.0) for port in test_ports)
        )
        reachable = any(port_checks)

        if not reachable:
            print(f"\n⚠️  Device '{device_name}' at {ip} is not responding.")
            print("   Possible causes:")
            print("   • TV is turned off or in deep standby")
            print("   • TV's IP address may have changed")

            if allow_prompt and attempt + 1 < max_attempts:
                switched = await _scan_and_offer_alternatives(ip, device_name)
                if switched:
                    print("\n📺 Connecting to new device...")
                    continue
            return None, None

        requester = AiohttpRequester()
        factory = UpnpFactory(requester)

        locations_to_try = []

        saved_location = current_device.get("location")
        if saved_location:
            locations_to_try.append(saved_location)

        port = current_device.get("port", 9197)
        url_host = format_host_for_url(ip)
        common_endpoints = [
            f"http://{url_host}:{port}/dmr",
            f"http://{url_host}:9197/dmr",
            f"http://{url_host}:9197/dmr/SamsungMRDesc.xml",
            f"http://{url_host}:7676/dmr",
            f"http://{url_host}:7678/dmr",
        ]

        for endpoint in common_endpoints:
            if endpoint not in locations_to_try:
                locations_to_try.append(endpoint)

        last_error = None
        for location in locations_to_try:
            try:
                device = await asyncio.wait_for(
                    factory.async_create_device(location),
                    timeout=10.0,
                )

                for service in device.services.values():
                    if "AVTransport" in service.service_type:
                        changed = False
                        if location != saved_location:
                            current_device["location"] = location
                            changed = True
                        raw_control_url = getattr(service, "control_url", None)
                        if raw_control_url:
                            resolved_control = urljoin(location, raw_control_url)
                            if current_device.get("control_url") != resolved_control:
                                current_device["control_url"] = resolved_control
                                changed = True
                        if changed:
                            save_config()
                        return service, device.friendly_name

            except TimeoutError:
                last_error = f"Connection timed out ({location})"
                continue
            except Exception as e:
                last_error = str(e)
                continue

        print(f"\n⚠️  Could not connect to DLNA service on {device_name}")
        print("   Possible causes:")
        print("   • TV's DLNA/media sharing is disabled")
        print("   • TV needs to accept connection (check for popup on TV)")
        print("   • TV firmware may need updating")
        if last_error:
            print(f"\n   Last error: {last_error[:100]}")

        if allow_prompt and attempt + 1 < max_attempts:
            switched = await _scan_and_offer_alternatives(ip, device_name)
            if switched:
                print("\n📺 Connecting to new device...")
                continue

        return None, None

    return None, None


async def _scan_and_offer_alternatives(failed_ip: str, failed_name: str) -> bool:
    """Scan for alternative DLNA devices and offer to switch."""
    print("\n🔍 Scanning for other TVs on the network...")

    try:
        alternative_devices = await discover_dlna_devices(timeout=4)
        alternatives = [d for d in alternative_devices if d.get("ip") != failed_ip]

        if not alternatives:
            print("   No other TVs found on the network.")
            print("\n   💡 Try:")
            print("   • Turn on the TV and wait a moment")
            print("   • Check that the TV is on the same WiFi network")
            print("   • Run a full scan from the 'Network Devices' menu")
            return False

        print(f"\n📺 Found {len(alternatives)} other TV(s):\n")
        for i, dev in enumerate(alternatives, 1):
            print(f"   {i}. {dev.get('name', 'Unknown')} ({dev.get('ip')})")

        print("\n   0. Cancel (don't switch)")
        print()

        try:
            choice = prompt_input(
                f"Switch to a different TV? (1-{len(alternatives)}, or 0 to cancel): "
            ).strip()

            if choice == "0" or choice.lower() in ("", "n", "no", "q"):
                print("   Keeping current device selection.")
                return False

            idx = int(choice) - 1
            if 0 <= idx < len(alternatives):
                selected = alternatives[idx]
                set_current_device(selected)
                save_config()
                save_discovered_devices(alternatives)
                print(
                    f"\n✅ Switched to: {selected.get('name')} ({selected.get('ip')})"
                )
                return True
            else:
                print("   Invalid selection. Keeping current device.")
                return False

        except (ValueError, EOFError, KeyboardInterrupt):
            print("\n   Keeping current device selection.")
            return False

    except Exception as e:
        print(f"   Scan failed: {e}")
        print("\n   💡 Try running a full scan from the 'Network Devices' menu")
        return False


async def stop_playback(interactive: bool | None = None) -> bool:
    """Stop current playback on TV."""
    global _active_playback_control_url

    current_device = get_current_device()
    if not current_device:
        print("❌ No device selected")
        return False

    print(f"📺 Connecting to {current_device['name']}...")

    try:
        av_transport, tv_name = await get_av_transport(interactive=interactive)
        if av_transport:
            print(f"   ✅ Connected: {tv_name}")
            print("⏹️  Stopping playback...")
            stop = av_transport.action("Stop")
            await stop.async_call(InstanceID=0)
            _active_playback_control_url = None
            print("   ✅ Stopped")
            return True
    except Exception as e:
        print(f"❌ Error: {e}")

    return False


async def _capture_app_state(
    device: dict[str, Any] | None,
    restore: bool,
) -> TVStateSnapshot | None:
    """Capture the TV's foreground app (without DLNA state) before casting."""
    if not restore or not device or not device.get("ip"):
        return None
    cached_apps = device.get("installed_apps")
    extra_apps = cached_apps if isinstance(cached_apps, dict) else None
    snapshot = await get_tv_state(device["ip"], extra_apps=extra_apps)
    if snapshot.visible_app_name:
        print(
            f"   💾 Currently open: {snapshot.visible_app_name} (will restore after playback)"
        )
    return snapshot


async def _capture_previous_state(
    device: dict[str, Any] | None,
    av_transport: Any,
    restore: bool,
    app_state: TVStateSnapshot | None = None,
) -> TVStateSnapshot | None:
    """Capture current TV app/DLNA state before casting if restore is enabled.

    When ``app_state`` is given (captured before an earlier cast attempt changed the
    foreground app), its app fields are kept and only the DLNA state is refreshed.
    """
    if not restore or not device or not device.get("ip"):
        return None
    cached_apps = device.get("installed_apps")
    extra_apps = cached_apps if isinstance(cached_apps, dict) else None
    snapshot = await get_tv_state(
        device["ip"], av_transport=av_transport, extra_apps=extra_apps
    )
    if app_state is not None:
        snapshot.visible_app_id = app_state.visible_app_id
        snapshot.visible_app_name = app_state.visible_app_name
        snapshot.running_apps = list(app_state.running_apps)
        snapshot.youtube = app_state.youtube
    elif snapshot.visible_app_name:
        print(
            f"   💾 Currently open: {snapshot.visible_app_name} (will restore after playback)"
        )
    if (
        not snapshot.visible_app_name
        and snapshot.dlna_state == "PLAYING"
        and snapshot.dlna_uri
    ):
        label = snapshot.dlna_title or snapshot.dlna_uri
        print(f"   💾 Currently playing DLNA: {label} (will restore after playback)")
    return snapshot if snapshot.has_restorable_state else None


async def _wait_for_native_playback(duration: int) -> None:
    """Wait for the requested duration, or until Ctrl+C when duration is 0."""
    if duration > 0:
        print(f"\n⏱️  Playing for {duration} seconds...")
    else:
        print("\n⏱️  Press Ctrl+C to stop and return to menu")
    try:
        if duration > 0:
            await asyncio.sleep(duration)
        else:
            await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        _uncancel_current_task()


async def cast_video(
    video_path: str,
    duration: int = 0,
    interactive: bool | None = None,
    restore: bool = True,
) -> bool:
    """Cast video to Samsung TV."""
    global _active_playback_control_url

    current_device = get_current_device()
    if not current_device:
        print("❌ No device selected. Use --scan and --device IP to set one.")
        return False

    print("=" * 60)
    print("📺 Samsung TV Video Caster")
    print("=" * 60)

    cancel_event = threading.Event()
    pre_native_state: TVStateSnapshot | None = None

    if is_youtube_url(video_path):
        print(f"\n🎬 YouTube: {video_path}")
        yt_id = extract_youtube_video_id(video_path)
        if yt_id and current_device.get("ip"):
            # Snapshot the TV *before* the native attempt: a failed attempt may still
            # have launched the YouTube app, which must not be mistaken for the
            # user's previous app when falling back to DLNA.
            pre_cast_state = await _capture_app_state(current_device, restore=restore)
            played_natively = await play_youtube_on_tv(
                current_device["ip"],
                yt_id,
                samsung_token=current_device.get("samsung_token"),
            )
            if played_natively:
                await _wait_for_native_playback(duration)
                print("\n⏹️  Stopping YouTube playback...")
                if await stop_youtube_on_tv(current_device["ip"]):
                    print("   ✅ Stopped")
                else:
                    print("   ⚠️  Could not stop YouTube playback on the TV")
                if pre_cast_state is not None and pre_cast_state.has_restorable_state:
                    await restore_tv_state(
                        pre_cast_state,
                        samsung_token=current_device.get("samsung_token"),
                    )
                return True
            print(
                "   ⚠️  Native YouTube cast unavailable; falling back to yt-dlp + DLNA HLS..."
            )
            pre_native_state = pre_cast_state

        try:
            downloaded = await asyncio.to_thread(
                download_youtube, video_path, cancel_event
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            cancel_event.set()
            _uncancel_current_task()
            return False
        if not downloaded:
            return False
        video_path = downloaded
    elif not await asyncio.to_thread(os.path.exists, video_path):
        print(f"❌ File not found: {video_path}")
        return False

    video_path = await asyncio.to_thread(os.path.abspath, video_path)
    video_name = os.path.basename(video_path)
    print(f"\n🎬 Video: {video_name}")

    try:
        playlist_path = await asyncio.to_thread(
            convert_to_hls, video_path, cancel_event
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        cancel_event.set()
        _uncancel_current_task()
        return False
    if not playlist_path:
        return False

    cache_dir = os.path.dirname(playlist_path)

    device_name = current_device["name"]
    print(f"\n📺 Connecting to {device_name}...")
    av_transport, tv_name = await get_av_transport(interactive=interactive)

    if not av_transport:
        print("❌ Could not connect to TV")
        return False

    print(f"   ✅ Connected: {tv_name}")
    previous_state = await _capture_previous_state(
        get_current_device(), av_transport, restore=restore, app_state=pre_native_state
    )

    local_ip = get_local_ip()
    handler_cls = functools.partial(QuietHandler, directory=cache_dir)

    try:
        server = ReusableThreadingHTTPServer((local_ip, HTTP_PORT), handler_cls)
    except OSError as e:
        if e.errno in (48, 98):
            print(
                f"❌ Port {HTTP_PORT} is already in use. Stop the other tv-cast process and try again."
            )
        else:
            print(
                f"❌ Could not start local stream server on {local_ip}:{HTTP_PORT}: {e}"
            )
        return False

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    bound_port = server.server_address[1]
    hls_url = f"http://{format_host_for_url(local_ip)}:{bound_port}/playlist.m3u8"
    print(f"\n🌐 Stream URL: {hls_url}")

    playback_started = False
    try:
        escaped_video_name = html.escape(video_name, quote=True)
        escaped_hls_url = html.escape(hls_url, quote=True)
        didl = f"""<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
                    xmlns:dc="http://purl.org/dc/elements/1.1/"
                    xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">
            <item id="0" parentID="-1" restricted="1">
                <dc:title>{escaped_video_name}</dc:title>
                <upnp:class>object.item.videoItem</upnp:class>
                <res protocolInfo="http-get:*:application/vnd.apple.mpegurl:*">{escaped_hls_url}</res>
            </item>
        </DIDL-Lite>"""

        try:
            stop = av_transport.action("Stop")
            await stop.async_call(InstanceID=0)
        except Exception:
            logger.debug("Best-effort step failed", exc_info=True)

        print("\n🎬 Loading video...")
        set_uri = av_transport.action("SetAVTransportURI")
        await set_uri.async_call(
            InstanceID=0, CurrentURI=hls_url, CurrentURIMetaData=didl
        )

        await asyncio.sleep(0.5)

        print("▶️  Starting playback...")
        play = av_transport.action("Play")
        await play.async_call(InstanceID=0, Speed="1")
        playback_started = True

        active_device = get_current_device()
        if active_device:
            _active_playback_control_url = _resolve_control_url(active_device)

        print("\n" + "=" * 60)
        print("🎉 NOW PLAYING ON YOUR TV!")
        print("=" * 60)

        if duration > 0:
            print(f"\n⏱️  Playing for {duration} seconds...")
            await asyncio.sleep(duration)
        else:
            print("\n⏱️  Press Ctrl+C to stop and return to menu")
            try:
                await asyncio.Event().wait()
            except (KeyboardInterrupt, asyncio.CancelledError):
                _uncancel_current_task()

        print("\n⏹️  Stopping playback...")
        stop = av_transport.action("Stop")
        await stop.async_call(InstanceID=0)
        _active_playback_control_url = None
        print("   ✅ Stopped")
        if previous_state is not None:
            dev = get_current_device()
            token = dev.get("samsung_token") if dev else None
            await restore_tv_state(
                previous_state, av_transport=av_transport, samsung_token=token
            )

        return True

    except (KeyboardInterrupt, asyncio.CancelledError):
        _uncancel_current_task()
        print("\n⏹️  Stopping playback...")
        try:
            stop = av_transport.action("Stop")
            await stop.async_call(InstanceID=0)
            print("   ✅ Stopped")
        except Exception:
            logger.debug("Best-effort step failed", exc_info=True)
        _active_playback_control_url = None
        if previous_state is not None:
            dev = get_current_device()
            token = dev.get("samsung_token") if dev else None
            await restore_tv_state(
                previous_state, av_transport=av_transport, samsung_token=token
            )
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        logger.debug("Casting failed", exc_info=True)
        if playback_started:
            with contextlib.suppress(Exception):
                stop = av_transport.action("Stop")
                await stop.async_call(InstanceID=0)
            _active_playback_control_url = None
            if previous_state is not None:
                dev = get_current_device()
                token = dev.get("samsung_token") if dev else None
                await restore_tv_state(
                    previous_state, av_transport=av_transport, samsung_token=token
                )
        return False

    finally:
        _active_playback_control_url = None
        server.shutdown()
        with contextlib.suppress(socket.error):
            server.server_close()


async def _drain_async_stream(
    stream: asyncio.StreamReader | None, buffer: deque[str]
) -> None:
    """Continuously drain an async subprocess stream into a bounded buffer."""
    if stream is None:
        return
    try:
        while True:
            line = await stream.readline()
            if not line:
                break
            buffer.append(line.decode("utf-8", errors="replace"))
    except Exception:
        logger.debug("Best-effort step failed", exc_info=True)


async def _wait_for_live_playlist(
    playlist_path: str,
    process: asyncio.subprocess.Process,
    min_segments: int = 2,
    timeout: float = 20.0,
) -> bool:
    """Wait until ffmpeg produces a live playlist with at least min_segments."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.returncode is not None:
            return False
        if await asyncio.to_thread(os.path.exists, playlist_path):
            try:
                content = await asyncio.to_thread(_read_text_file, playlist_path)
                if content.count("#EXTINF:") >= min_segments:
                    return True
            except OSError:
                pass
        await asyncio.sleep(0.2)
    return False


def _read_text_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


async def cast_live_hls(
    ffmpeg_cmd: list[str],
    hls_dir: str,
    title: str = "Live Stream",
    duration: int = 0,
    interactive: bool | None = None,
    startup_timeout: float = 20.0,
    restore: bool = True,
) -> bool:
    """Stream a real-time rolling HLS feed produced by ffmpeg directly to the TV."""
    global _active_playback_control_url

    current_device = get_current_device()
    if not current_device:
        print("❌ No device selected. Use --scan and --device IP to set one.")
        return False

    print("=" * 60)
    print(f"🔴 Live Casting: {title}")
    print("=" * 60)

    device_name = current_device["name"]
    print(f"\n📺 Connecting to {device_name}...")
    av_transport, tv_name = await get_av_transport(interactive=interactive)
    if not av_transport:
        print("❌ Could not connect to TV")
        return False
    print(f"   ✅ Connected: {tv_name}")
    previous_state = await _capture_previous_state(
        get_current_device(), av_transport, restore=restore
    )

    print("\n🎬 Starting real-time encoder...")
    try:
        process = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        print("❌ ffmpeg not found. Please install ffmpeg and try again.")
        return False

    stderr_lines: deque[str] = deque(maxlen=200)
    stderr_task = asyncio.create_task(_drain_async_stream(process.stderr, stderr_lines))

    server: ReusableThreadingHTTPServer | None = None
    playback_started = False
    try:
        playlist_path = os.path.join(hls_dir, "playlist.m3u8")
        ready = await _wait_for_live_playlist(
            playlist_path, process, min_segments=2, timeout=startup_timeout
        )
        if not ready:
            stderr_tail = "".join(stderr_lines)[-500:]
            print(f"❌ Live stream failed to start: {stderr_tail}")
            return False

        local_ip = get_local_ip()
        handler_cls = functools.partial(QuietHandler, directory=hls_dir)
        try:
            server = ReusableThreadingHTTPServer((local_ip, HTTP_PORT), handler_cls)
        except OSError as e:
            if e.errno in (48, 98):
                print(
                    f"❌ Port {HTTP_PORT} is already in use. Stop the other tv-cast process and try again."
                )
            else:
                print(
                    f"❌ Could not start local stream server on {local_ip}:{HTTP_PORT}: {e}"
                )
            return False

        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        bound_port = server.server_address[1]
        hls_url = f"http://{format_host_for_url(local_ip)}:{bound_port}/playlist.m3u8"
        print(f"\n🌐 Live Stream URL: {hls_url}")

        escaped_title = html.escape(title, quote=True)
        escaped_hls_url = html.escape(hls_url, quote=True)
        didl = f"""<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
                    xmlns:dc="http://purl.org/dc/elements/1.1/"
                    xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">
            <item id="0" parentID="-1" restricted="1">
                <dc:title>{escaped_title}</dc:title>
                <upnp:class>object.item.videoItem</upnp:class>
                <res protocolInfo="http-get:*:application/vnd.apple.mpegurl:*">{escaped_hls_url}</res>
            </item>
        </DIDL-Lite>"""

        with contextlib.suppress(Exception):
            stop = av_transport.action("Stop")
            await stop.async_call(InstanceID=0)

        print("\n🎬 Loading live stream...")
        set_uri = av_transport.action("SetAVTransportURI")
        await set_uri.async_call(
            InstanceID=0, CurrentURI=hls_url, CurrentURIMetaData=didl
        )

        await asyncio.sleep(0.5)

        print("▶️  Starting live playback...")
        play = av_transport.action("Play")
        await play.async_call(InstanceID=0, Speed="1")
        playback_started = True

        active_device = get_current_device()
        if active_device:
            _active_playback_control_url = _resolve_control_url(active_device)

        print("\n" + "=" * 60)
        print("🔴 LIVE ON YOUR TV!")
        print("=" * 60)

        if duration > 0:
            print(f"\n⏱️  Streaming live for {duration} seconds...")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=duration)
        else:
            print("\n⏱️  Streaming live indefinitely — press Ctrl+C to stop")
            await process.wait()

        print("\n⏹️  Stopping live stream...")
        stop = av_transport.action("Stop")
        await stop.async_call(InstanceID=0)
        _active_playback_control_url = None
        print("   ✅ Stopped")
        if previous_state is not None:
            dev = get_current_device()
            token = dev.get("samsung_token") if dev else None
            await restore_tv_state(
                previous_state, av_transport=av_transport, samsung_token=token
            )
        return True

    except (KeyboardInterrupt, asyncio.CancelledError):
        _uncancel_current_task()
        print("\n⏹️  Stopping live stream...")
        with contextlib.suppress(Exception):
            stop = av_transport.action("Stop")
            await stop.async_call(InstanceID=0)
            print("   ✅ Stopped")
        _active_playback_control_url = None
        if previous_state is not None:
            dev = get_current_device()
            token = dev.get("samsung_token") if dev else None
            await restore_tv_state(
                previous_state, av_transport=av_transport, samsung_token=token
            )
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        if playback_started:
            with contextlib.suppress(Exception):
                stop = av_transport.action("Stop")
                await stop.async_call(InstanceID=0)
            _active_playback_control_url = None
            if previous_state is not None:
                dev = get_current_device()
                token = dev.get("samsung_token") if dev else None
                await restore_tv_state(
                    previous_state, av_transport=av_transport, samsung_token=token
                )
        return False

    finally:
        _active_playback_control_url = None
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stderr_task
        if server is not None:
            server.shutdown()
            with contextlib.suppress(socket.error):
                server.server_close()


async def cast_live_rio(
    duration: int = 0,
    interactive: bool | None = None,
    restore: bool = True,
) -> bool:
    """Render and stream the dancing RIO logo live in real time to the TV."""
    with tempfile.TemporaryDirectory(prefix="tv_cast_rio_live_") as tmp_root:
        assets_dir = os.path.join(tmp_root, "assets")
        hls_dir = os.path.join(tmp_root, "hls")
        await asyncio.to_thread(os.makedirs, assets_dir, exist_ok=True)
        await asyncio.to_thread(os.makedirs, hls_dir, exist_ok=True)

        print("🎨 Preparing live RIO sprites...")
        await asyncio.to_thread(render_rio_assets, assets_dir)
        cmd = build_dancing_rio_live_hls_cmd(assets_dir, hls_dir)

        return await cast_live_hls(
            cmd,
            hls_dir=hls_dir,
            title="Dancing RIO (Live)",
            duration=duration,
            interactive=interactive,
            restore=restore,
        )


def cleanup_on_exit() -> None:
    """Stop playback when exiting the app while playback is active."""
    global _active_playback_control_url

    control_url = _active_playback_control_url
    if not control_url:
        return

    _active_playback_control_url = None
    print("\n⏹️  Stopping playback...")

    try:
        soap_body = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
    <s:Body>
        <u:Stop xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">
            <InstanceID>0</InstanceID>
        </u:Stop>
    </s:Body>
</s:Envelope>"""

        req = urllib.request.Request(
            control_url,
            data=soap_body.encode("utf-8"),
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPACTION": '"urn:schemas-upnp-org:service:AVTransport:1#Stop"',
            },
        )

        with urllib.request.urlopen(req, timeout=2):
            pass
        print("   ✅ Stopped")
    except Exception:
        logger.debug("Best-effort step failed", exc_info=True)
