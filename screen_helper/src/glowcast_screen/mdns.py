"""The mDNS announcement `_glowcast._tcp.local.` that Home Assistant finds (#109)."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any

import ifaddr
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from glowcast_screen import PROTOCOL

SERVICE_TYPE = "_glowcast._tcp.local."
# A DNS label holds at most 63 bytes; the instance name is "<name> (<id prefix>)".
_MAX_NAME = 50


def lan_addresses() -> list[str]:
    """The computer's IPv4 addresses, without loopback and link-local ones."""
    found = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if not ip.is_IPv4:
                continue
            address = ipaddress.IPv4Address(ip.ip)
            if not (address.is_loopback or address.is_link_local):
                found.append(str(address))
    return sorted(set(found))


def default_route_address() -> str | None:
    """The address the computer reaches the network with, or None without a route.

    Connecting a UDP socket sends nothing; it only asks the OS which address it
    would use. 192.0.2.1 is a documentation address, routed like any other.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
    except OSError:
        return None
    ip = ipaddress.IPv4Address(address)
    return None if ip.is_loopback or ip.is_link_local or ip.is_unspecified else address


def announce_addresses(
    route: Callable[[], str | None] = default_route_address,
    lan: Callable[[], list[str]] = lan_addresses,
) -> list[str]:
    """The address(es) to announce: only the default-route one when there is one.

    Home Assistant uses the first announced address. Virtual adapters (Hyper-V,
    WSL, Docker, VPN) have addresses Home Assistant cannot reach, so they are
    left out when the OS says which address faces the network.
    """
    address = route()
    return [address] if address else lan()


def service_info(
    helper_id: str, name: str, port: int, addresses: list[str]
) -> ServiceInfo:
    short = name.encode()[:_MAX_NAME].decode(errors="ignore").strip() or "Glowcast"
    return ServiceInfo(
        SERVICE_TYPE,
        f"{short} ({helper_id[:6]}).{SERVICE_TYPE}",
        port=port,
        properties={"id": helper_id, "name": name, "protocol": str(PROTOCOL)},
        server=f"glowcast-{helper_id[:12]}.local.",
        parsed_addresses=addresses,
    )


class Announcer:
    def __init__(self, zeroconf_factory: Callable[[], Any] = AsyncZeroconf) -> None:
        self._factory = zeroconf_factory
        self._zeroconf: Any = None
        self._info: ServiceInfo | None = None

    async def start(self, info: ServiceInfo) -> None:
        self._zeroconf = self._factory()
        await (
            await self._zeroconf.async_register_service(info, allow_name_change=True)
        )
        self._info = info

    async def stop(self) -> None:
        """Withdraw the announcement (goodbye packets) and close; safe to call twice."""
        zeroconf, self._zeroconf = self._zeroconf, None
        info, self._info = self._info, None
        if zeroconf is None:
            return
        try:
            if info is not None:
                await (await zeroconf.async_unregister_service(info))
        finally:
            await zeroconf.async_close()
