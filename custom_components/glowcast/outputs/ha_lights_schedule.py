"""When each loose light gets its next command (#9, limits from #125).

Pure and synchronous, with the time as an argument, so it is tested without
Home Assistant. The product owner's limits, measured in #125:
- each light at most once per second;
- at most 4 commands per second per Hue bridge and per Thread network (they do
  not share capacity); with more lights on one network each gets an equal
  share, so it changes less often than once per second;
- a light of another integration has no shared network (not measured yet), so
  only the once per second applies to it.
"""

from collections import deque
from collections.abc import Callable, Mapping

# Commands per second per network, and the shortest time between two commands
# to one light.
NETWORK_COMMANDS_PER_SECOND = 4
LIGHT_INTERVAL = 1.0
# The send loop ticks at a fixed rhythm; a light that is due within this much
# of its interval is sent on this tick instead of waiting a whole tick.
LIGHT_TOLERANCE = 0.01
# The integrations whose lights share a network that limits commands: a Hue
# bridge (one per hue config entry) and Thread (one per matter config entry).
SHARED_NETWORKS = ("hue", "matter")


def network_of(platform: str, config_entry_id: str | None) -> str | None:
    """The network a light's commands go through, from its integration.

    `platform` is the integration of the light's entity and `config_entry_id`
    its config entry, both from the entity registry. None: the light shares no
    limited network with other lights (any other integration).
    """
    if platform not in SHARED_NETWORKS or config_entry_id is None:
        return None
    return f"{platform}:{config_entry_id}"


class Schedule:
    """Which lights get a command now, within the rate limits."""

    def __init__(self, networks: Mapping[str, tuple[str, ...]]) -> None:
        """Initialize for {light: the networks its commands go through}.

        A light without networks is limited only to once per second; a light
        group of Home Assistant can go through several networks at once.
        """
        self._networks = dict(networks)
        lights_per_network: dict[str, int] = {}
        for light_networks in self._networks.values():
            for network in light_networks:
                lights_per_network[network] = lights_per_network.get(network, 0) + 1
        self._interval = {
            light: max(
                [
                    LIGHT_INTERVAL,
                    *(
                        lights_per_network[network] / NETWORK_COMMANDS_PER_SECOND
                        for network in light_networks
                    ),
                ]
            )
            for light, light_networks in self._networks.items()
        }
        self._last: dict[str, float] = {}
        self._sent: dict[str, deque[float]] = {
            network: deque(maxlen=NETWORK_COMMANDS_PER_SECOND)
            for network in lights_per_network
        }

    def interval(self, light: str) -> float:
        """Seconds between two commands to this light: its share of the network."""
        return self._interval[light]

    def due(self, now: float, ready: Callable[[str], bool]) -> list[str]:
        """The lights to send a command to now; they count as sent.

        At most one command per network per call, and never more than
        NETWORK_COMMANDS_PER_SECOND in any second. A light gets a command at
        most once per its interval, and only when `ready` says so (for
        example a new colour, and no command still on its way); one that is
        not ready keeps its turn. The light that waited longest goes first.
        """
        used: set[str] = set()
        chosen: list[str] = []
        for light in sorted(
            self._networks, key=lambda light: self._last.get(light, -float("inf"))
        ):
            last = self._last.get(light)
            if (
                last is not None
                and now - last < self._interval[light] - LIGHT_TOLERANCE
            ):
                continue
            networks = self._networks[light]
            if any(network in used or self._full(network, now) for network in networks):
                continue
            if not ready(light):
                continue
            chosen.append(light)
            self._last[light] = now
            for network in networks:
                used.add(network)
                self._sent[network].append(now)
        return chosen

    def _full(self, network: str, now: float) -> bool:
        """Whether the network had its commands for the last second already."""
        sent = self._sent[network]
        return len(sent) == sent.maxlen and now - sent[0] < 1.0
