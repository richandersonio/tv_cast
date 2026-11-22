"""Interactive menus for the TV Caster."""

import asyncio
import os

from .casting import cast_live_rio, cast_video, stop_playback
from .config import (
    APP_DATE,
    APP_VERSION,
    SAMPLE_YOUTUBE_TITLE,
    SAMPLE_YOUTUBE_URL,
    YOUTUBE_CACHE_DIR,
    forget_device,
    get_current_device,
    save_config,
    save_discovered_devices,
    set_current_device,
)
from .conversion import (
    count_hls_cache_entries,
    count_image_cache_entries,
    image_to_video,
    is_cached,
)
from .discovery import discover_all_devices, discover_dlna_devices
from .rio import create_dancing_rio_video
from .utils import format_size, get_device_icon, is_youtube_url, prompt_input
from .youtube import find_cached_youtube_videos


def find_video_files(directory: str = ".") -> list[tuple[str, int, bool]]:
    """Find all video files in directory."""
    video_extensions = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts'}
    videos = []

    for file in os.listdir(directory):
        full_path = os.path.join(directory, file)
        if os.path.isfile(full_path):
            _, ext = os.path.splitext(file.lower())
            if ext in video_extensions:
                size = os.path.getsize(full_path)
                file_path = os.path.abspath(full_path)
                cached, _ = is_cached(file_path)
                videos.append((file, size, cached))

    videos.sort(key=lambda x: x[0].lower())
    return videos


def find_image_files(directory: str = ".") -> list[tuple[str, int]]:
    """Find all image files in directory."""
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    images = []

    for file in os.listdir(directory):
        full_path = os.path.join(directory, file)
        if os.path.isfile(full_path):
            _, ext = os.path.splitext(file.lower())
            if ext in image_extensions:
                size = os.path.getsize(full_path)
                images.append((file, size))

    images.sort(key=lambda x: x[0].lower())
    return images


def select_youtube_submenu() -> str | None:
    """Show YouTube selection submenu with sample option."""
    print()
    print("   " + "-" * 40)
    print("   📺 YouTube")
    print("   " + "-" * 40)

    print(f"\n   1. {SAMPLE_YOUTUBE_TITLE}")
    print("\n   p. Enter a custom URL")
    print("   b. Back")
    print()

    while True:
        try:
            choice = input("   Select (1, p, or b): ").strip().lower()

            if choice == 'b':
                return None

            if choice == '1':
                return SAMPLE_YOUTUBE_URL

            if choice == 'p':
                url = input("   Enter YouTube URL: ").strip()
                if url and is_youtube_url(url):
                    return url
                else:
                    print("   ❌ Invalid YouTube URL")
                    continue

            print("   Please enter 1, p, or b")

        except ValueError:
            print("   Please enter a valid option")
        except (KeyboardInterrupt, EOFError):
            print()
            return None


def select_image_submenu() -> str | None:
    """Show image selection submenu."""
    print()
    print("   " + "-" * 40)
    print("   🖼️  Select Image")
    print("   " + "-" * 40)

    images = find_image_files()

    if images:
        print()
        for i, (name, size) in enumerate(images, 1):
            print(f"   {i}. {name} ({format_size(size)})")
    else:
        print("\n   (no images in current directory)")

    print("\n   p. Enter a custom path")
    print("   b. Back")
    print()

    while True:
        try:
            if images:
                choice = input(f"   Select (1-{len(images)}, p, or b): ").strip().lower()
            else:
                choice = input("   Select (p or b): ").strip().lower()

            if choice == 'b':
                return None

            if choice == 'p':
                path = input("   Enter image path: ").strip()
                if path and os.path.exists(path):
                    _, ext = os.path.splitext(path.lower())
                    if ext in {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}:
                        return f"IMAGE:{path}"
                    else:
                        print("   ❌ Not a supported image format")
                        continue
                else:
                    print("   ❌ File not found")
                    continue

            if images:
                idx = int(choice) - 1
                if 0 <= idx < len(images):
                    return f"IMAGE:{images[idx][0]}"
                else:
                    print(f"   Please enter 1-{len(images)}, p, or b")
            else:
                print("   Please enter p or b")

        except ValueError:
            print("   Please enter a valid option")
        except (KeyboardInterrupt, EOFError):
            print()
            return None


def interactive_select() -> str:
    """Let user interactively select a video file."""
    current_device = get_current_device()

    print("=" * 60)
    print("🎬 TV Playback")
    print("=" * 60)

    if current_device:
        print(f"   📡 Device: {current_device['name']}")
    else:
        print("   ⚠️  Device: Not selected")
    print()

    local_videos = find_video_files()
    youtube_videos = find_cached_youtube_videos()
    local_images = find_image_files()

    all_items = []

    if local_videos:
        print("📁 Local Videos:\n")
        for i, (name, size, cached) in enumerate(local_videos, 1):
            cache_icon = "⚡" if cached else "  "
            print(f"   {i:2d}. {cache_icon} {name} ({format_size(size)})")
            all_items.append(('video', name))

    if youtube_videos:
        start_idx = len(all_items) + 1
        print("\n📺 Cached YouTube Videos:\n")
        for i, yt in enumerate(youtube_videos, start_idx):
            cache_icon = "⚡" if yt['hls_cached'] else "  "
            title = yt['title'][:45] + "..." if len(yt['title']) > 48 else yt['title']
            print(f"   {i:2d}. {cache_icon} {title} ({format_size(yt['size'])})")
            all_items.append(('youtube', yt['path']))

    if local_images:
        start_idx = len(all_items) + 1
        print("\n🖼️  Images (will display for 10s):\n")
        for i, (name, size) in enumerate(local_images, start_idx):
            print(f"   {i:2d}.    {name} ({format_size(size)})")
            all_items.append(('image', name))

    if not local_videos and not youtube_videos and not local_images:
        print("   (no media found)")

    print("\n   ⚡ = cached (instant playback)")
    print("\n   y. 📺 YouTube - enter a new URL")
    print("   i. 🖼️  Display an image (enter path)")
    print("   r. 🕺 Dancing RIO logo (15s)")
    print("   l. 🔴 Live dancing RIO stream (real-time)")
    print("   0. ⏹️  Stop current playback")
    print("   b. ← Back to main menu")
    print()

    max_choice = len(all_items)

    while True:
        try:
            if max_choice > 0:
                choice = input(f"Select (1-{max_choice}), y/i/r/l/0/b: ").strip().lower()
            else:
                choice = input("Enter y/i/r/l/0/b: ").strip().lower()

            if choice == 'b' or choice == 'q':
                return 'BACK'

            if choice == '0':
                return 'STOP'

            if choice == 'r':
                return 'RIO'

            if choice == 'l':
                return 'RIO_LIVE'

            if choice == 'y':
                result = select_youtube_submenu()
                if result:
                    return result
                continue

            if choice == 'i':
                result = select_image_submenu()
                if result:
                    return result
                continue

            if max_choice > 0:
                idx = int(choice) - 1
                if 0 <= idx < len(all_items):
                    item_type, item_path = all_items[idx]
                    if item_type == 'image':
                        return f"IMAGE:{item_path}"
                    return item_path
                else:
                    print(f"   Please enter 1-{max_choice} or a menu option")
            else:
                print("   Please enter a valid option")

        except ValueError:
            print("   Please enter a valid option")
        except (KeyboardInterrupt, EOFError):
            print()
            return 'BACK'


def main_menu() -> str:
    """Show the main parent menu."""
    current_device = get_current_device()

    print("=" * 60)
    print(f"📺 Samsung TV Caster v{APP_VERSION} — {APP_DATE}")
    print("=" * 60)

    if current_device:
        print(f"\n   ✅ Device: {current_device['name']}")
    else:
        print("\n   ⚠️  No device selected")

    cache_count = count_hls_cache_entries()
    if os.path.exists(YOUTUBE_CACHE_DIR):
        cache_count += len([f for f in os.listdir(YOUTUBE_CACHE_DIR) if f.endswith('.mp4')])
    cache_count += count_image_cache_entries()

    print("\n   1. 🎬 TV Playback - cast videos/images")
    print("   2. 📡 Network Devices - scan and select devices")
    if cache_count > 0:
        print(f"   3. 🗑️  Clear Cache ({cache_count} cached)")
    print("\n   q. Quit")
    print()

    while True:
        try:
            choice = input("Select option: ").strip().lower()

            if not choice or choice == 'q':
                return 'QUIT'

            if choice == '1':
                return 'PLAYBACK'

            if choice == '2':
                return 'DEVICES'

            if choice == '3' and cache_count > 0:
                return 'CLEAR_CACHE'

            print("   Please enter a valid option")

        except ValueError:
            print("   Please enter a valid option")
        except (KeyboardInterrupt, EOFError):
            print()
            return 'QUIT'


def playback_menu_loop() -> None:
    """Run the playback menu loop."""
    while True:
        try:
            video = interactive_select()
            if video == 'BACK':
                return
            elif video == 'STOP':
                current_device = get_current_device()
                if current_device:
                    asyncio.run(stop_playback())
                else:
                    print("❌ No device selected")
                print()
            else:
                current_device = get_current_device()
                if not current_device:
                    print("\n❌ No device selected. Please select a device first.")
                    print("   Use the 'Network Devices' menu to scan and select a device.")
                    print()
                    continue

                if video == 'RIO':
                    video_path = create_dancing_rio_video(duration=15)
                    if video_path:
                        asyncio.run(cast_video(video_path, duration=17))
                    else:
                        print("❌ Failed to generate dancing RIO animation")
                elif video == 'RIO_LIVE':
                    asyncio.run(cast_live_rio(duration=0))
                elif video.startswith("IMAGE:"):
                    image_path = video[6:]
                    video_path = image_to_video(image_path, duration=10)
                    if video_path:
                        asyncio.run(cast_video(video_path, duration=12))
                    else:
                        print("❌ Failed to convert image")
                else:
                    asyncio.run(cast_video(video))
                print()
        except KeyboardInterrupt:
            print("\n")
            continue


async def device_menu() -> str | None:
    """Show the device management menu."""
    current_device = get_current_device()

    print("=" * 60)
    print("📡 Network Devices")
    print("=" * 60)

    if current_device:
        print(f"\n   ✅ Current: {current_device['name']} ({current_device.get('ip', 'unknown')})")
    else:
        print("\n   ⚠️  No device selected")

    print("\n   1. 🔍 Quick scan (TVs only)")
    print("   2. 🔬 Deep scan (all devices)")
    print("   3. ❌ Forget current device")
    print("\n   b. ← Back to main menu")
    print()

    while True:
        try:
            choice = prompt_input("Select option: ").strip().lower()

            if choice == 'b' or choice == 'q':
                return 'BACK'

            if choice == '1':
                print("\n🔍 Scanning for TVs...")
                devices = await discover_dlna_devices(timeout=5)
                if devices:
                    save_discovered_devices(devices)
                    print(f"\n✅ Found {len(devices)} TV(s)")

                    # Offer to select
                    print()
                    for i, dev in enumerate(devices, 1):
                        icon = get_device_icon(dev)
                        print(f"   {i}. {icon} {dev.get('name', 'Unknown')} ({dev.get('ip')})")

                    print("\n   0. Cancel")
                    print()

                    try:
                        sel = prompt_input(f"Select device (1-{len(devices)}, or 0): ").strip()
                        if sel != '0':
                            idx = int(sel) - 1
                            if 0 <= idx < len(devices):
                                set_current_device(devices[idx])
                                save_config()
                                print(f"\n✅ Selected: {devices[idx].get('name')}")
                    except (ValueError, KeyboardInterrupt, EOFError):
                        pass
                else:
                    print("\n❌ No TVs found. Make sure your TV is on and connected.")
                print()
                continue

            if choice == '2':
                devices = await discover_all_devices(timeout=8)
                if devices:
                    save_discovered_devices(devices)
                    castable = [d for d in devices if d.get('castable')]
                    if castable:
                        print("\n📺 Cast-capable devices:\n")
                        for i, dev in enumerate(castable, 1):
                            icon = get_device_icon(dev)
                            print(f"   {i}. {icon} {dev.get('name', 'Unknown')} ({dev.get('ip')})")

                        print("\n   0. Cancel")
                        print()

                        try:
                            sel = prompt_input(f"Select device (1-{len(castable)}, or 0): ").strip()
                            if sel != '0':
                                idx = int(sel) - 1
                                if 0 <= idx < len(castable):
                                    set_current_device(castable[idx])
                                    save_config()
                                    print(f"\n✅ Selected: {castable[idx].get('name')}")
                        except (ValueError, KeyboardInterrupt, EOFError):
                            pass
                print()
                continue

            if choice == '3':
                forget_device()
                print()
                continue

            print("   Please enter a valid option")

        except (KeyboardInterrupt, EOFError):
            print()
            return 'BACK'
