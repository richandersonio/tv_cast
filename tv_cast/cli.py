"""Command-line interface for TV Caster."""

import argparse
import asyncio
import os
from collections.abc import Sequence

from .casting import cast_live_rio, cast_video, get_av_transport, stop_playback
from .config import (
    YOUTUBE_CACHE_DIR,
    forget_device,
    get_current_device,
    get_discovered_devices,
    load_config,
    save_config,
    save_discovered_devices,
    set_current_device,
)
from .conversion import (
    clear_cache,
    count_hls_cache_entries,
    count_image_cache_entries,
    count_rio_cache_entries,
    image_to_video,
)
from .discovery import discover_all_devices, discover_dlna_devices
from .menu import device_menu, main_menu, playback_menu_loop
from .rio import create_dancing_rio_video
from .tv_state import (
    discover_installed_apps_via_rest,
    format_tv_state,
    get_tv_state,
    pair_and_fetch_installed_apps,
)
from .utils import is_valid_ip


def _non_negative_int(value: str) -> int:
    """Argparse type ensuring a non-negative integer duration."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer value: {value!r}") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("duration must be >= 0")
    return parsed


def show_status() -> None:
    """Show current device status."""
    current_device = get_current_device()

    if current_device:
        print(f"📺 Current device: {current_device['name']}")
        print(f"   IP: {current_device.get('ip', 'unknown')}")
        if current_device.get("location"):
            print(f"   DLNA: {current_device['location']}")
    else:
        print("⚠️  No device selected")
        print("   Use --scan to find TVs, then --device IP to select one")

    print("\n📁 Cache:")

    hls_count = count_hls_cache_entries()
    print(f"   HLS conversions: {hls_count}")

    yt_count = 0
    if os.path.exists(YOUTUBE_CACHE_DIR):
        yt_count = len([f for f in os.listdir(YOUTUBE_CACHE_DIR) if f.endswith(".mp4")])
    print(f"   YouTube downloads: {yt_count}")

    img_count = count_image_cache_entries(include_rio=False)
    print(f"   Image videos: {img_count}")
    rio_count = count_rio_cache_entries()
    if rio_count:
        print(f"   RIO animations: {rio_count}")

    if hls_count + yt_count + img_count + rio_count > 0:
        print("\n   Use --clear-cache to clear all")


def list_devices_cli() -> None:
    """List all discovered devices."""
    discovered_devices = get_discovered_devices()
    current_device = get_current_device()

    if not discovered_devices:
        print("⚠️  No devices discovered yet")
        print("   Use --scan to find TVs on the network")
        return

    castable = [d for d in discovered_devices if d.get("castable", False)]
    other = [d for d in discovered_devices if not d.get("castable", False)]

    if castable:
        print("📺 Cast-capable devices:\n")
        for dev in castable:
            current = (
                " ← selected"
                if current_device and current_device.get("ip") == dev.get("ip")
                else ""
            )
            print(f"   {dev.get('ip'):15}  {dev.get('name', 'Unknown')}{current}")

    if other:
        print("\n📱 Other devices:\n")
        for dev in other:
            print(f"   {dev.get('ip'):15}  {dev.get('name', 'Unknown')}")


async def scan_devices_cli() -> None:
    """Scan for cast-capable devices (CLI mode)."""
    print("🔍 Scanning for cast-capable TVs...")
    devices = await discover_dlna_devices(timeout=5)

    if devices:
        save_discovered_devices(devices)
        print(f"\n✅ Found {len(devices)} TV(s):\n")
        for dev in devices:
            print(f"   {dev.get('ip'):15}  {dev.get('name', 'Unknown')}")
        print("\n💡 Use --device IP to select a TV")
    else:
        print("\n❌ No TVs found on the network")
        print("   Make sure your TV is on and connected to the same network")


async def scan_all_devices_cli() -> None:
    """Scan for all network devices (CLI mode)."""
    devices = await discover_all_devices(timeout=8)

    if devices:
        save_discovered_devices(devices)
        castable = [d for d in devices if d.get("castable", False)]
        print(f"\n💡 Found {len(castable)} castable device(s)")
        if castable:
            print("   Use --device IP to select one")


def set_device_by_ip(ip: str) -> bool:
    """Set the current device by IP address."""
    ip = ip.strip()
    if not is_valid_ip(ip):
        print(f"❌ Invalid IP address: {ip}")
        return False

    discovered_devices = get_discovered_devices()

    for dev in discovered_devices:
        if dev.get("ip") == ip:
            set_current_device(dev)
            save_config()
            print(f"✅ Selected: {dev.get('name', ip)} ({ip})")
            return True

    device = {
        "ip": ip,
        "name": f"TV ({ip})",
        "type": "dlna",
        "castable": True,
    }
    set_current_device(device)
    save_config()
    print(f"✅ Selected: {ip}")
    print("   (Device will be fully discovered on first connection)")
    return True


def cast_image_cli(image_path: str, duration: int, restore: bool = True) -> None:
    """Cast an image from CLI."""
    current_device = get_current_device()

    if not current_device:
        print("❌ No device selected. Use --scan and --device IP to set one.")
        return

    if not os.path.exists(image_path):
        print(f"❌ Image not found: {image_path}")
        return

    _, ext = os.path.splitext(image_path.lower())
    if ext not in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}:
        print(f"❌ Unsupported image format: {ext}")
        return

    video_path = image_to_video(image_path, duration=duration)
    if video_path:
        asyncio.run(cast_video(video_path, duration=duration + 2, restore=restore))
    else:
        print("❌ Failed to convert image")


def cast_rio_cli(duration: int, restore: bool = True) -> None:
    """Cast the dancing RIO logo animation from CLI."""
    current_device = get_current_device()

    if not current_device:
        print("❌ No device selected. Use --scan and --device IP to set one.")
        return

    video_path = create_dancing_rio_video(duration=duration)
    if video_path:
        asyncio.run(cast_video(video_path, duration=duration + 2, restore=restore))
    else:
        print("❌ Failed to generate dancing RIO animation")


def cast_rio_live_cli(duration: int, restore: bool = True) -> None:
    """Stream the dancing RIO logo live in real time from CLI."""
    current_device = get_current_device()

    if not current_device:
        print("❌ No device selected. Use --scan and --device IP to set one.")
        return

    asyncio.run(cast_live_rio(duration=duration, restore=restore))


async def show_tv_state_cli() -> None:
    """Query and display what the selected TV is currently showing/playing."""
    current_device = get_current_device()
    if not current_device:
        print("❌ No device selected. Use --scan and --device IP to set one.")
        return

    cached_apps = current_device.get("installed_apps")
    extra_apps = cached_apps if isinstance(cached_apps, dict) else None
    av_transport, _ = await get_av_transport(interactive=False)
    snapshot = await get_tv_state(
        current_device["ip"], av_transport=av_transport, extra_apps=extra_apps
    )
    print(format_tv_state(snapshot))


async def list_tv_apps_cli() -> None:
    """Discover installed TV apps (via paired WebSocket + local REST fallback) and cache them."""
    current_device = get_current_device()
    if not current_device:
        print("❌ No device selected. Use --scan and --device IP to set one.")
        return

    ip = current_device["ip"]
    saved_token = current_device.get("samsung_token")

    ws_apps: dict[str, str] = {}
    try:
        token, ws_apps = await pair_and_fetch_installed_apps(ip, token=saved_token)
        if token:
            current_device["samsung_token"] = token
    except Exception as e:
        detail = str(e) or type(e).__name__
        print(
            f"⚠️  WebSocket app query skipped ({detail}); probing installed apps via REST..."
        )

    rest_entries = await discover_installed_apps_via_rest(ip)
    status_by_id: dict[str, tuple[bool, bool]] = {}
    merged_apps: dict[str, str] = dict(ws_apps)
    for app_id, name, running, visible in rest_entries:
        merged_apps[app_id] = name
        status_by_id[app_id] = (running, visible)

    if merged_apps:
        current_device["installed_apps"] = merged_apps
    save_config()

    if not merged_apps:
        print("⚠️  No installed apps detected on TV.")
        return

    if not ws_apps:
        print(
            "   (Note: newer Samsung Tizen firmware disables full WebSocket app listing; "
            "showing apps detected via local REST API.)"
        )

    print(
        f"\n📱 Installed apps on {current_device.get('name', ip)} ({len(merged_apps)} found):\n"
    )
    for app_id, name in sorted(merged_apps.items(), key=lambda kv: kv[1].lower()):
        running, visible = status_by_id.get(app_id, (False, False))
        badge = " ← foreground" if visible else (" (running)" if running else "")
        print(f"   {app_id:18}  {name}{badge}")
    print("\n✅ Saved app list for --tv-state and auto-restore.")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Cast videos to your TV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Casting
  python -m tv_cast movie.mp4           # Cast a video (Ctrl+C to stop)
  python -m tv_cast movie.mp4 -d 30     # Cast for 30 seconds
  python -m tv_cast "https://youtube.com/watch?v=..."  # Cast YouTube video
  python -m tv_cast --image photo.jpg   # Display an image for 10 seconds
  python -m tv_cast --rio               # Cast a dancing RIO logo animation
  python -m tv_cast --rio-live          # Stream live real-time dancing RIO logo
  python -m tv_cast --stop              # Stop current playback

  # Device management
  python -m tv_cast --scan              # Scan network for TVs
  python -m tv_cast --device 192.168.1.50  # Set TV by IP address
  python -m tv_cast --status            # Show current device
  python -m tv_cast --list-devices      # List all discovered devices
  python -m tv_cast --forget            # Forget current device

  # Cache
  python -m tv_cast --clear-cache       # Clear cached conversions

  # Interactive mode
  python -m tv_cast                     # Launch interactive menu
        """,
    )

    parser.add_argument("video", nargs="?", help="Video file or YouTube URL to cast")

    # Casting options
    parser.add_argument(
        "-d",
        "--duration",
        type=_non_negative_int,
        default=0,
        help="Duration in seconds (0 = until Ctrl+C)",
    )
    parser.add_argument(
        "--image",
        metavar="PATH",
        help="Display an image on TV (10 second duration)",
    )
    parser.add_argument(
        "--rio",
        action="store_true",
        help="Cast an animated dancing RIO logo (15 second default duration)",
    )
    parser.add_argument(
        "--rio-live",
        action="store_true",
        help="Stream a live real-time dancing RIO logo (until Ctrl+C, or -d seconds)",
    )
    parser.add_argument(
        "--no-restore",
        action="store_true",
        help="Do not restore the previous TV app/stream after playback stops",
    )
    parser.add_argument(
        "--tv-state",
        action="store_true",
        help="Show what the TV is currently displaying/playing",
    )
    parser.add_argument(
        "--tv-apps",
        action="store_true",
        help="Pair with Samsung TV (one-time Allow) and list/cache all installed apps",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop current playback",
    )

    # Device management
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan network for cast-capable TVs",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan network for ALL devices (deep scan)",
    )
    parser.add_argument(
        "--device",
        metavar="IP",
        help="Set TV by IP address",
    )
    parser.add_argument(
        "--select-device",
        action="store_true",
        help="Interactively select a device",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List all discovered devices",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current device and status",
    )
    parser.add_argument(
        "--forget",
        action="store_true",
        help="Forget/unpair current device",
    )

    # Cache
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear cached HLS conversions",
    )

    return parser


def run_cli(argv: Sequence[str] | None = None) -> None:
    """Run the command-line interface."""
    load_config()

    parser = build_parser()
    args = parser.parse_args(argv)

    # Handle commands in priority order
    restore = not args.no_restore
    if args.status:
        show_status()
    elif args.tv_state:
        asyncio.run(show_tv_state_cli())
    elif args.tv_apps:
        asyncio.run(list_tv_apps_cli())
    elif args.list_devices:
        list_devices_cli()
    elif args.scan:
        asyncio.run(scan_devices_cli())
    elif args.scan_all:
        asyncio.run(scan_all_devices_cli())
    elif args.device:
        set_device_by_ip(args.device)
    elif args.select_device:
        asyncio.run(device_menu())
    elif args.forget:
        forget_device()
    elif args.clear_cache:
        clear_cache()
    elif args.stop:
        asyncio.run(stop_playback())
    elif args.image:
        cast_image_cli(args.image, args.duration or 10, restore=restore)
    elif args.rio:
        cast_rio_cli(args.duration or 15, restore=restore)
    elif args.rio_live:
        cast_rio_live_cli(args.duration, restore=restore)
    elif args.video:
        current_device = get_current_device()
        if not current_device:
            print("❌ No device selected. Use --scan and --device IP to set one.")
            return
        asyncio.run(cast_video(args.video, args.duration, restore=restore))
    else:
        # Interactive mode
        run_interactive()


def run_interactive() -> None:
    """Run the interactive menu loop."""
    while True:
        try:
            choice = main_menu()

            if choice == "QUIT":
                break
            elif choice == "PLAYBACK":
                playback_menu_loop()
            elif choice == "DEVICES":
                asyncio.run(device_menu())
            elif choice == "CLEAR_CACHE":
                clear_cache()
                print()

        except KeyboardInterrupt:
            print("\n")
            continue
