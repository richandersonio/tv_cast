import asyncio
import functools
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.request

import aiohttp
import pytest
from aiohttp import web

from tv_cast import casting, config, conversion


def test_safe_hls_handler_serves_hls_only_without_chdir(tmp_path):
    (tmp_path / "playlist.m3u8").write_text(
        "#EXTM3U\n#EXT-X-ENDLIST\n", encoding="utf-8"
    )
    (tmp_path / "segment000.ts").write_bytes(b"ts-bytes")
    (tmp_path / "secret.txt").write_text("top-secret", encoding="utf-8")

    cwd_before = os.getcwd()
    handler = functools.partial(casting.QuietHandler, directory=str(tmp_path))
    server = casting.ReusableThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        # Allowed .m3u8 file
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/playlist.m3u8") as resp:
            assert resp.status == 200
            assert resp.headers["Accept-Ranges"] == "none"
            assert resp.headers["Content-Type"] == "application/vnd.apple.mpegurl"
            assert b"#EXTM3U" in resp.read()

        # Allowed .ts file
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/segment000.ts") as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"] == "video/MP2T"
            assert resp.read() == b"ts-bytes"

        # Directory listing must be forbidden
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/")
        assert exc_info.value.code == 403
        exc_info.value.close()

        # Non-HLS files must be forbidden
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/secret.txt")
        assert exc_info.value.code == 403
        exc_info.value.close()
    finally:
        server.shutdown()
        server.server_close()

    assert os.getcwd() == cwd_before


def test_resolve_control_url_handles_default_port_ipv6_and_saved_control_url():
    assert (
        casting._resolve_control_url(
            {"ip": "192.168.1.50", "location": "http://192.168.1.50/dmr"}
        )
        == "http://192.168.1.50:80/upnp/control/AVTransport1"
    )
    assert (
        casting._resolve_control_url({"ip": "::1", "port": 9197})
        == "http://[::1]:9197/upnp/control/AVTransport1"
    )
    assert (
        casting._resolve_control_url(
            {
                "ip": "192.168.1.50",
                "location": "http://192.168.1.50:9197/dmr",
                "control_url": "http://192.168.1.50:9197/custom/AVT",
            }
        )
        == "http://192.168.1.50:9197/custom/AVT"
    )


@pytest.mark.asyncio
async def test_cast_video_cancellation_signals_converter_and_uncancels(
    tmp_path, monkeypatch
):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"data")

    started = threading.Event()
    saw_cancel = threading.Event()

    def slow_convert(path, cancel_event=None):
        started.set()
        if cancel_event is not None and cancel_event.wait(timeout=5.0):
            saw_cancel.set()
        return None

    monkeypatch.setattr(casting, "convert_to_hls", slow_convert)

    old = config.get_current_device()
    config.set_current_device({"name": "TV", "ip": "192.168.1.50"})
    try:
        task = asyncio.create_task(casting.cast_video(str(video), interactive=False))
        await asyncio.to_thread(started.wait, 2.0)
        task.cancel()
        result = await task
        assert result is False
        assert saw_cancel.wait(timeout=2.0)
        assert not task.cancelled()
    finally:
        config.set_current_device(old)


def test_cleanup_on_exit_noop_when_idle_and_stops_when_active(monkeypatch):
    sent_urls = []

    class DummyResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=2):
        sent_urls.append(req.full_url)
        return DummyResp()

    monkeypatch.setattr(casting.urllib.request, "urlopen", fake_urlopen)

    # Inactive -> no HTTP call
    casting._active_playback_control_url = None
    casting.cleanup_on_exit()
    assert sent_urls == []

    # Active -> sends Stop and clears state
    casting._active_playback_control_url = (
        "http://192.168.1.50:9197/upnp/control/AVTransport1"
    )
    casting.cleanup_on_exit()
    assert sent_urls == ["http://192.168.1.50:9197/upnp/control/AVTransport1"]
    assert casting._active_playback_control_url is None


@pytest.mark.asyncio
async def test_cast_video_native_youtube_stops_after_duration(monkeypatch):
    called_native = []
    called_download = False
    stopped = []
    restored = []

    async def fake_play_native(ip, video_id, samsung_token=None):
        called_native.append((ip, video_id))
        return True

    def fake_download(*args, **kwargs):
        nonlocal called_download
        called_download = True
        return None

    async def fake_app_state(device, restore):
        return casting.TVStateSnapshot(
            device_ip=device["ip"]
        )  # Home screen: nothing to restore

    async def fake_stop(ip):
        stopped.append(ip)
        return True

    async def fake_restore(snapshot, **kwargs):
        restored.append(snapshot)
        return True

    monkeypatch.setattr(casting, "play_youtube_on_tv", fake_play_native)
    monkeypatch.setattr(casting, "download_youtube", fake_download)
    monkeypatch.setattr(casting, "_capture_app_state", fake_app_state)
    monkeypatch.setattr(casting, "stop_youtube_on_tv", fake_stop)
    monkeypatch.setattr(casting, "restore_tv_state", fake_restore)

    old = config.get_current_device()
    config.set_current_device({"name": "TV", "ip": "192.168.4.22"})
    try:
        ok = await casting.cast_video(
            "https://www.youtube.com/watch?v=yvsoeyqCIU8", duration=1
        )
        assert ok is True
        assert called_native == [("192.168.4.22", "yvsoeyqCIU8")]
        assert not called_download
        # Even with nothing to restore, the video must stop when the duration ends.
        assert stopped == ["192.168.4.22"]
        assert restored == []
    finally:
        config.set_current_device(old)


@pytest.mark.asyncio
async def test_cast_video_native_youtube_waits_for_ctrl_c_and_restores(monkeypatch):
    stopped = []
    restored = []
    previous = casting.TVStateSnapshot(
        device_ip="192.168.4.22",
        visible_app_id="3201907018807",
        visible_app_name="Netflix",
    )

    async def fake_play_native(ip, video_id, samsung_token=None):
        return True

    async def fake_app_state(device, restore):
        return previous

    async def fake_stop(ip):
        stopped.append(ip)
        return True

    async def fake_restore(snapshot, **kwargs):
        restored.append(snapshot)
        return True

    monkeypatch.setattr(casting, "play_youtube_on_tv", fake_play_native)
    monkeypatch.setattr(casting, "_capture_app_state", fake_app_state)
    monkeypatch.setattr(casting, "stop_youtube_on_tv", fake_stop)
    monkeypatch.setattr(casting, "restore_tv_state", fake_restore)

    old = config.get_current_device()
    config.set_current_device({"name": "TV", "ip": "192.168.4.22"})
    try:
        task = asyncio.create_task(
            casting.cast_video(
                "https://www.youtube.com/watch?v=yvsoeyqCIU8", duration=0
            )
        )
        await asyncio.sleep(0.2)
        assert not task.done(), "duration=0 should play until Ctrl+C"
        task.cancel()  # Simulates asyncio.run's Ctrl+C handling
        assert await task is True
        assert stopped == ["192.168.4.22"]
        assert restored == [previous]
    finally:
        config.set_current_device(old)


@pytest.mark.asyncio
async def test_cast_video_falls_back_to_yt_dlp_when_native_youtube_fails(monkeypatch):
    called_download = []

    async def fake_play_native(ip, video_id, samsung_token=None):
        return False

    def fake_download(url, cancel_event=None):
        called_download.append(url)
        return None  # Stop cleanly after proving fallback ran

    async def fake_app_state(device, restore):
        return None

    monkeypatch.setattr(casting, "play_youtube_on_tv", fake_play_native)
    monkeypatch.setattr(casting, "download_youtube", fake_download)
    monkeypatch.setattr(casting, "_capture_app_state", fake_app_state)

    old = config.get_current_device()
    config.set_current_device({"name": "TV", "ip": "192.168.4.22"})
    try:
        ok = await casting.cast_video("https://www.youtube.com/watch?v=yvsoeyqCIU8")
        assert ok is False
        assert called_download == ["https://www.youtube.com/watch?v=yvsoeyqCIU8"]
    finally:
        config.set_current_device(old)


@pytest.mark.asyncio
async def test_capture_previous_state_keeps_pre_native_app(monkeypatch):
    """A failed native attempt that opened YouTube must not replace the user's app."""
    pre_native = casting.TVStateSnapshot(
        device_ip="192.168.4.22",
        visible_app_id="3201907018807",
        visible_app_name="Netflix",
    )

    async def fake_get_tv_state(ip, av_transport=None, extra_apps=None):
        return casting.TVStateSnapshot(
            device_ip=ip, visible_app_id="111299001912", visible_app_name="YouTube"
        )

    monkeypatch.setattr(casting, "get_tv_state", fake_get_tv_state)

    snapshot = await casting._capture_previous_state(
        {"ip": "192.168.4.22"}, av_transport=None, restore=True, app_state=pre_native
    )
    assert snapshot is not None
    assert snapshot.visible_app_id == "3201907018807"
    assert snapshot.visible_app_name == "Netflix"


@pytest.mark.asyncio
async def test_cast_video_fails_early_when_no_device_selected(tmp_path):
    old = config.get_current_device()
    config.set_current_device(None)
    try:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"data")
        assert await casting.cast_video(str(video), interactive=False) is False
    finally:
        config.set_current_device(old)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg required for full local DLNA casting integration test",
)
@pytest.mark.asyncio
async def test_full_local_dlna_cast_and_stop_integration(tmp_path, monkeypatch):
    """End-to-end integration test with real ffmpeg HLS + local HTTP server + mock UPnP TV."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(conversion, "HLS_CACHE_DIR", str(tmp_path / "hls"))
    monkeypatch.setattr(casting, "HTTP_PORT", 0)
    monkeypatch.setattr(casting, "get_local_ip", lambda: "127.0.0.1")

    events = []
    fetched_playlist = []

    scpd_xml = """<?xml version="1.0" encoding="utf-8"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <actionList>
    <action>
      <name>SetAVTransportURI</name>
      <argumentList>
        <argument><name>InstanceID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_InstanceID</relatedStateVariable></argument>
        <argument><name>CurrentURI</name><direction>in</direction><relatedStateVariable>AVTransportURI</relatedStateVariable></argument>
        <argument><name>CurrentURIMetaData</name><direction>in</direction><relatedStateVariable>AVTransportURIMetaData</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>Play</name>
      <argumentList>
        <argument><name>InstanceID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_InstanceID</relatedStateVariable></argument>
        <argument><name>Speed</name><direction>in</direction><relatedStateVariable>TransportPlaySpeed</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>Stop</name>
      <argumentList>
        <argument><name>InstanceID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_InstanceID</relatedStateVariable></argument>
      </argumentList>
    </action>
  </actionList>
  <serviceStateTable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_InstanceID</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>AVTransportURI</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>AVTransportURIMetaData</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>TransportPlaySpeed</name><dataType>string</dataType></stateVariable>
  </serviceStateTable>
</scpd>"""

    async def handle_dmr(request):
        xml = """<?xml version="1.0" encoding="utf-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>Mock Samsung TV</friendlyName>
    <manufacturer>Samsung</manufacturer>
    <modelName>MockTV</modelName>
    <UDN>uuid:mock-tv-1234</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:AVTransport</serviceId>
        <SCPDURL>/AVTransport.xml</SCPDURL>
        <controlURL>/custom/control/AVTransport1</controlURL>
        <eventSubURL>/event/AVTransport1</eventSubURL>
      </service>
    </serviceList>
  </device>
</root>"""
        return web.Response(text=xml, content_type="text/xml")

    async def handle_scpd(request):
        return web.Response(text=scpd_xml, content_type="text/xml")

    async def handle_control(request):
        soap_action = request.headers.get("SOAPACTION", "")
        body = await request.text()
        if "SetAVTransportURI" in soap_action:
            events.append("SetAVTransportURI")
            # Extract CurrentURI and verify the mock TV can fetch the HLS playlist live!
            start = body.index("<CurrentURI>") + len("<CurrentURI>")
            end = body.index("</CurrentURI>")
            uri = body[start:end]
            async with (
                aiohttp.ClientSession() as session,
                session.get(uri, timeout=aiohttp.ClientTimeout(total=3)) as resp,
            ):
                fetched_playlist.append(await resp.text())
            resp_action = "SetAVTransportURIResponse"
        elif "Play" in soap_action:
            events.append("Play")
            resp_action = "PlayResponse"
        elif "Stop" in soap_action:
            events.append("Stop")
            resp_action = "StopResponse"
        else:
            resp_action = "UnknownResponse"

        soap_resp = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body><u:{resp_action} xmlns:u="urn:schemas-upnp-org:service:AVTransport:1"/></s:Body>
</s:Envelope>"""
        return web.Response(text=soap_resp, content_type="text/xml")

    app = web.Application()
    app.router.add_get("/dmr", handle_dmr)
    app.router.add_get("/AVTransport.xml", handle_scpd)
    app.router.add_post("/custom/control/AVTransport1", handle_control)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    tv_port = site._server.sockets[0].getsockname()[1]

    # Create a real synthetic video
    sample_mp4 = tmp_path / "integration.mp4"
    await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(sample_mp4),
        ],
        check=True,
        capture_output=True,
    )

    old_device = config.get_current_device()
    config.set_current_device(
        {
            "name": "Mock Samsung TV",
            "ip": "127.0.0.1",
            "port": tv_port,
            "location": f"http://127.0.0.1:{tv_port}/dmr",
            "type": "dlna",
            "castable": True,
        }
    )

    try:
        ok = await casting.cast_video(str(sample_mp4), duration=1, interactive=False)
        assert ok is True
        assert "SetAVTransportURI" in events
        assert "Play" in events
        assert events[-1] == "Stop"
        assert fetched_playlist and "#EXT-X-ENDLIST" in fetched_playlist[0]
        # Verify discovered control_url was persisted on the device
        assert config.get_current_device()["control_url"] == (
            f"http://127.0.0.1:{tv_port}/custom/control/AVTransport1"
        )

        # Also test stop_playback end-to-end
        stopped = await casting.stop_playback(interactive=False)
        assert stopped is True
    finally:
        config.set_current_device(old_device)
        await runner.cleanup()
