"""When each Home Assistant light gets its next command (#9, limits from #125).

Pure and synchronous, with the time as an argument, so it is tested without
Home Assistant. The product owner's limits, measured in #125:
- each light at most once per second;
- at most 4 commands per second per Hue bridge and per Thread network (they do
  not share capacity); with more lights on one network each gets an equal
  share, so it changes less often than once per second;
- a light of another integration has no shared network (not measured yet), so
  only the once per second applies to it.
"""

from collections import Counter
from collections.abc import Callable, Mapping

# Commands per second per network, and the shortest time between two commands
# to one light.
NETWORK_COMMANDS_PER_SECOND = 4
LIGHT_INTERVAL = 1.0
# The send loop ticks at a fixed rhythm, each tick a few milliseconds late. A
# light or a network whose time comes within this much is served on this
# tick instead of losing a whole tick.
TOLERANCE = 0.01
# A light that waited longer than this many intervals (its colour did not
# change) fades its next colour over its interval, not over the wait.
TRANSITION_GAP = 2.0
# The integrations whose lights share a network that limits commands: a Hue
# bridge (one per hue config entry) and Thread (one per matter config entry;
# all Matter lights of that entry count as one Thread network, which is the
# closest Home Assistant shows).
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
        """Initialize for {light: the networks of the commands it causes}.

        One entry per command: a light group of Home Assistant sends one
        command per member, so it lists each member's network (a network
        twice for two members on it). A light without networks is limited
        only to once per second.
        """
        self._commands = {light: Counter(nets) for light, nets in networks.items()}
        per_network: Counter[str] = Counter()
        for commands in self._commands.values():
            per_network.update(commands)
        self._interval = {
            light: max(
                [
                    LIGHT_INTERVAL,
                    *(
                        per_network[network] / NETWORK_COMMANDS_PER_SECOND
                        for network in commands
                    ),
                ]
            )
            for light, commands in self._commands.items()
        }
        self._last: dict[str, float] = {}
        self._transition: dict[str, float] = {}
        # When each network's commands of about the last second went out.
        self._sent: dict[str, list[float]] = {network: [] for network in per_network}

    def interval(self, light: str) -> float:
        """Seconds between two commands to this light: its share of the network."""
        return self._interval[light]

    def transition(self, light: str) -> float:
        """How long the light's last command should fade: its actual interval.

        The time since its command before, or its interval for the first one
        and after a wait (a colour that did not change).
        """
        return self._transition.get(light, self._interval[light])

    def due(self, now: float, ready: Callable[[str], bool]) -> list[str]:
        """The lights to send a command to now; they count as sent.

        At most one light per network per call, and never more than
        NETWORK_COMMANDS_PER_SECOND commands per network within a second
        (less TOLERANCE); a group with more members than that goes only when
        its networks had no command in the last second. A light gets a command
        at most once per its interval, and only when `ready` says so (for
        example a new colour, and no command still on its way); one that is
        not ready keeps its turn. The light that waited longest goes first.
        """
        used: set[str] = set()
        chosen: list[str] = []
        for light in sorted(
            self._commands, key=lambda light: self._last.get(light, -float("inf"))
        ):
            last = self._last.get(light)
            interval = self._interval[light]
            if last is not None and now - last < interval - TOLERANCE:
                continue
            commands = self._commands[light]
            if any(
                network in used or not self._room(network, count, now)
                for network, count in commands.items()
            ):
                continue
            if not ready(light):
                continue
            chosen.append(light)
            if last is not None and now - last < TRANSITION_GAP * interval:
                self._transition[light] = round(now - last, 2)
            else:
                self._transition[light] = interval
            self._last[light] = now
            for network, count in commands.items():
                used.add(network)
                self._sent[network].extend([now] * count)
        return chosen

    def _room(self, network: str, count: int, now: float) -> bool:
        """Whether the network takes `count` more commands now."""
        sent = self._sent[network] = [
            t for t in self._sent[network] if now - t < 1.0 - TOLERANCE
        ]
        if count > NETWORK_COMMANDS_PER_SECOND:
            return not sent
        return len(sent) + count <= NETWORK_COMMANDS_PER_SECOND
