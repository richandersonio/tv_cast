import sys
import time
from types import SimpleNamespace

import pytest

from tv_cast import discovery


def test_is_usable_host_ip_filters_multicast_broadcast_and_loopback():
    assert discovery._is_usable_host_ip("192.168.1.42")
    assert not discovery._is_usable_host_ip("224.0.0.251")
    assert not discovery._is_usable_host_ip("239.255.255.250")
    assert not discovery._is_usable_host_ip("255.255.255.255")
    assert not discovery._is_usable_host_ip("127.0.0.1")
    assert not discovery._is_usable_host_ip("not-an-ip")
    assert not discovery._is_usable_host_ip(None)


def test_discover_mdns_handles_ipv6_and_merges_services(monkeypatch):
    class FakeIPVersion:
        V4Only = "v4only"

    class FakeServiceInfo:
        def __init__(self, v4_addrs, port=8009, properties=None):
            self._v4_addrs = v4_addrs
            self.port = port
            self.properties = properties or {}

        def parsed_addresses(self, version=None):
            return list(self._v4_addrs)

    class FakeZeroconf:
        def __init__(self):
            self.closed = False

        def get_service_info(self, type_, name):
            if name.startswith("ipv6only"):
                return FakeServiceInfo([])  # IPv6-only device: no v4 addresses
            if type_ == "_http._tcp.local.":
                return FakeServiceInfo(["192.168.1.77"], port=80)
            if type_ == "_airplay._tcp.local.":
                return FakeServiceInfo(["192.168.1.77"], port=7000, properties={b"model": b"AppleTV"})
            return None

        def close(self):
            self.closed = True

    class FakeBrowser:
        def __init__(self, zc, service_type, listener):
            if service_type == "_http._tcp.local.":
                listener.add_service(zc, service_type, "ipv6only._http._tcp.local.")
                listener.add_service(zc, service_type, "LivingRoom._http._tcp.local.")
            elif service_type == "_airplay._tcp.local.":
                listener.add_service(zc, service_type, "LivingRoom._airplay._tcp.local.")

    fake_mod = SimpleNamespace(
        IPVersion=FakeIPVersion,
        Zeroconf=FakeZeroconf,
        ServiceBrowser=FakeBrowser,
        ServiceListener=object,
    )
    monkeypatch.setitem(sys.modules, "zeroconf", fake_mod)
    monkeypatch.setattr(discovery.time, "sleep", lambda _: None)

    devices = discovery.discover_mdns_devices(timeout=1)
    assert len(devices) == 1
    assert devices[0]["ip"] == "192.168.1.77"
    assert devices[0]["type"] == "airplay"
    assert devices[0]["castable"] is False  # Only DLNA is castable
    assert "AppleTV" in devices[0]["name"]


def test_scan_arp_table_falls_back_to_ip_neigh_on_linux(monkeypatch):
    monkeypatch.setattr(discovery.platform, "system", lambda: "Linux")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "arp":
            raise FileNotFoundError("arp missing")
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "192.168.1.50 dev eth0 lladdr 78:bd:bc:11:22:33 REACHABLE\n"
                "192.168.1.51 dev eth0 FAILED\n"
            ),
        )

    monkeypatch.setattr(discovery.subprocess, "run", fake_run)

    devices = discovery.scan_arp_table()
    assert calls == [["arp", "-n"], ["ip", "neigh"]]
    assert devices == [{"ip": "192.168.1.50", "mac": "78:BD:BC:11:22:33"}]


def test_discover_network_hosts_skips_ping_sweep_on_loopback(monkeypatch):
    monkeypatch.setattr(discovery, "scan_arp_table", list)
    monkeypatch.setattr(discovery, "get_local_ip", lambda: "127.0.0.1")

    ping_called = False

    def fail_if_called(*args, **kwargs):
        nonlocal ping_called
        ping_called = True
        return False

    monkeypatch.setattr(discovery, "ping_host", fail_if_called)

    assert discovery.discover_network_hosts(timeout=1) == []
    assert not ping_called


@pytest.mark.asyncio
async def test_discover_all_devices_filters_multicast_and_respects_timeout(monkeypatch):
    async def fake_dlna(timeout):
        return [
            {"name": "Samsung TV", "ip": "192.168.1.50", "type": "dlna", "castable": True},
            {"name": "SSDP", "ip": "239.255.255.250", "type": "dlna", "castable": True},
        ]

    def fake_mdns(timeout):
        return [
            {"name": "mDNS Multicast", "ip": "224.0.0.251", "type": "web", "castable": False},
            {"name": "Speaker", "ip": "192.168.1.60", "type": "spotify", "castable": False},
        ]

    def slow_network(timeout, partial_results=None):
        time.sleep(0.05)
        found = [
            {"name": "Samsung TV (192.168.1.50)", "ip": "192.168.1.50", "mac": "78:BD:BC:00:00:01", "vendor": "Samsung", "castable": False},
            {"name": "Broadcast", "ip": "255.255.255.255", "castable": False},
        ]
        if partial_results is not None:
            partial_results.extend(found)
        return found

    monkeypatch.setattr(discovery, "discover_dlna_devices", fake_dlna)
    monkeypatch.setattr(discovery, "discover_mdns_devices", fake_mdns)
    monkeypatch.setattr(discovery, "discover_network_hosts", slow_network)

    devices = await discovery.discover_all_devices(timeout=1)
    ips = {d["ip"] for d in devices}
    assert ips == {"192.168.1.50", "192.168.1.60"}
    tv = next(d for d in devices if d["ip"] == "192.168.1.50")
    assert tv["vendor"] == "Samsung"
    assert tv["castable"] is True


@pytest.mark.asyncio
async def test_discover_dlna_devices_binds_local_source_ip(monkeypatch):
    captured = {}

    async def fake_async_search(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(discovery, "get_local_ip", lambda: "192.168.1.42")
    monkeypatch.setitem(
        sys.modules,
        "async_upnp_client.search",
        SimpleNamespace(async_search=fake_async_search),
    )

    await discovery.discover_dlna_devices(timeout=2)
    assert captured["source"] == ("192.168.1.42", 0)
    assert captured["timeout"] == 2


@pytest.mark.asyncio
async def test_discover_dlna_devices_retries_without_source_on_bind_error(monkeypatch):
    calls = []

    async def fake_async_search(**kwargs):
        calls.append(dict(kwargs))
        if "source" in kwargs:
            raise OSError("Cannot assign requested address")

    monkeypatch.setattr(discovery, "get_local_ip", lambda: "192.168.1.42")
    monkeypatch.setitem(
        sys.modules,
        "async_upnp_client.search",
        SimpleNamespace(async_search=fake_async_search),
    )

    await discovery.discover_dlna_devices(timeout=2)
    assert len(calls) == 2
    assert "source" in calls[0]
    assert "source" not in calls[1]


@pytest.mark.asyncio
async def test_discover_all_devices_preserves_partial_arp_results_on_timeout(monkeypatch):
    async def empty_dlna(timeout):
        return []

    def empty_mdns(timeout):
        return []

    def hanging_network(timeout, partial_results=None):
        if partial_results is not None:
            partial_results.append(
                {"name": "ARP Host (192.168.1.88)", "ip": "192.168.1.88", "castable": False}
            )
        time.sleep(5.0)
        return partial_results or []

    monkeypatch.setattr(discovery, "discover_dlna_devices", empty_dlna)
    monkeypatch.setattr(discovery, "discover_mdns_devices", empty_mdns)
    monkeypatch.setattr(discovery, "discover_network_hosts", hanging_network)

    orig_wait_for = discovery.asyncio.wait_for

    async def fast_wait_for(aw, timeout):
        return await orig_wait_for(aw, timeout=0.1)

    monkeypatch.setattr(discovery.asyncio, "wait_for", fast_wait_for)

    devices = await discovery.discover_all_devices(timeout=1)
    assert [d["ip"] for d in devices] == ["192.168.1.88"]
