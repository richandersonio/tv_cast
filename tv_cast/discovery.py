"""Device discovery via DLNA, mDNS, and network scanning."""

import asyncio
import concurrent.futures
import ipaddress
import logging
import platform
import re
import subprocess
import threading
import time
from typing import Any, TypeGuard
from urllib.parse import urljoin, urlparse

from .utils import get_hostname, get_local_ip, get_local_subnet, ping_host

logger = logging.getLogger(__name__)


def _is_usable_host_ip(ip: str | None) -> TypeGuard[str]:
    """Return True if IP is a unicast, non-multicast, non-broadcast, non-loopback address."""
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_multicast or addr.is_loopback or addr.is_unspecified:
        return False
    return not (
        isinstance(addr, ipaddress.IPv4Address) and str(addr) == "255.255.255.255"
    )


async def discover_dlna_devices(timeout: int = 5) -> list[dict[str, Any]]:
    """Discover DLNA Media Renderer devices on the network."""
    from async_upnp_client.aiohttp import AiohttpRequester
    from async_upnp_client.client_factory import UpnpFactory
    from async_upnp_client.search import async_search

    devices: list[dict[str, Any]] = []
    seen: set[str] = set()
    requester = AiohttpRequester()
    factory = UpnpFactory(requester)

    async def on_response(response: dict[str, Any]) -> None:
        location = response.get("location", "")
        if location and location not in seen:
            seen.add(location)

            try:
                parsed = urlparse(location)
                ip = parsed.hostname
                port = parsed.port or (443 if parsed.scheme == "https" else 80)

                try:
                    device = await factory.async_create_device(location)
                    name = device.friendly_name or f"Unknown ({ip})"

                    av_service = None
                    for service in device.services.values():
                        if "AVTransport" in str(service.service_type):
                            av_service = service
                            break

                    if av_service is not None:
                        control_url = None
                        raw_control_url = getattr(av_service, "control_url", None)
                        if raw_control_url:
                            control_url = urljoin(location, raw_control_url)

                        entry: dict[str, Any] = {
                            "name": name,
                            "ip": ip,
                            "port": port,
                            "location": location,
                            "type": "dlna",
                            "castable": True,
                        }
                        if control_url:
                            entry["control_url"] = control_url

                        devices.append(entry)
                        print(f"   📺 {name} ({ip})")
                except Exception:
                    logger.debug("Best-effort step failed", exc_info=True)
            except (ValueError, AttributeError):
                pass

    search_kwargs: dict[str, Any] = {
        "search_target": "urn:schemas-upnp-org:device:MediaRenderer:1",
        "timeout": timeout,
        "async_callback": on_response,
    }
    local_ip = get_local_ip()
    if _is_usable_host_ip(local_ip):
        search_kwargs["source"] = (local_ip, 0)

    try:
        await async_search(**search_kwargs)
    except Exception:
        if "source" in search_kwargs:
            search_kwargs.pop("source", None)
            try:
                await async_search(**search_kwargs)
            except Exception:
                logger.debug("Best-effort step failed", exc_info=True)

    return devices


_MDNS_TYPE_PRIORITY = {
    "unknown": 0,
    "web": 1,
    "computer": 2,
    "file-share": 2,
    "printer": 3,
    "homekit": 3,
    "apple": 3,
    "spotify": 4,
    "airplay": 5,
    "chromecast": 5,
}


def _classify_mdns_service(type_: str) -> tuple[str, str]:
    """Return (device_type, icon) for an mDNS service type."""
    if "_airplay" in type_ or "_raop" in type_:
        return "airplay", "🍎"
    if "_googlecast" in type_:
        return "chromecast", "📺"
    if "_spotify" in type_:
        return "spotify", "🎵"
    if "_homekit" in type_ or "_hap" in type_:
        return "homekit", "🏠"
    if "_companion" in type_ or "_sleep-proxy" in type_:
        return "apple", "🍎"
    if "_smb" in type_ or "_afp" in type_:
        return "file-share", "📁"
    if "_printer" in type_ or "_ipp" in type_:
        return "printer", "🖨️"
    if "_ssh" in type_ or "_workstation" in type_:
        return "computer", "💻"
    if "_http" in type_:
        return "web", "🌐"
    return "unknown", "📱"


def discover_mdns_devices(timeout: int = 5) -> list[dict[str, Any]]:
    """Discover devices via mDNS/Bonjour."""
    from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf

    devices_by_ip: dict[str, dict[str, Any]] = {}
    lock = threading.Lock()

    service_types = [
        "_airplay._tcp.local.",
        "_raop._tcp.local.",
        "_googlecast._tcp.local.",
        "_spotify-connect._tcp.local.",
        "_homekit._tcp.local.",
        "_hap._tcp.local.",
        "_companion-link._tcp.local.",
        "_sleep-proxy._udp.local.",
        "_smb._tcp.local.",
        "_afpovertcp._tcp.local.",
        "_http._tcp.local.",
        "_ipp._tcp.local.",
        "_printer._tcp.local.",
        "_ssh._tcp.local.",
        "_workstation._tcp.local.",
        "_device-info._tcp.local.",
    ]

    class MyListener(ServiceListener):
        def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            try:
                info = zc.get_service_info(type_, name)
                if not info:
                    return

                ipv4_addresses = info.parsed_addresses(IPVersion.V4Only)
                if not ipv4_addresses:
                    return
                ip = ipv4_addresses[0]

                device_type, icon = _classify_mdns_service(type_)

                friendly_name = name.split(".")[0] if name else f"Device ({ip})"
                if info.properties:
                    raw_model = info.properties.get(b"model", b"")
                    model = (
                        raw_model.decode("utf-8", errors="ignore")
                        if isinstance(raw_model, bytes)
                        else str(raw_model)
                    )
                    if model:
                        friendly_name = f"{friendly_name} ({model})"

                # tv-cast only supports DLNA casting; mDNS-only services are informational.
                candidate = {
                    "name": friendly_name,
                    "ip": ip,
                    "port": info.port,
                    "type": device_type,
                    "castable": False,
                    "service": type_,
                }

                with lock:
                    existing = devices_by_ip.get(ip)
                    if existing is None:
                        devices_by_ip[ip] = candidate
                        print(f"   {icon} {friendly_name} ({ip})")
                    elif _MDNS_TYPE_PRIORITY.get(
                        device_type, 0
                    ) > _MDNS_TYPE_PRIORITY.get(existing.get("type", "unknown"), 0):
                        devices_by_ip[ip] = candidate
            except (AttributeError, ValueError, IndexError, OSError):
                pass

        def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            pass

        def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            pass

    zc = Zeroconf()
    try:
        listener = MyListener()

        browsers = []
        for service_type in service_types:
            try:
                browser = ServiceBrowser(zc, service_type, listener)
                browsers.append(browser)
            except Exception:
                logger.debug("Best-effort step failed", exc_info=True)

        time.sleep(timeout)
    finally:
        zc.close()

    with lock:
        return list(devices_by_ip.values())


def get_mac_vendor(mac: str) -> str | None:
    """Get vendor name from MAC address prefix."""
    vendors = {
        "00:1A:79": "Tesla",
        "4C:FC:AA": "Tesla",
        "00:17:F2": "Apple",
        "3C:06:30": "Apple",
        "B8:27:EB": "Raspberry Pi",
        "B8:17:C2": "Samsung",
        "78:BD:BC": "Samsung",
        "8C:71:F8": "Samsung",
        "00:1A:11": "Google",
        "F4:F5:D8": "Google",
        "00:50:F2": "Microsoft",
        "B4:2E:99": "Amazon",
        "00:04:20": "Roku",
        "00:0E:08": "Sony",
        "A0:D3:7A": "LG",
        "00:1B:63": "HP",
        "00:21:9B": "Dell",
    }

    if not mac:
        return None

    mac_prefix = mac.upper().replace("-", ":")[0:8]
    return vendors.get(mac_prefix)


def _parse_arp_output(output: str) -> list[dict[str, str]]:
    """Parse IP and MAC pairs from arp or `ip neigh` output."""
    devices = []
    for line in output.split("\n"):
        ip_match = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", line)
        mac_match = re.search(
            r"([0-9a-fA-F]{1,2}[:-][0-9a-fA-F]{1,2}[:-][0-9a-fA-F]{1,2}[:-][0-9a-fA-F]{1,2}[:-][0-9a-fA-F]{1,2}[:-][0-9a-fA-F]{1,2})",
            line,
        )

        if ip_match and mac_match:
            ip = ip_match.group(1)
            mac = mac_match.group(1).upper().replace("-", ":")

            if (
                "(incomplete)" in line.lower()
                or "failed" in line.lower()
                or mac == "FF:FF:FF:FF:FF:FF"
            ):
                continue

            devices.append({"ip": ip, "mac": mac})
    return devices


def scan_arp_table() -> list[dict[str, str]]:
    """Get devices from the system ARP/neighbor table."""
    system = platform.system().lower()
    if system in ("darwin", "windows"):
        commands = [["arp", "-a"]]
    else:
        commands = [["arp", "-n"], ["ip", "neigh"]]

    for cmd in commands:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5, check=False
            )
            if result.returncode == 0:
                return _parse_arp_output(result.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue

    return []


def discover_network_hosts(
    timeout: int = 10,
    partial_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Discover all hosts on the local network via ARP + ping sweep."""

    devices: list[dict[str, Any]] = (
        partial_results if partial_results is not None else []
    )
    seen_ips: set[str] = {d["ip"] for d in devices if d.get("ip")}
    deadline = time.monotonic() + max(1, timeout)

    print("   📋 Checking ARP table...")

    arp_devices = scan_arp_table()

    for dev in arp_devices:
        ip = dev["ip"]
        if ip not in seen_ips and _is_usable_host_ip(ip):
            seen_ips.add(ip)

            hostname = get_hostname(ip) if time.monotonic() < deadline else None
            vendor = get_mac_vendor(dev.get("mac", ""))

            if hostname:
                name = hostname.split(".")[0]
            elif vendor:
                name = f"{vendor} Device"
            else:
                name = "Device"

            device_type = "unknown"
            if vendor:
                vendor_lower = vendor.lower()
                if "tesla" in vendor_lower:
                    device_type = "tesla"
                elif "apple" in vendor_lower:
                    device_type = "apple"
                elif "samsung" in vendor_lower:
                    device_type = "samsung"

            devices.append(
                {
                    "name": f"{name} ({ip})",
                    "ip": ip,
                    "mac": dev.get("mac"),
                    "type": device_type,
                    "vendor": vendor,
                    "hostname": hostname,
                    "castable": False,
                }
            )

    # Ping sweep (skip if local IP fell back to loopback or time budget exhausted)
    my_ip = get_local_ip()
    remaining = deadline - time.monotonic()
    if not _is_usable_host_ip(my_ip) or remaining <= 0:
        return devices

    subnet = get_local_subnet()
    print(f"   🔍 Ping sweep on {subnet}.0/24...")

    ips_to_scan = [
        f"{subnet}.{i}"
        for i in range(1, 255)
        if f"{subnet}.{i}" not in seen_ips and f"{subnet}.{i}" != my_ip
    ]

    def check_host(ip: str) -> str | None:
        if ping_host(ip, timeout=0.5):
            return ip
        return None

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=50)
    try:
        futures = {executor.submit(check_host, ip): ip for ip in ips_to_scan}
        try:
            for future in concurrent.futures.as_completed(futures, timeout=remaining):
                try:
                    found_ip = future.result(timeout=0.1)
                    if found_ip and found_ip not in seen_ips:
                        ip = found_ip
                        seen_ips.add(ip)
                        hostname = (
                            get_hostname(ip) if time.monotonic() < deadline else None
                        )
                        name = hostname.split(".")[0] if hostname else "Device"

                        devices.append(
                            {
                                "name": f"{name} ({ip})",
                                "ip": ip,
                                "type": "unknown",
                                "hostname": hostname,
                                "castable": False,
                            }
                        )
                except Exception:
                    logger.debug("Best-effort step failed", exc_info=True)
        except concurrent.futures.TimeoutError:
            pass
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return devices


async def discover_all_devices(timeout: int = 8) -> list[dict[str, Any]]:
    """Discover all devices on the network (ARP, ping, mDNS, DLNA)."""

    print("🔍 Scanning network for all devices...")
    print()

    all_devices: list[dict[str, Any]] = []
    seen_ips: set[str] = set()

    loop = asyncio.get_running_loop()

    effective_timeout = max(1, timeout)
    dlna_timeout = max(1, min(effective_timeout, 4))
    mdns_timeout = max(1, min(effective_timeout, 4))
    network_timeout = max(1, effective_timeout)

    mdns_devices: list[dict[str, Any]] = []
    partial_network_devices: list[dict[str, Any]] = []
    network_devices: list[dict[str, Any]] = []
    dlna_devices: list[dict[str, Any]] = []

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        mdns_future = loop.run_in_executor(pool, discover_mdns_devices, mdns_timeout)
        network_future = loop.run_in_executor(
            pool, discover_network_hosts, network_timeout, partial_network_devices
        )

        try:
            dlna_devices = await asyncio.wait_for(
                discover_dlna_devices(dlna_timeout),
                timeout=dlna_timeout + 2,
            )
        except Exception:
            logger.debug("Best-effort step failed", exc_info=True)

        try:
            mdns_devices = await asyncio.wait_for(mdns_future, timeout=mdns_timeout + 2)
        except Exception:
            logger.debug("Best-effort step failed", exc_info=True)

        try:
            network_devices = await asyncio.wait_for(
                network_future, timeout=network_timeout + 2
            )
        except Exception:
            logger.debug("Best-effort step failed", exc_info=True)
            network_devices = list(partial_network_devices)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    # Merge results
    for dev in dlna_devices:
        ip = dev.get("ip")
        if _is_usable_host_ip(ip) and ip not in seen_ips:
            seen_ips.add(ip)
            all_devices.append(dev)

    for dev in mdns_devices:
        ip = dev.get("ip")
        if _is_usable_host_ip(ip) and ip not in seen_ips:
            seen_ips.add(ip)
            all_devices.append(dev)

    for dev in network_devices:
        ip = dev.get("ip")
        if not _is_usable_host_ip(ip):
            continue
        if ip not in seen_ips:
            seen_ips.add(ip)
            all_devices.append(dev)
        else:
            for existing in all_devices:
                if existing.get("ip") == ip:
                    if dev.get("mac") and not existing.get("mac"):
                        existing["mac"] = dev.get("mac")
                    if dev.get("vendor") and not existing.get("vendor"):
                        existing["vendor"] = dev.get("vendor")
                    break

    print(f"\n✅ Found {len(all_devices)} device(s)")

    return all_devices
