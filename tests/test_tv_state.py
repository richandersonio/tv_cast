import pytest
from aiohttp import web

from tv_cast import cli, tv_state


@pytest.mark.asyncio
async def test_get_and_restore_tv_state_with_mock_samsung_api():
    restored_apps: list[str] = []

    async def handle_info(request):
        return web.json_response({"device": {"PowerState": "on", "modelName": "QN90C"}})

    async def handle_app_get(request):
        app_id = request.match_info["app_id"]
        if app_id == "111299001912":
            return web.json_response(
                {"id": app_id, "name": "YouTube", "running": True, "visible": True}
            )
        if app_id == "3201907018807":
            return web.json_response(
                {"id": app_id, "name": "Netflix", "running": True, "visible": False}
            )
        return web.json_response({"code": 404, "message": "Not found"}, status=404)

    async def handle_app_post(request):
        app_id = request.match_info["app_id"]
        restored_apps.append(app_id)
        return web.Response(text="true", status=200)

    app = web.Application()
    app.router.add_get("/api/v2/", handle_info)
    app.router.add_get("/api/v2/applications/{app_id}", handle_app_get)
    app.router.add_post("/api/v2/applications/{app_id}", handle_app_post)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        snapshot = await tv_state.get_tv_state(
            "127.0.0.1",
            api_port=port,
            apps={"111299001912": "YouTube", "3201907018807": "Netflix"},
            fetch_youtube=False,
        )
        assert snapshot.power_state == "on"
        assert snapshot.model_name == "QN90C"
        assert snapshot.visible_app_id == "111299001912"
        assert snapshot.visible_app_name == "YouTube"
        assert snapshot.has_restorable_state is True

        formatted = tv_state.format_tv_state(snapshot)
        assert "YouTube" in formatted
        assert "Netflix" in formatted

        ok = await tv_state.restore_tv_state(snapshot, api_port=port)
        assert ok is True
        assert restored_apps == ["111299001912"]

        discovered = await tv_state.discover_installed_apps_via_rest(
            "127.0.0.1",
            api_port=port,
            catalog={
                "111299001912": "YouTube",
                "3201907018807": "Netflix",
                "999": "Missing",
            },
        )
        assert discovered == [
            ("3201907018807", "Netflix", True, False),
            ("111299001912", "YouTube", True, True),
        ]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_pair_and_fetch_installed_apps_via_websocket():
    async def handle_ws(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json(
            {"event": "ms.channel.connect", "data": {"token": "tok-123"}}
        )
        async for msg in ws:
            payload = msg.json()
            if payload.get("params", {}).get("event") == "ed.installedApp.get":
                await ws.send_json(
                    {
                        "event": "ed.installedApp.get",
                        "data": {
                            "data": [
                                {"appId": "111299001912", "name": "YouTube"},
                                {"appId": "3201512006963", "name": "Plex"},
                            ]
                        },
                    }
                )
                break
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/api/v2/channels/samsung.remote.control", handle_ws)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        token, apps = await tv_state.pair_and_fetch_installed_apps(
            "127.0.0.1", scheme="ws", ws_port=port, pair_timeout=5.0
        )
        assert token == "tok-123"
        assert apps == {"111299001912": "YouTube", "3201512006963": "Plex"}
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_restore_tv_state_falls_back_to_ws_launch_when_rest_fails():
    launched: list[dict] = []

    async def handle_ws(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"event": "ms.channel.connect", "data": {}})
        async for msg in ws:
            payload = msg.json()
            if payload.get("params", {}).get("event") == "ed.apps.launch":
                launched.append(payload["params"]["data"])
                break
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/api/v2/channels/samsung.remote.control", handle_ws)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        snapshot = tv_state.TVStateSnapshot(
            device_ip="127.0.0.1",
            visible_app_id="111299001912",
            visible_app_name="YouTube",
        )
        ok = await tv_state.restore_tv_state(
            snapshot,
            api_port=1,  # Unreachable REST port -> forces WS fallback
            samsung_token="tok-123",
            ws_scheme="ws",
            ws_port=port,
            timeout=2.0,
        )
        assert ok is True
        assert launched == [{"appId": "111299001912", "action_type": "DEEP_LINK"}]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_get_and_resume_youtube_now_playing_via_dial_and_lounge():
    resumed_payloads: list[dict[str, str]] = []

    async def handle_dial(request):
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<service xmlns="urn:dial-multiscreen-org:schemas:dial">'
            "<name>YouTube</name><state>running</state>"
            "<additionalData><screenId>screen-abc-123</screenId></additionalData>"
            "</service>"
        )
        return web.Response(text=xml, content_type="application/xml")

    async def handle_token(request):
        data = await request.post()
        assert data["screen_ids"] == "screen-abc-123"
        return web.json_response({"screens": [{"loungeToken": "lounge-tok-999"}]})

    async def handle_bind_post(request):
        data = await request.post()
        if data.get("req0__sc") == "setPlaylist":
            resumed_payloads.append(dict(data))
            return web.Response(text="")
        payload = (
            '[[0,["c","SID1","",8]],'
            '[1,["S","GSID1"]],'
            '[2,["nowPlaying",{"videoId":"dgMChp20gnk","listId":"LIST1",'
            '"duration":"2671.6","currentTime":"394.2","state":"1"}]]]'
        )
        return web.Response(text=f"{len(payload)}\n{payload}\n")

    async def handle_oembed(request):
        return web.json_response(
            {"title": "Test Video Title", "author_name": "Test Channel"}
        )

    app = web.Application()
    app.router.add_get("/ws/apps/YouTube", handle_dial)
    app.router.add_post("/api/lounge/pairing/get_lounge_token_batch", handle_token)
    app.router.add_post("/api/lounge/bc/bind", handle_bind_post)
    app.router.add_get("/oembed", handle_oembed)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    try:
        yt = await tv_state.get_youtube_now_playing(
            "127.0.0.1",
            dial_port=port,
            lounge_base=f"{base}/api/lounge",
            oembed_base=f"{base}/oembed",
        )
        assert yt is not None
        assert yt.video_id == "dgMChp20gnk"
        assert yt.title == "Test Video Title"
        assert yt.author == "Test Channel"
        assert yt.state_label == "playing"
        assert yt.current_time == pytest.approx(394.2)

        ok = await tv_state.resume_youtube_via_lounge(
            "127.0.0.1",
            yt,
            dial_port=port,
            lounge_base=f"{base}/api/lounge",
        )
        assert ok is True
        assert resumed_payloads[0]["req0_videoId"] == "dgMChp20gnk"
        assert resumed_payloads[0]["req0_currentTime"] == "394.2"
        assert resumed_payloads[0]["req0_listId"] == "LIST1"

        played = await tv_state.play_youtube_on_tv(
            "127.0.0.1",
            "eNBqkc7V1nU",
            api_port=port,
            dial_port=port,
            lounge_base=f"{base}/api/lounge",
            oembed_base=f"{base}/oembed",
        )
        assert played is True
        assert resumed_payloads[-1]["req0_videoId"] == "eNBqkc7V1nU"
    finally:
        await runner.cleanup()


def test_cli_parses_tv_state_and_no_restore_flags():
    parser = cli.build_parser()
    args = parser.parse_args(["--tv-state"])
    assert args.tv_state is True
    assert args.no_restore is False

    apps_args = parser.parse_args(["--tv-apps"])
    assert apps_args.tv_apps is True

    args2 = parser.parse_args(["--rio-live", "-d", "10", "--no-restore"])
    assert args2.rio_live is True
    assert args2.no_restore is True


async def _start_ws_launch_server(reply):
    """Mock Samsung WS endpoint; `reply` is the JSON sent after ed.apps.launch (or None)."""

    async def handle_ws(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"event": "ms.channel.connect", "data": {}})
        async for msg in ws:
            payload = msg.json()
            if payload.get("params", {}).get("event") == "ed.apps.launch":
                if reply is not None:
                    await ws.send_json(reply)
                break
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/api/v2/channels/samsung.remote.control", handle_ws)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({"event": "ed.apps.launch", "data": 200, "from": "host"}, True),
        ({"event": "ed.apps.launch", "data": 404, "from": "host"}, False),
        ({"event": "ms.error", "data": {"message": "unknown app"}}, False),
    ],
)
async def test_launch_app_via_ws_honours_tv_confirmation(reply, expected):
    runner, port = await _start_ws_launch_server(reply)
    try:
        ok = await tv_state._launch_app_via_ws(
            "127.0.0.1", "3201907018807", "tok", scheme="ws", ws_port=port, timeout=2.0
        )
        assert ok is expected
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_restore_reports_failure_when_tv_rejects_ws_launch(capsys):
    runner, port = await _start_ws_launch_server({"event": "ms.error", "data": {}})
    try:
        snapshot = tv_state.TVStateSnapshot(
            device_ip="127.0.0.1",
            visible_app_id="3201907018807",
            visible_app_name="Netflix",
        )
        ok = await tv_state.restore_tv_state(
            snapshot,
            api_port=1,
            samsung_token="tok",
            ws_scheme="ws",
            ws_port=port,
            timeout=2.0,
        )
        assert ok is False
        out = capsys.readouterr().out
        assert "✅ Restored" not in out
        assert "Could not restore Netflix" in out
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_get_tv_state_keeps_partial_app_results_when_probes_time_out():
    import asyncio

    async def handle_info(request):
        return web.json_response({"device": {"PowerState": "on"}})

    async def handle_app_get(request):
        app_id = request.match_info["app_id"]
        if app_id == "3201907018807":
            return web.json_response(
                {"id": app_id, "name": "Netflix", "running": True, "visible": True}
            )
        await asyncio.sleep(30)  # Unresponsive probe
        return web.json_response({}, status=404)

    app = web.Application()
    app.router.add_get("/api/v2/", handle_info)
    app.router.add_get("/api/v2/applications/{app_id}", handle_app_get)
    runner = web.AppRunner(app, shutdown_timeout=0.1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    apps = {"3201907018807": "Netflix"}
    apps.update({f"slow{i}": f"Slow {i}" for i in range(10)})
    try:
        snapshot = await tv_state.get_tv_state(
            "127.0.0.1", api_port=port, apps=apps, fetch_youtube=False, timeout=1.0
        )
        assert snapshot.visible_app_id == "3201907018807"
        assert snapshot.has_restorable_state is True
    finally:
        await runner.cleanup()


async def test_get_tv_state_cancels_app_probes_when_caller_is_cancelled():
    import asyncio

    probes_started = asyncio.Event()
    probes_cancelled: list[str] = []

    async def handle_info(request):
        return web.json_response({"device": {"PowerState": "on"}})

    async def handle_app_get(request):
        probes_started.set()
        await asyncio.sleep(30)  # Never answers before the caller gives up
        return web.json_response({}, status=404)

    app = web.Application()
    app.router.add_get("/api/v2/", handle_info)
    app.router.add_get("/api/v2/applications/{app_id}", handle_app_get)
    runner = web.AppRunner(app, shutdown_timeout=0.1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    real_fetch = tv_state._fetch_app_status

    async def tracking_fetch(session, base_url, app_id, name, semaphore):
        try:
            return await real_fetch(session, base_url, app_id, name, semaphore)
        except asyncio.CancelledError:
            probes_cancelled.append(app_id)
            raise

    apps = {f"app{i}": f"App {i}" for i in range(3)}
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(tv_state, "_fetch_app_status", tracking_fetch)
            task = asyncio.create_task(
                tv_state.get_tv_state(
                    "127.0.0.1",
                    api_port=port,
                    apps=apps,
                    fetch_youtube=False,
                    timeout=10.0,
                )
            )
            await asyncio.wait_for(probes_started.wait(), timeout=5.0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert sorted(probes_cancelled) == sorted(apps)
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_stop_youtube_on_tv_sends_lounge_stop_then_falls_back_to_closing_app():
    commands: list[str] = []
    deleted: list[str] = []
    lounge_ok = {"value": True}

    async def handle_dial(request):
        xml = (
            '<service xmlns="urn:dial-multiscreen-org:schemas:dial">'
            "<additionalData><screenId>screen-1</screenId></additionalData></service>"
        )
        return web.Response(text=xml, content_type="application/xml")

    async def handle_token(request):
        if not lounge_ok["value"]:
            return web.json_response({}, status=500)
        return web.json_response({"screens": [{"loungeToken": "tok"}]})

    async def handle_bind(request):
        data = await request.post()
        if data.get("req0__sc"):
            commands.append(data["req0__sc"])
            return web.Response(text="")
        payload = '[[0,["c","SID1","",8]],[1,["S","GSID1"]]]'
        return web.Response(text=f"{len(payload)}\n{payload}\n")

    async def handle_delete(request):
        deleted.append(request.match_info["app_id"])
        return web.Response(text="true")

    app = web.Application()
    app.router.add_get("/ws/apps/YouTube", handle_dial)
    app.router.add_post("/api/lounge/pairing/get_lounge_token_batch", handle_token)
    app.router.add_post("/api/lounge/bc/bind", handle_bind)
    app.router.add_delete("/api/v2/applications/{app_id}", handle_delete)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    try:
        kwargs = {
            "api_port": port,
            "dial_port": port,
            "lounge_base": f"{base}/api/lounge",
        }
        assert await tv_state.stop_youtube_on_tv("127.0.0.1", **kwargs) is True
        assert commands == ["stopVideo"]
        assert deleted == []

        lounge_ok["value"] = False
        assert await tv_state.stop_youtube_on_tv("127.0.0.1", **kwargs) is True
        assert deleted == [tv_state.YOUTUBE_APP_ID]
    finally:
        await runner.cleanup()
