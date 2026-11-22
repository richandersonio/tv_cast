"""Query and restore Samsung Tizen TV foreground app / DLNA state."""

import asyncio
import base64
import contextlib
import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import aiohttp

from .utils import format_host_for_url

logger = logging.getLogger(__name__)

# Common Samsung Tizen Smart TV application IDs (queried via :8001/api/v2/applications/<id>).
YOUTUBE_APP_ID = "111299001912"

KNOWN_SAMSUNG_APPS: dict[str, str] = {
    "111299001912": "YouTube",
    "3201707014489": "YouTube TV",
    "3201608010191": "YouTube Kids",
    "3201907018807": "Netflix",
    "11101200001": "Netflix",
    "3201910019365": "Prime Video",
    "3201901017640": "Disney+",
    "3201807016597": "Apple TV",
    "3201908019041": "Apple Music",
    "3201601007625": "Hulu",
    "3202301029760": "Max",
    "3201601007230": "Max",
    "3202006020991": "Peacock",
    "3201710014981": "Paramount+",
    "3201512006963": "Plex",
    "3201912019686": "Emby",
    "3201606009684": "Spotify",
    "3202203026841": "Twitch",
    "3201512006785": "Samsung TV Plus",
    "3201808016802": "Pluto TV",
    "3201504001965": "Tubi",
    "3201708014618": "ESPN",
    "3201806016390": "FuboTV",
    "3201806016381": "Sling TV",
    "3201704012147": "Crunchyroll",
    "3201506003488": "Fandango at Home",
    "3201612011418": "Vimeo",
    "3201810017074": "Roku Channel",
    "3201909019271": "Xfinity Stream",
    "3201511006428": "DirecTV Stream",
    "3202010022098": "Discovery+",
    "3201703012079": "Starz",
    "3202103023232": "AMC+",
    "3201907018786": "BritBox",
    "3202008021577": "Xbox",
    "3202009021652": "NVIDIA GeForce NOW",
    "3201710014874": "Steam Link",
    "org.tizen.browser": "Web Browser",
}


_YOUTUBE_STATE_LABELS: dict[str, str] = {
    "-1": "unstarted",
    "0": "ended",
    "1": "playing",
    "2": "paused",
    "3": "buffering",
    "5": "cued",
}


@dataclass
class YouTubeNowPlaying:
    """Currently loaded/playing YouTube video reported via DIAL + Lounge."""

    video_id: str
    title: str | None = None
    author: str | None = None
    current_time: float | None = None
    duration: float | None = None
    state: str | None = None
    list_id: str | None = None
    screen_id: str | None = None

    @property
    def state_label(self) -> str:
        return _YOUTUBE_STATE_LABELS.get(str(self.state), "unknown")

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def _format_mmss(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "?"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


@dataclass
class TVStateSnapshot:
    """Snapshot of what the TV is currently displaying/playing."""

    device_ip: str
    power_state: str | None = None
    model_name: str | None = None
    visible_app_id: str | None = None
    visible_app_name: str | None = None
    running_apps: list[tuple[str, str]] = field(default_factory=list)
    dlna_state: str | None = None
    dlna_uri: str | None = None
    dlna_metadata: str | None = None
    dlna_title: str | None = None
    youtube: YouTubeNowPlaying | None = None

    @property
    def has_restorable_state(self) -> bool:
        return bool(
            self.visible_app_id or (self.dlna_state == "PLAYING" and self.dlna_uri)
        )


def _extract_didl_title(metadata: str | None) -> str | None:
    """Extract dc:title from a DIDL-Lite XML metadata string if present."""
    if not metadata:
        return None
    try:
        root = ET.fromstring(metadata)
        for elem in root.iter():
            if elem.tag.endswith("}title") or elem.tag == "title":
                if elem.text and elem.text.strip():
                    return elem.text.strip()
    except ET.ParseError:
        return None
    return None


_MAX_CONCURRENT_APP_PROBES = 4


async def _fetch_app_status(
    session: aiohttp.ClientSession,
    base_url: str,
    app_id: str,
    fallback_name: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, str, bool, bool] | None:
    """Query one Samsung app ID. Returns (id, name, running, visible) or None."""
    url = f"{base_url}/api/v2/applications/{app_id}"
    async with semaphore:
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                if not isinstance(data, dict):
                    return None
                name = str(data.get("name") or fallback_name)
                running = bool(data.get("running", False))
                visible = bool(data.get("visible", False))
                return app_id, name, running, visible
        except Exception:
            logger.debug("Best-effort step failed", exc_info=True)
            return None


def _parse_lounge_chunks(text: str) -> list[tuple[str, list[Any]]]:
    """Parse YouTube Lounge length-prefixed JSON event chunks."""
    events: list[tuple[str, list[Any]]] = []
    idx = 0
    n = len(text)
    while idx < n:
        while idx < n and text[idx].isspace():
            idx += 1
        if idx >= n:
            break
        nl = text.find("\n", idx)
        if nl == -1:
            break
        try:
            length = int(text[idx:nl].strip())
        except ValueError:
            break
        chunk = text[nl + 1 : nl + 1 + length]
        idx = nl + 1 + length
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, list):
            continue
        for item in parsed:
            if (
                isinstance(item, list)
                and len(item) == 2
                and isinstance(item[1], list)
                and item[1]
            ):
                events.append((str(item[1][0]), item[1][1:]))
    return events


def _extract_dial_screen_id(xml_text: str) -> str | None:
    """Extract the YouTube Lounge <screenId> from a TV's DIAL YouTube XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    for elem in root.iter():
        if elem.tag.endswith("screenId") and elem.text and elem.text.strip():
            return elem.text.strip()
    return None


def _parse_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


async def _fetch_youtube_oembed(
    session: aiohttp.ClientSession,
    video_id: str,
    oembed_base: str = "https://www.youtube.com/oembed",
) -> tuple[str | None, str | None]:
    """Resolve a YouTube video's title and channel name via public oEmbed."""
    url = f"{oembed_base}?url=https://www.youtube.com/watch?v={quote(video_id, safe='')}&format=json"
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None, None
            data = await resp.json(content_type=None)
            if isinstance(data, dict):
                return data.get("title"), data.get("author_name")
    except Exception:
        logger.debug("Best-effort step failed", exc_info=True)
        return None, None
    return None, None


async def get_youtube_now_playing(
    ip: str,
    *,
    dial_port: int = 8080,
    lounge_base: str = "https://www.youtube.com/api/lounge",
    oembed_base: str = "https://www.youtube.com/oembed",
    timeout: float = 10.0,
) -> YouTubeNowPlaying | None:
    """Query the currently playing YouTube video on the TV via DIAL screenId + Lounge."""
    host = format_host_for_url(ip)
    dial_url = f"http://{host}:{dial_port}/ws/apps/YouTube"

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout, sock_read=5.0)
        ) as session:
            # 1. Read DIAL <screenId> from the TV over the local network
            async with session.get(dial_url) as resp:
                if resp.status != 200:
                    return None
                screen_id = _extract_dial_screen_id(await resp.text())
            if not screen_id:
                return None

            # 2. Exchange screenId for a temporary Lounge token
            async with session.post(
                f"{lounge_base}/pairing/get_lounge_token_batch",
                data={"screen_ids": screen_id},
            ) as resp:
                if resp.status != 200:
                    return None
                tok_data = await resp.json(content_type=None)
            screens = tok_data.get("screens") if isinstance(tok_data, dict) else None
            if not screens or not isinstance(screens, list):
                return None
            lounge_token = screens[0].get("loungeToken")
            if not lounge_token:
                return None

            base_params = {
                "device": "REMOTE_CONTROL",
                "id": "tv-cast-status",
                "name": "TV Cast",
                "mdx-version": "3",
                "loungeIdToken": lounge_token,
                "VER": "8",
                "v": "2",
                "CVER": "1",
                "app": "youtube-desktop",
            }

            # 3. Initial bind (yields SID, gsessionid, and sometimes playlistModified)
            async with session.post(
                f"{lounge_base}/bc/bind",
                params={**base_params, "RID": "1"},
                data={"count": "0"},
            ) as resp:
                if resp.status != 200:
                    return None
                init_events = _parse_lounge_chunks(await resp.text())

            sid = next(
                (args[0] for name, args in init_events if name == "c" and args), None
            )
            gsid = next(
                (args[0] for name, args in init_events if name == "S" and args), None
            )

            video_id: str | None = None
            list_id: str | None = None
            current_time: float | None = None
            duration: float | None = None
            state: str | None = None

            def _apply_event(name: str, args: list[Any]) -> bool:
                nonlocal video_id, list_id, current_time, duration, state
                data = args[0] if args and isinstance(args[0], dict) else None
                if not data:
                    return False
                if name in ("nowPlaying", "onStateChange"):
                    if data.get("videoId"):
                        video_id = str(data["videoId"])
                    if data.get("listId"):
                        list_id = str(data["listId"])
                    if data.get("currentTime") is not None:
                        current_time = _parse_float(data.get("currentTime"))
                    if data.get("duration") is not None:
                        duration = _parse_float(data.get("duration"))
                    if data.get("state") is not None:
                        state = str(data["state"])
                    return name == "nowPlaying" and video_id is not None
                if name == "playlistModified":
                    if data.get("videoId") and not video_id:
                        video_id = str(data["videoId"])
                    if data.get("listId") and not list_id:
                        list_id = str(data["listId"])
                return False

            got_now_playing = False
            for name, args in init_events:
                if _apply_event(name, args):
                    got_now_playing = True

            # 4. Ask the TV for live nowPlaying (timestamp + playing/paused state)
            if sid and gsid and not got_now_playing:

                async def _listen_for_now_playing() -> None:
                    async with session.get(
                        f"{lounge_base}/bc/bind",
                        params={
                            **base_params,
                            "SID": sid,
                            "gsessionid": gsid,
                            "RID": "rpc",
                            "CI": "0",
                            "AID": str(len(init_events)),
                            "TYPE": "xmlhttp",
                        },
                    ) as stream_resp:
                        buf = ""
                        async for raw_line in stream_resp.content:
                            buf += raw_line.decode("utf-8", errors="replace")
                            if "nowPlaying" in buf or "onStateChange" in buf:
                                for ev_name, ev_args in _parse_lounge_chunks(buf):
                                    if _apply_event(ev_name, ev_args):
                                        return
                                # Also try line-level JSON array parsing for streamed events
                                for line in buf.splitlines():
                                    line = line.strip().lstrip(",")
                                    if line.startswith("[") and (
                                        "nowPlaying" in line or "onStateChange" in line
                                    ):
                                        with contextlib.suppress(json.JSONDecodeError):
                                            parsed = json.loads(line)
                                            items = (
                                                parsed
                                                if (
                                                    parsed
                                                    and isinstance(parsed[0], list)
                                                )
                                                else [parsed]
                                            )
                                            for item in items:
                                                if (
                                                    isinstance(item, list)
                                                    and len(item) == 2
                                                    and isinstance(item[1], list)
                                                    and item[1]
                                                    and _apply_event(
                                                        str(item[1][0]), item[1][1:]
                                                    )
                                                ):
                                                    return

                listener = asyncio.create_task(_listen_for_now_playing())
                try:
                    await asyncio.sleep(0.15)
                    with contextlib.suppress(Exception):
                        async with session.post(
                            f"{lounge_base}/bc/bind",
                            params={
                                **base_params,
                                "SID": sid,
                                "gsessionid": gsid,
                                "RID": "2",
                                "AID": str(len(init_events)),
                            },
                            data={
                                "count": "1",
                                "ofs": "0",
                                "req0__sc": "getNowPlaying",
                            },
                        ):
                            pass
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(listener, timeout=3.5)
                finally:
                    if not listener.done():
                        listener.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await listener

            if not video_id:
                return None

            title, author = await _fetch_youtube_oembed(
                session, video_id, oembed_base=oembed_base
            )
            return YouTubeNowPlaying(
                video_id=video_id,
                title=title,
                author=author,
                current_time=current_time,
                duration=duration,
                state=state,
                list_id=list_id,
                screen_id=screen_id,
            )
    except Exception:
        logger.debug("Best-effort step failed", exc_info=True)
        return None


async def _send_lounge_command(
    ip: str,
    command: str,
    fields: dict[str, str] | None = None,
    *,
    screen_id: str | None = None,
    client_id: str = "tv-cast-remote",
    dial_port: int = 8080,
    lounge_base: str = "https://www.youtube.com/api/lounge",
    timeout: float = 5.0,
) -> bool:
    """Send one YouTube Lounge remote-control command (e.g. setPlaylist, stopVideo) to the TV."""
    host = format_host_for_url(ip)
    dial_url = f"http://{host}:{dial_port}/ws/apps/YouTube"

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            with contextlib.suppress(Exception):
                async with session.get(dial_url) as resp:
                    if resp.status == 200:
                        fresh = _extract_dial_screen_id(await resp.text())
                        if fresh:
                            screen_id = fresh
            if not screen_id:
                return False

            async with session.post(
                f"{lounge_base}/pairing/get_lounge_token_batch",
                data={"screen_ids": screen_id},
            ) as resp:
                if resp.status != 200:
                    return False
                tok_data = await resp.json(content_type=None)
            screens = tok_data.get("screens") if isinstance(tok_data, dict) else None
            if not screens:
                return False
            lounge_token = screens[0].get("loungeToken")
            if not lounge_token:
                return False

            base_params = {
                "device": "REMOTE_CONTROL",
                "id": client_id,
                "name": "TV Cast",
                "mdx-version": "3",
                "loungeIdToken": lounge_token,
                "VER": "8",
                "v": "2",
                "CVER": "1",
                "app": "youtube-desktop",
            }
            async with session.post(
                f"{lounge_base}/bc/bind",
                params={**base_params, "RID": "1"},
                data={"count": "0"},
            ) as resp:
                if resp.status != 200:
                    return False
                init_events = _parse_lounge_chunks(await resp.text())

            sid = next(
                (args[0] for name, args in init_events if name == "c" and args), None
            )
            gsid = next(
                (args[0] for name, args in init_events if name == "S" and args), None
            )
            if not sid or not gsid:
                return False

            form: dict[str, str] = {"count": "1", "ofs": "0", "req0__sc": command}
            for key, value in (fields or {}).items():
                form[f"req0_{key}"] = value

            async with session.post(
                f"{lounge_base}/bc/bind",
                params={
                    **base_params,
                    "SID": sid,
                    "gsessionid": gsid,
                    "RID": "2",
                    "AID": str(len(init_events)),
                },
                data=form,
            ) as resp:
                return 200 <= resp.status < 300
    except Exception:
        logger.debug("Best-effort step failed", exc_info=True)
        return False


async def resume_youtube_via_lounge(
    ip: str,
    yt: YouTubeNowPlaying,
    *,
    dial_port: int = 8080,
    lounge_base: str = "https://www.youtube.com/api/lounge",
    timeout: float = 5.0,
) -> bool:
    """Resume a previously playing YouTube video at its saved timestamp via Lounge."""
    fields: dict[str, str] = {"videoId": yt.video_id}
    if yt.current_time is not None and yt.current_time > 0:
        fields["currentTime"] = f"{yt.current_time:.1f}"
    if yt.list_id:
        fields["listId"] = yt.list_id
    return await _send_lounge_command(
        ip,
        "setPlaylist",
        fields,
        screen_id=yt.screen_id,
        client_id="tv-cast-restore",
        dial_port=dial_port,
        lounge_base=lounge_base,
        timeout=timeout,
    )


async def stop_youtube_on_tv(
    ip: str,
    *,
    api_port: int = 8001,
    dial_port: int = 8080,
    lounge_base: str = "https://www.youtube.com/api/lounge",
    timeout: float = 5.0,
) -> bool:
    """Stop the video playing in the TV's YouTube app (Lounge stopVideo, then close the app)."""
    stopped = await _send_lounge_command(
        ip,
        "stopVideo",
        client_id="tv-cast-stop",
        dial_port=dial_port,
        lounge_base=lounge_base,
        timeout=timeout,
    )
    if stopped:
        return True

    host = format_host_for_url(ip)
    for url in (
        f"http://{host}:{api_port}/api/v2/applications/{YOUTUBE_APP_ID}",
        f"http://{host}:{dial_port}/ws/apps/YouTube/run",
    ):
        with contextlib.suppress(Exception):
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=timeout)
                ) as session,
                session.delete(url) as resp,
            ):
                if 200 <= resp.status < 300:
                    return True
    return False


async def play_youtube_on_tv(
    ip: str,
    video_id: str,
    *,
    samsung_token: str | None = None,
    api_port: int = 8001,
    dial_port: int = 8080,
    lounge_base: str = "https://www.youtube.com/api/lounge",
    oembed_base: str = "https://www.youtube.com/oembed",
    ws_scheme: str = "wss",
    ws_port: int = 8002,
    launch_wait: float = 6.0,
    timeout: float = 5.0,
) -> bool:
    """Play a YouTube video natively in the TV's YouTube app via DIAL + Lounge.

    Opens the YouTube app first if it is not already running, waits briefly for
    its DIAL ``<screenId>``, and dispatches ``setPlaylist``.
    """
    host = format_host_for_url(ip)
    dial_url = f"http://{host}:{dial_port}/ws/apps/YouTube"

    async def _get_screen_id(session: aiohttp.ClientSession) -> str | None:
        with contextlib.suppress(Exception):
            async with session.get(dial_url) as resp:
                if resp.status == 200:
                    return _extract_dial_screen_id(await resp.text())
        return None

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            screen_id = await _get_screen_id(session)
            if not screen_id:
                # Launch YouTube app via Samsung REST (:8001), WS (:8002), or DIAL (:8080)
                launched = False
                with contextlib.suppress(Exception):
                    async with session.post(
                        f"http://{host}:{api_port}/api/v2/applications/111299001912"
                    ) as resp:
                        launched = 200 <= resp.status < 300
                if not launched and samsung_token:
                    launched = await _launch_app_via_ws(
                        ip,
                        "111299001912",
                        samsung_token,
                        scheme=ws_scheme,
                        ws_port=ws_port,
                        timeout=timeout,
                    )
                if not launched:
                    with contextlib.suppress(Exception):
                        async with session.post(dial_url) as resp:
                            launched = 200 <= resp.status < 300
                if not launched:
                    return False

                deadline = asyncio.get_running_loop().time() + launch_wait
                while asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.5)
                    screen_id = await _get_screen_id(session)
                    if screen_id:
                        break
                if not screen_id:
                    return False

            title, author = await _fetch_youtube_oembed(
                session, video_id, oembed_base=oembed_base
            )
    except Exception:
        logger.debug("Best-effort step failed", exc_info=True)
        return False

    ok = await resume_youtube_via_lounge(
        ip,
        YouTubeNowPlaying(video_id=video_id, current_time=0.0, screen_id=screen_id),
        dial_port=dial_port,
        lounge_base=lounge_base,
        timeout=timeout,
    )
    if ok:
        label = f"{title} — {author}" if title and author else (title or video_id)
        print(f"▶️  Playing natively in YouTube app: {label}")
    return ok


async def get_tv_state(
    ip: str,
    av_transport: Any | None = None,
    *,
    api_port: int = 8001,
    apps: dict[str, str] | None = None,
    extra_apps: dict[str, str] | None = None,
    fetch_youtube: bool = True,
    timeout: float = 2.0,
) -> TVStateSnapshot:
    """Inspect a Samsung TV's current power state, foreground app, and DLNA status."""
    snapshot = TVStateSnapshot(device_ip=ip)
    host = format_host_for_url(ip)
    base_url = f"http://{host}:{api_port}"
    if apps is not None:
        app_map = dict(apps)
    elif extra_apps:
        # When the TV's installed apps have already been cached (--tv-apps),
        # probe only those apps to avoid unnecessary requests to the Smart Hub.
        app_map = dict(extra_apps)
    else:
        app_map = dict(KNOWN_SAMSUNG_APPS)

    client_timeout = aiohttp.ClientTimeout(total=timeout)
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_APP_PROBES)
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            # 1. General device info (power state, model name).
            # If :8001/api/v2/ is unreachable (e.g., non-Samsung renderer), skip app probes
            # so casting never stalls waiting on dozens of dropped connections.
            samsung_api_reachable = False
            with contextlib.suppress(Exception):
                async with session.get(f"{base_url}/api/v2/") as resp:
                    if resp.status == 200:
                        samsung_api_reachable = True
                        info = await resp.json(content_type=None)
                        device_info = (
                            info.get("device", {}) if isinstance(info, dict) else {}
                        )
                        snapshot.power_state = device_info.get("PowerState")
                        snapshot.model_name = device_info.get(
                            "modelName"
                        ) or device_info.get("name")

            # 2. Query apps with bounded concurrency and an overall cap
            # Keep whatever probes finished before the overall cap instead of discarding all.
            results: list[tuple[str, str, bool, bool] | None] = []
            if samsung_api_reachable and app_map:
                probe_tasks = [
                    asyncio.create_task(
                        _fetch_app_status(session, base_url, app_id, name, semaphore)
                    )
                    for app_id, name in app_map.items()
                ]
                try:
                    done, _ = await asyncio.wait(
                        probe_tasks, timeout=max(timeout * 3.0, 5.0)
                    )
                finally:
                    # Cancel stragglers on timeout and when the caller is cancelled.
                    pending = [t for t in probe_tasks if not t.done()]
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                for task in probe_tasks:
                    if (
                        task in done
                        and not task.cancelled()
                        and task.exception() is None
                    ):
                        results.append(task.result())
            seen_running_ids: set[str] = set()
            for item in results:
                if item is None:
                    continue
                app_id, name, running, visible = item
                if running and app_id not in seen_running_ids:
                    seen_running_ids.add(app_id)
                    snapshot.running_apps.append((app_id, name))
                if visible and snapshot.visible_app_id is None:
                    snapshot.visible_app_id = app_id
                    snapshot.visible_app_name = name
    except Exception:
        logger.debug("Best-effort step failed", exc_info=True)

    # 3. Query active YouTube video via DIAL + Lounge when YouTube is in foreground
    if fetch_youtube and snapshot.visible_app_id == "111299001912":
        snapshot.youtube = await get_youtube_now_playing(ip)

    # 4. Query UPnP AVTransport state if connected
    if av_transport is not None:
        with contextlib.suppress(Exception):
            t_info = await av_transport.action("GetTransportInfo").async_call(
                InstanceID=0
            )
            snapshot.dlna_state = t_info.get("CurrentTransportState")
        with contextlib.suppress(Exception):
            m_info = await av_transport.action("GetMediaInfo").async_call(InstanceID=0)
            uri = m_info.get("CurrentURI") or None
            meta = m_info.get("CurrentURIMetaData") or None
            snapshot.dlna_uri = uri
            snapshot.dlna_metadata = meta
            snapshot.dlna_title = _extract_didl_title(meta)

    return snapshot


async def _launch_app_via_ws(
    ip: str,
    app_id: str,
    token: str,
    *,
    scheme: str = "wss",
    ws_port: int = 8002,
    timeout: float = 5.0,
    confirm_timeout: float = 2.0,
) -> bool:
    """Launch/foreground a Tizen app using authenticated WebSocket ed.apps.launch."""
    url = _build_remote_ws_url(ip, token=token, scheme=scheme, port=ws_port)
    ws_kwargs: dict[str, Any] = {
        "timeout": aiohttp.ClientWSTimeout(ws_close=3.0),
    }
    if scheme == "wss":
        ws_kwargs["ssl"] = False

    action_type = "NATIVE_LAUNCH" if app_id.startswith("org.tizen.") else "DEEP_LINK"
    try:
        async with (
            aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as session,
            session.ws_connect(url, **ws_kwargs) as ws,
        ):
            msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
            if msg.type != aiohttp.WSMsgType.TEXT:
                return False
            payload = json.loads(msg.data)
            if payload.get("event") != "ms.channel.connect":
                return False
            await ws.send_json(
                {
                    "method": "ms.channel.emit",
                    "params": {
                        "event": "ed.apps.launch",
                        "to": "host",
                        "data": {
                            "appId": app_id,
                            "action_type": action_type,
                        },
                    },
                }
            )
            return await _await_launch_confirmation(ws, confirm_timeout)
    except Exception:
        logger.debug("Best-effort step failed", exc_info=True)
        return False


async def _await_launch_confirmation(ws: Any, confirm_timeout: float) -> bool:
    """Wait for the TV to acknowledge ed.apps.launch.

    Returns True when the TV confirms the launch, False on an explicit error. Some
    firmware never replies; in that case the launch is treated as sent but unconfirmed.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + confirm_timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
        except TimeoutError:
            break
        if msg.type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.ERROR,
        ):
            break
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        event = payload.get("event")
        if event == "ms.error":
            return False
        if event == "ed.apps.launch":
            return str(payload.get("data")) in ("200", "True", "true")
    print("   ⚠️  Launch request sent; TV did not confirm it")
    return True


async def restore_tv_state(
    snapshot: TVStateSnapshot | None,
    av_transport: Any | None = None,
    *,
    api_port: int = 8001,
    samsung_token: str | None = None,
    ws_scheme: str = "wss",
    ws_port: int = 8002,
    timeout: float = 3.0,
) -> bool:
    """Restore the TV to the foreground app or DLNA stream captured in snapshot."""
    if snapshot is None or not snapshot.has_restorable_state:
        return False

    if snapshot.visible_app_id:
        host = format_host_for_url(snapshot.device_ip)
        url = f"http://{host}:{api_port}/api/v2/applications/{snapshot.visible_app_id}"
        app_label = snapshot.visible_app_name or snapshot.visible_app_id
        print(f"🔄 Restoring {app_label}...")
        restored = False
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=timeout)
                ) as session,
                session.post(url) as resp,
            ):
                if 200 <= resp.status < 300:
                    restored = True
        except Exception:
            logger.debug("Best-effort step failed", exc_info=True)

        # Fallback to authenticated WebSocket ed.apps.launch when REST is unavailable
        if not restored and samsung_token:
            restored = await _launch_app_via_ws(
                snapshot.device_ip,
                snapshot.visible_app_id,
                samsung_token,
                scheme=ws_scheme,
                ws_port=ws_port,
                timeout=timeout,
            )

        if restored:
            print(f"   ✅ Restored {app_label}")
            # If YouTube was actively playing a video, resume at the saved timestamp.
            if (
                snapshot.visible_app_id == "111299001912"
                and snapshot.youtube is not None
                and snapshot.youtube.state == "1"
            ):
                await asyncio.sleep(1.5)
                resumed = await resume_youtube_via_lounge(
                    snapshot.device_ip, snapshot.youtube
                )
                if resumed:
                    title = snapshot.youtube.title or snapshot.youtube.video_id
                    pos = _format_mmss(snapshot.youtube.current_time)
                    print(f"   ▶️  Resumed YouTube video: {title} at {pos}")
            return True

        print(f"   ⚠️  Could not restore {app_label}")
        return False

    if (
        snapshot.dlna_state == "PLAYING"
        and snapshot.dlna_uri
        and av_transport is not None
    ):
        label = snapshot.dlna_title or snapshot.dlna_uri
        print(f"🔄 Restoring previous DLNA stream ({label})...")
        try:
            set_uri = av_transport.action("SetAVTransportURI")
            await set_uri.async_call(
                InstanceID=0,
                CurrentURI=snapshot.dlna_uri,
                CurrentURIMetaData=snapshot.dlna_metadata or "",
            )
            await asyncio.sleep(0.3)
            play = av_transport.action("Play")
            await play.async_call(InstanceID=0, Speed="1")
            print("   ✅ Restored previous DLNA stream")
            return True
        except Exception as e:
            print(f"   ⚠️  Could not restore DLNA stream: {e}")
            return False

    return False


def _build_remote_ws_url(
    ip: str,
    client_name: str = "TV Cast",
    token: str | None = None,
    *,
    scheme: str = "wss",
    port: int = 8002,
) -> str:
    """Build the Samsung WebSocket remote control URL."""
    host = format_host_for_url(ip)
    b64_name = quote(
        base64.b64encode(client_name.encode("utf-8")).decode("ascii"), safe=""
    )
    url = f"{scheme}://{host}:{port}/api/v2/channels/samsung.remote.control?name={b64_name}"
    if token:
        url += f"&token={quote(token, safe='')}"
    return url


async def pair_and_fetch_installed_apps(
    ip: str,
    token: str | None = None,
    *,
    client_name: str = "TV Cast",
    scheme: str = "wss",
    ws_port: int = 8002,
    pair_timeout: float = 45.0,
) -> tuple[str | None, dict[str, str]]:
    """Connect to Samsung's WebSocket remote API and silently fetch all installed apps.

    Returns ``(auth_token, installed_apps)`` where ``installed_apps`` maps ``appId -> name``.
    On first run without a token, the TV displays a one-time 'Allow' prompt.
    """
    url = _build_remote_ws_url(
        ip, client_name=client_name, token=token, scheme=scheme, port=ws_port
    )
    if not token:
        print(
            "📺 Connecting to TV — check your TV screen and select 'Allow' to pair..."
        )

    new_token = token
    installed_apps: dict[str, str] = {}

    # Samsung Tizen TVs use a self-signed TLS certificate on local port 8002.
    # Omit WebSocket heartbeat during pairing so the TV does not time out while
    # waiting for the user to click 'Allow' on screen.
    ws_kwargs: dict[str, Any] = {
        "timeout": aiohttp.ClientWSTimeout(ws_close=5.0),
    }
    if scheme == "wss":
        ws_kwargs["ssl"] = False

    async with (
        aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(sock_connect=5.0)
        ) as session,
        session.ws_connect(url, **ws_kwargs) as ws,
    ):
        connected = False
        deadline = asyncio.get_running_loop().time() + pair_timeout

        while not connected:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for TV pairing approval")
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
            except TimeoutError as exc:
                raise TimeoutError("Timed out waiting for TV pairing approval") from exc
            if msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                raise RuntimeError(
                    "TV closed WebSocket connection before pairing completed"
                )
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue

            payload = json.loads(msg.data)
            event = payload.get("event")
            if event == "ms.channel.connect":
                data = payload.get("data") or {}
                if isinstance(data, dict) and data.get("token"):
                    new_token = str(data["token"])
                connected = True
            elif event in ("ms.channel.unauthorized", "ms.channel.timeOut"):
                raise PermissionError(f"TV rejected pairing ({event})")

        # Request complete installed app list silently (does not open any app on screen).
        await ws.send_json(
            {
                "method": "ms.channel.emit",
                "params": {
                    "event": "ed.installedApp.get",
                    "to": "host",
                },
            }
        )

        app_deadline = asyncio.get_running_loop().time() + 3.0
        while True:
            remaining = app_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
            except TimeoutError:
                break
            if msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                break
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue

            payload = json.loads(msg.data)
            if payload.get("event") == "ed.installedApp.get":
                raw_apps = (payload.get("data") or {}).get("data") or []
                for entry in raw_apps:
                    if not isinstance(entry, dict):
                        continue
                    app_id = entry.get("appId")
                    name = entry.get("name")
                    if app_id and name:
                        installed_apps[str(app_id)] = str(name)
                break

    return new_token, installed_apps


async def discover_installed_apps_via_rest(
    ip: str,
    *,
    api_port: int = 8001,
    catalog: dict[str, str] | None = None,
    timeout: float = 3.0,
) -> list[tuple[str, str, bool, bool]]:
    """Probe Samsung's local REST API for installed apps from the known catalog.

    Returns a list of ``(app_id, name, running, visible)`` tuples for apps
    installed on the TV (HTTP 200). Works without pairing on all Tizen models.
    """
    host = format_host_for_url(ip)
    base_url = f"http://{host}:{api_port}"
    app_catalog = catalog if catalog is not None else KNOWN_SAMSUNG_APPS

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_APP_PROBES)
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout)
    ) as session:
        results = await asyncio.gather(
            *(
                _fetch_app_status(session, base_url, app_id, name, semaphore)
                for app_id, name in app_catalog.items()
            )
        )

    found: dict[str, tuple[str, str, bool, bool]] = {}
    for item in results:
        if item is not None:
            found[item[0]] = item
    return sorted(found.values(), key=lambda x: x[1].lower())


def format_tv_state(snapshot: TVStateSnapshot) -> str:
    """Format a TVStateSnapshot for human-readable CLI output."""
    lines = []
    if snapshot.model_name:
        lines.append(f"📺 TV: {snapshot.model_name} ({snapshot.device_ip})")
    else:
        lines.append(f"📺 TV: {snapshot.device_ip}")

    if snapshot.power_state:
        lines.append(f"   Power: {snapshot.power_state}")

    if snapshot.visible_app_name:
        lines.append(
            f"   Foreground app: {snapshot.visible_app_name} ({snapshot.visible_app_id})"
        )
    else:
        lines.append(
            "   Foreground app: none detected (Home screen, HDMI input, or unknown app)"
        )

    if snapshot.youtube is not None:
        yt = snapshot.youtube
        title = yt.title or yt.video_id
        by = f" — {yt.author}" if yt.author else ""
        lines.append(f"   YouTube video: {title}{by}")
        pos = _format_mmss(yt.current_time)
        dur = _format_mmss(yt.duration)
        lines.append(f"   Playback: {yt.state_label} ({pos} / {dur})")
        lines.append(f"   URL: {yt.url}")

    if snapshot.running_apps:
        bg_apps = [
            name
            for app_id, name in snapshot.running_apps
            if app_id != snapshot.visible_app_id
        ]
        if bg_apps:
            lines.append(f"   Background apps: {', '.join(bg_apps)}")

    if snapshot.dlna_state:
        dlna_line = f"   DLNA state: {snapshot.dlna_state}"
        if snapshot.dlna_title:
            dlna_line += f" ({snapshot.dlna_title})"
        lines.append(dlna_line)

    return "\n".join(lines)
