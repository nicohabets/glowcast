"""GIFs of Glowcast's flows in the local demo; run through `scripts/demo.py record`.

Each flow is one function in FLOWS. It starts on a recognisable screen, calls
rec.begin() when that screen is ready (the GIF starts there), clicks with
rec.click() (a ring shows where) and calls rec.step("name") on every screen
worth a pause. With --still, every rec.step() saves a PNG instead. To add a
flow, write such a function and add it to FLOWS.

Playwright's video and Chrome's screencast record at 1x, whatever the device
scale factor, so a GIF is made of screenshots: while a flow waits, the
recorder takes them one after another, each with its time.

Files: <lang>-<theme>-<flow>.gif, or <lang>-<theme>-<flow>-<nn>-<name>.png.
"""

import argparse
import contextlib
import json
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import imageio_ffmpeg
from fake_bridge import TV_AREA_ID
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Response,
    sync_playwright,
)
from playwright.sync_api import Error as PlaywrightError

# Home Assistant's mobile view, recorded at scale factor 2: GIFs 800 px wide.
VIEWPORT = {"width": 400, "height": 860}
SCALE = 2
FPS = 10  # the GIF's frame rate; screenshots come at about 5 to 8 per second
PAUSE = 900  # ms on each step of a GIF, after it settles
LOAD_TIMEOUT = 120000  # ms; right after `start` the frontend can take a minute
MAX_GIF_BYTES = 10_000_000  # GitHub's limit for an attached image
# The dialog of setup and options flows, and its primary button. Scoped to
# the flow dialog: a closing confirm dialog would match a bare ha-dialog too.
DIALOG = "dialog-data-entry-flow ha-dialog"
PRIMARY = f"{DIALOG} ha-button[slot=primaryAction]"
CONFIRM = "dialog-box ha-dialog ha-button[slot=primaryAction]"
HASS = "document.querySelector('home-assistant').hass"
ROOT = Path(__file__).resolve().parents[2]
# The screen helper the screen flows start: a generic name, never the
# computer's own, and a fixed pairing code.
HELPER_NAME = "Test PC"
HELPER_PORT = 8488
PAIRING_CODE = "123456"
# A ring where the next click lands, removed with the click so it never shows
# on the next screen. A popover sits in the top layer, so it shows above Home
# Assistant's modal dialogs too.
RING = """([x, y]) => {
  const ring = document.createElement('div');
  ring.id = 'glowcast-demo-ring';
  ring.popover = 'manual';
  ring.style.cssText = `position: fixed; inset: auto; margin: 0; padding: 0;
    left: ${x - 20}px; top: ${y - 20}px; width: 40px; height: 40px;
    border-radius: 50%; border: 3px solid #ff4081;
    background: rgba(255, 64, 129, 0.3); pointer-events: none;`;
  document.body.appendChild(ring);
  ring.showPopover();
}"""
UNRING = "() => document.getElementById('glowcast-demo-ring')?.remove()"


class Recorder:
    """The browser page and what the flows need."""

    def __init__(
        self,
        page: Page,
        args: argparse.Namespace,
        mode: str,
        flow: str = "",
        frames_dir: Path | None = None,
    ) -> None:
        """Mode "gif" records frames in frames_dir and shows clicks, "still"
        saves a PNG of each step, "quiet" neither."""
        self.page = page
        self.args = args
        self.mode = mode
        self.flow = flow
        self.frames_dir = frames_dir
        self.frames: list[tuple[float, Path]] = []
        self.recording = False
        self.count = 0

    def begin(self) -> None:
        """The flow's first screen is ready: the GIF starts here."""
        self.recording = self.mode == "gif"
        self.step("start")

    def wait(self, ms: int) -> None:
        """Wait; while recording, take screenshots all the time."""
        if not self.recording:
            self.page.wait_for_timeout(ms)
            return
        end = time.monotonic() + ms / 1000
        while True:
            self.capture()
            if time.monotonic() >= end:
                break

    def capture(self) -> None:
        """Add a frame to the GIF."""
        assert self.frames_dir is not None
        path = self.frames_dir / f"{len(self.frames):05d}.png"
        self.page.screenshot(path=path)
        self.frames.append((time.monotonic(), path))

    def step(self, name: str) -> None:
        """Pause on this screen in a GIF, or save it as a PNG."""
        self.wait(400)
        if self.mode == "gif":
            self.wait(PAUSE)
        elif self.mode == "still":
            self.count += 1
            prefix = f"{self.args.lang}-{self.args.theme}-{self.flow}"
            path = self.args.out / f"{prefix}-{self.count:02d}-{name}.png"
            self.page.screenshot(path=path, animations="disabled")
            print(f"saved {path}")

    def click(self, locator: Locator, at: float = 0.5) -> None:
        """Click, showing first where (in a GIF).

        `at` is how far across the element to click (0 left, 1 right), for
        example a point on a slider.
        """
        position = None
        locator.scroll_into_view_if_needed()
        if box := locator.bounding_box():
            position = {"x": box["width"] * at, "y": box["height"] / 2}
            if self.mode == "gif":
                point = [box["x"] + position["x"], box["y"] + position["y"]]
                self.page.evaluate(RING, point)
                self.wait(600)
        locator.click(position=position)
        if self.mode == "gif":
            # A click that loads another page has removed the ring already.
            with contextlib.suppress(PlaywrightError):
                self.page.evaluate(UNRING)

    def goto(self, path: str) -> None:
        """Open a page of the frontend and wait until it has drawn."""
        self.page.goto(self.args.url + path)
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(1500)

    def ws(self, message: dict[str, Any]) -> Any:
        """Send a websocket command as the logged-in user."""
        return self.page.evaluate(f"m => {HASS}.callWS(m)", message)

    def localize(self, key: str) -> str:
        """A text of the frontend in the chosen language, to find a button by."""
        return self.page.evaluate(f"k => {HASS}.localize(k)", key)

    def option(self, value: str) -> Locator:
        """The item with this value in the open dropdown."""
        # Closed menus elsewhere on the page have items too, but hidden ones.
        items = self.page.locator("ha-dropdown-item")
        index = items.evaluate_all(
            "(items, value) => items.findIndex("
            "i => i.value === value && i.checkVisibility())",
            value,
        )
        if index < 0:  # nth(-1) would be the last item
            raise LookupError(f"No option {value!r} in an open dropdown")
        return items.nth(index)

    def submit(self) -> None:
        """Click the primary button of the open dialog."""
        self.click(self.page.locator(PRIMARY))
        self.wait(300)

    def press_link_button(self) -> None:
        """Press the fake bridge's link button."""
        request = urllib.request.Request(
            f"{self.args.bridge}/demo/press-link-button", data=b"", method="POST"
        )
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(request, context=context, timeout=10):
            pass


def setup(rec: Recorder) -> None:
    """The setup flow: TV, bridge, link button, pairing, area, done."""
    page = rec.page
    open_setup(rec)
    rec.click(menu_item(rec, "tv"))
    page.locator(PRIMARY).wait_for()
    rec.step("tv")
    rec.submit()
    hue_steps(rec)


def open_setup(rec: Recorder) -> None:
    """From "Do you want to set up Glowcast?" to the menu of source types."""
    page = rec.page
    rec.goto("/config/integrations/dashboard/add?domain=glowcast")
    # "Do you want to set up Glowcast?" over the integrations page.
    page.locator(CONFIRM).wait_for()
    rec.begin()
    rec.click(page.locator(CONFIRM))
    page.locator(f"{DIALOG} step-flow-menu").wait_for()
    rec.step("menu")


def menu_item(rec: Recorder, step: str) -> Locator:
    """The item of the open flow menu that goes to this step."""
    items = rec.page.locator(f"{DIALOG} step-flow-menu ha-list-item")
    index = items.evaluate_all("(items, s) => items.findIndex(i => i.step === s)", step)
    if index < 0:
        raise LookupError(f"No menu item for the step {step!r}")
    return items.nth(index)


def hue_steps(rec: Recorder) -> None:
    """The output's steps, after the source's: the output menu, bridge, link,
    area, done."""
    page = rec.page
    page.locator(f"{DIALOG} step-flow-menu").wait_for()
    rec.step("output")
    rec.click(menu_item(rec, "bridge"))
    page.locator(PRIMARY).wait_for()
    rec.step("bridge")
    rec.submit()
    rec.step("link")
    rec.submit()
    rec.step("pairing")
    rec.press_link_button()
    page.locator(f"{DIALOG} ha-picker-field").wait_for(timeout=15000)
    rec.step("area")
    rec.click(page.locator(f"{DIALOG} ha-picker-field"))
    rec.step("area-choices")
    rec.click(rec.option(TV_AREA_ID))
    rec.submit()
    rec.step("done")
    rec.submit()
    rec.step("integration")


@contextlib.contextmanager
def screen_helper() -> Iterator[dict[str, Any]]:
    """Run the screen helper with test patterns on 127.0.0.1; yield its
    `started` event (id, port).

    A fixed pairing code and its own config directory, so it never touches the
    computer's own helper id or pairings. On a loopback address it does not
    announce itself, so nothing is found on the network.
    """
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    with socket.socket() as probe:
        # As the helper's server: connections of an earlier run that are
        # still closing do not count as in use.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", HELPER_PORT))
            port = HELPER_PORT
        except OSError:
            port = 0  # another session's helper: any free port
    with tempfile.TemporaryDirectory() as config:
        process = subprocess.Popen(
            [
                uv,
                "run",
                "--project",
                str(ROOT / "screen_helper"),
                "glowcast-screen",
                "--pattern",
                "cycle",
                "--name",
                HELPER_NAME,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--non-interactive",
                "--pairing-code",
                PAIRING_CODE,
                "--json",
                "--config-dir",
                config,
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert process.stdout is not None
            started = None
            for line in process.stdout:
                event = json.loads(line)
                if event["event"] == "started":
                    started = event
                    break
            if started is None:
                raise RuntimeError("The screen helper did not start")
            # It keeps writing events (frames, patterns); read them away so a
            # full pipe never blocks it during a long recording.
            threading.Thread(
                target=process.stdout.read, name="screen helper output", daemon=True
            ).start()
            yield started
        finally:
            process.terminate()
            process.wait(10)


def fill(rec: Recorder, index: int, text: str) -> None:
    """Type into the open form's text field with this index."""
    field = rec.page.locator(f"{DIALOG} ha-form input").nth(index)
    rec.click(field)
    field.fill(text)
    rec.wait(300)


def screen_setup(rec: Recorder) -> None:
    """Set up a computer screen by hand: the address, the pairing code, up to
    the output's first step (#109).

    The output's steps are the same as for a TV (the `setup` and
    `screen-discovery` flows show them), so the GIF stays short; the flow is
    left open and aborted when the recording ends.
    """
    page = rec.page
    with screen_helper() as helper:
        open_setup(rec)
        rec.click(menu_item(rec, "screen"))
        page.locator(f"{DIALOG} ha-form input").first.wait_for()
        rec.step("address")
        fill(rec, 0, "127.0.0.1")
        if helper["port"] != HELPER_PORT:  # the form's default
            fill(rec, 1, str(helper["port"]))
        rec.step("address-filled")
        rec.submit()
        pairing_code(rec, through_output=False)
        rec.step("bridge")


def screen_discovery(rec: Recorder) -> None:
    """A discovered screen helper: Add, a wrong code, the right code, then
    the output's steps (#109)."""
    page = rec.page
    with screen_helper() as helper:
        # A discovery left by an earlier run (another helper id each run).
        for flow in rec.ws({"type": "config_entries/flow/progress"}):
            if flow["handler"] == "glowcast":
                page.evaluate(
                    f"id => {HASS}.callApi('DELETE', "
                    "`config/config_entries/flow/${id}`)",
                    flow["flow_id"],
                )
        rec.goto("/config/integrations/dashboard")
        rec.page.evaluate(
            f"data => {HASS}.callService('glowcast_demo', "
            "'discover_screen_helper', data)",
            {
                "host": "127.0.0.1",
                "port": helper["port"],
                "id": helper["id"],
                "name": HELPER_NAME,
            },
        )
        card = page.locator("ha-config-flow-card").filter(has_text=HELPER_NAME)
        card.wait_for(timeout=15000)
        rec.begin()
        rec.click(card.locator("ha-button[appearance=filled]"))
        pairing_code(rec, wrong_first=True)


def pairing_code(
    rec: Recorder, wrong_first: bool = False, through_output: bool = True
) -> None:
    """The pairing code step, then (or up to) the output's steps."""
    page = rec.page
    page.locator(f"{DIALOG} ha-form input").first.wait_for()
    rec.step("code")
    if wrong_first:
        fill(rec, 0, "000000")
        rec.submit()
        page.locator(f"{DIALOG} ha-alert").wait_for(timeout=15000)
        rec.step("wrong-code")
    fill(rec, 0, PAIRING_CODE)
    rec.step("code-filled")
    rec.submit()
    page.locator(f"{DIALOG} ha-picker-field").wait_for(timeout=15000)
    if through_output:
        hue_steps(rec)


def settings(rec: Recorder) -> None:
    """Change the update rate on the settings screen."""
    page = rec.page
    rec.goto("/config/integrations/integration/glowcast")
    rec.begin()
    configure = rec.localize("ui.panel.config.integrations.config_entry.configure")
    rec.click(page.get_by_role("button", name=configure).first)
    page.locator(f"{DIALOG} step-flow-menu").wait_for()
    rec.step("output")
    rec.click(menu_item(rec, "hue_options"))
    page.locator(PRIMARY).wait_for()
    rec.step("area")
    rec.submit()
    page.locator(PRIMARY).wait_for()
    rec.step("settings")
    rec.click(page.locator(f"{DIALOG} ha-picker-field").last)
    rec.step("update-rate")
    rec.click(rec.option("maximum"))
    rec.step("update-rate-chosen")
    rec.submit()
    rec.step("saved")


def device(rec: Recorder) -> None:
    """From the integration page to Glowcast's device page."""
    page = rec.page
    rec.goto("/config/integrations/integration/glowcast")
    rec.begin()
    name = page.evaluate(
        f"() => Object.values({HASS}.devices)"
        ".find(d => d.identifiers.some(i => i[0] === 'glowcast')).name"
    )
    rec.click(page.get_by_text(name, exact=True).first)
    page.wait_for_url("**/config/devices/device/**")
    rec.step("device")


def entities(rec: Recorder) -> None:
    """The settings as entities under Configuration on the device page (#13).

    Switches Glowcast on (the demo's fake TV source and light output keep it
    syncing), changes brightness, intensity and update rate, and the
    entertainment area that the Hue output type provides, while the switch
    stays on (a new area restarts the pipeline), and switches off.
    """
    registry = [
        entry
        for entry in rec.ws({"type": "config/entity_registry/list"})
        if entry["platform"] == "glowcast"
    ]
    ids = {
        entry["unique_id"].split("_", 1)[1]: entry["entity_id"]
        for entry in registry
        if "_" in entry["unique_id"]
    }
    # The switch's unique id is the entry id alone.
    (switch,) = [e for e in registry if e["entity_id"].startswith("switch.")]
    device = rec.ws(
        {"type": "config/entity_registry/get", "entity_id": ids["brightness"]}
    )
    # Start switched off, from the defaults and the first area, whatever an
    # earlier run left.
    for domain, service, data in (
        ("switch", "turn_off", {"entity_id": switch["entity_id"]}),
        ("number", "set_value", {"entity_id": ids["brightness"], "value": 80}),
        ("number", "set_value", {"entity_id": ids["intensity"], "value": 100}),
        (
            "select",
            "select_option",
            {"entity_id": ids["update_rate"], "option": "high"},
        ),
        (
            "select",
            "select_option",
            {"entity_id": ids["entertainment_area"], "option": "TV area"},
        ),
    ):
        rec.page.evaluate(
            f"([d, s, data]) => {HASS}.callService(d, s, data)",
            [domain, service, data],
        )
    rec.goto(f"/config/devices/device/{device['device_id']}")
    rec.begin()
    toggle = row(rec, switch["entity_id"]).locator("ha-entity-toggle")
    rec.click(toggle)
    wait_for_streaming(rec, switch["config_entry_id"])
    rec.step("syncing")
    rec.click(row(rec, ids["brightness"]).locator("ha-slider"), at=0.35)
    rec.step("brightness")
    rec.click(row(rec, ids["intensity"]).locator("ha-slider"), at=0.75)
    rec.step("intensity")
    rec.click(row(rec, ids["update_rate"]).locator("ha-select"))
    rec.step("update-rates")
    rec.click(rec.option("maximum"))
    rec.step("update-rate-chosen")
    rec.click(row(rec, ids["entertainment_area"]).locator("ha-select"))
    rec.step("areas")
    rec.click(rec.option("Whole room"))
    wait_for_streaming(rec, switch["config_entry_id"], channels=3)
    rec.step("area-chosen")
    rec.click(toggle)
    rec.step("off")


def test_lights(rec: Recorder) -> None:
    """The Test lights button under Configuration on the device page (#182).

    Pressed while Glowcast is off: the switch stays off. The demo's fake
    lights do not show colours, so the GIF shows the button and the switch.
    """
    registry = [
        entry
        for entry in rec.ws({"type": "config/entity_registry/list"})
        if entry["platform"] == "glowcast"
    ]
    (switch,) = [e for e in registry if e["entity_id"].startswith("switch.")]
    (button,) = [e for e in registry if e["unique_id"].endswith("_test_lights")]
    rec.page.evaluate(
        f"id => {HASS}.callService('switch', 'turn_off', {{entity_id: id}})",
        switch["entity_id"],
    )
    rec.goto(f"/config/devices/device/{button['device_id']}")
    rec.begin()
    press = row(rec, button["entity_id"]).locator("ha-button, mwc-button").first
    press.scroll_into_view_if_needed()
    rec.step("device")
    rec.click(press)
    rec.wait(1500)
    rec.step("pressed")
    row(rec, switch["entity_id"]).scroll_into_view_if_needed()
    rec.step("switch-still-off")


def hardware_check(rec: Recorder) -> None:
    """The Run hardware check button under Diagnostic on the device page
    (#115), then the notification that it is done.

    Pressed while Glowcast is off. The demo's fake TV and lights answer with
    generic values (glowcast_demo's probes); the result is in the diagnostics.
    """
    registry = [
        entry
        for entry in rec.ws({"type": "config/entity_registry/list"})
        if entry["platform"] == "glowcast"
    ]
    (button,) = [e for e in registry if e["unique_id"].endswith("_hardware_check")]
    rec.goto(f"/config/devices/device/{button['device_id']}")
    rec.begin()
    press = row(rec, button["entity_id"]).locator("ha-button, mwc-button").first
    press.scroll_into_view_if_needed()
    rec.step("device")
    rec.click(press)
    rec.wait(1000)
    rec.step("pressed")
    # The demo's fake TV takes about 2 s; then the notification comes.
    rec.wait(2500)
    rec.page.evaluate(
        "() => document.querySelector('home-assistant').shadowRoot"
        ".querySelector('home-assistant-main')"
        ".dispatchEvent(new CustomEvent('hass-show-notifications',"
        " {bubbles: true, composed: true}))"
    )
    rec.wait(1000)
    rec.step("done")


def refused(rec: Recorder) -> None:
    """A TV that refuses the screenshot call (#214): switched on, Glowcast
    asks again for the startup grace (10 s), then stops with Error, and
    Repairs explains why.

    Needs an instance started with GLOWCAST_DEMO_REFUSE (scripts/README.md).
    """
    registry = [
        entry
        for entry in rec.ws({"type": "config/entity_registry/list"})
        if entry["platform"] == "glowcast"
    ]
    (switch,) = [e for e in registry if e["entity_id"].startswith("switch.")]
    device = rec.ws(
        {"type": "config/entity_registry/get", "entity_id": switch["entity_id"]}
    )
    rec.goto(f"/config/devices/device/{device['device_id']}")
    rec.begin()
    toggle = row(rec, switch["entity_id"]).locator("ha-entity-toggle")
    (status,) = [e["entity_id"] for e in registry if e["unique_id"].endswith("_status")]
    since = datetime.now(UTC)
    rec.click(toggle)
    deadline = time.monotonic() + 20
    while not stopped_since(rec, status, since):
        if time.monotonic() > deadline:
            raise RuntimeError("Glowcast did not stop with an error")
        rec.wait(250)
    rec.wait(1000)
    rec.step("stopped")
    # An earlier run's prepare() ignored the issue; this run raised it again.
    for issue in rec.ws({"type": "repairs/list_issues"})["issues"]:
        if issue["domain"] == "glowcast" and issue["ignored"]:
            rec.ws(
                {
                    "type": "repairs/ignore_issue",
                    "domain": issue["domain"],
                    "issue_id": issue["issue_id"],
                    "ignore": False,
                }
            )
    name = rec.page.evaluate(
        f"id => {HASS}.states[id].attributes.friendly_name", switch["entity_id"]
    )
    rec.goto("/config/repairs")
    issue = rec.page.get_by_text(f"{name}:").first
    issue.wait_for()
    rec.step("repairs")
    rec.click(issue)
    rec.page.locator("#dialog-repairs-issue-description").wait_for()
    rec.step("issue")


def stopped_since(rec: Recorder, status: str, since: datetime) -> bool:
    """Whether the status sensor turned to error after `since`."""
    state = rec.page.evaluate(f"id => {HASS}.states[id]", status)
    return state["state"] == "error" and (
        datetime.fromisoformat(state["last_changed"]) >= since
    )


def wait_for_streaming(
    rec: Recorder, entry_id: str, channels: int | None = None
) -> None:
    """Wait (recording) until the pipeline streams, to this many channels.

    The status comes from the diagnostics; the area's channels show that the
    pipeline restarted with a new area.
    """
    deadline = time.monotonic() + 20
    while True:
        status = rec.page.evaluate(
            f"id => {HASS}.callApi('GET', `diagnostics/config_entry/${{id}}`)",
            entry_id,
        )["data"]["status"]
        if (
            status is not None
            and status["phase"] == "streaming"
            and channels in (None, status["channels"])
        ):
            return
        if time.monotonic() > deadline:
            raise RuntimeError(f"Glowcast is not streaming: {status}")
        rec.wait(250)


def row(rec: Recorder, entity_id: str) -> Locator:
    """The entity's row in an entities card, such as on the device page."""
    rows = rec.page.locator("hui-generic-entity-row")
    index = rows.evaluate_all(
        "(rows, id) => rows.findIndex(r => r.config?.entity === id)", entity_id
    )
    if index < 0:
        raise LookupError(f"No row for {entity_id}")
    return rows.nth(index)


# The area of the glowcast_demo stub's demo lights (light.py).
DEMO_AREA_ID = "living_room"


def radio(rec: Recorder, index: int) -> Locator:
    """The radio button at this place of the open form's list."""
    return rec.page.locator(DIALOG).get_by_role("radio").nth(index)


def ha_lights_setup(rec: Recorder) -> None:
    """Set up Home Assistant lights (#9): Home Assistant lights in the output
    menu, an area, a place per light."""
    page = rec.page
    open_setup(rec)
    rec.click(menu_item(rec, "tv"))
    page.locator(PRIMARY).wait_for()
    rec.step("tv")
    rec.submit()
    page.locator(f"{DIALOG} step-flow-menu").wait_for()
    rec.step("output")
    rec.click(menu_item(rec, "ha_lights"))
    radio(rec, 0).wait_for()
    rec.step("follow")
    rec.click(radio(rec, 1))  # a Home Assistant area
    rec.step("follow-area")
    rec.submit()
    page.locator(f"{DIALOG} ha-picker-field").wait_for()
    rec.step("area")
    rec.click(page.locator(f"{DIALOG} ha-picker-field"))
    rec.click(rec.option(DEMO_AREA_ID))
    rec.step("area-chosen")
    rec.submit()
    radio(rec, 0).wait_for()
    rec.step("first-light")
    rec.click(radio(rec, 0))  # left of the screen
    rec.submit()
    page.wait_for_timeout(500)
    rec.step("second-light")
    rec.click(radio(rec, 5))  # fine-tune
    rec.submit()
    page.locator(f"{DIALOG} ha-slider").first.wait_for()
    rec.step("fine-tune")
    rec.click(page.locator(f"{DIALOG} ha-slider").first, at=0.9)
    rec.click(page.locator(f"{DIALOG} ha-slider").nth(1), at=0.3)
    rec.step("fine-tuned")
    rec.submit()
    rec.step("done")
    rec.submit()
    rec.step("integration")


def ha_lights_settings(rec: Recorder) -> None:
    """From Home Assistant lights to an entertainment area in the settings (#9): the
    output menu, pairing with the bridge, the area, the common settings."""
    page = rec.page
    create_ha_lights_entry(rec)
    rec.goto("/config/integrations/integration/glowcast")
    rec.begin()
    configure = rec.localize("ui.panel.config.integrations.config_entry.configure")
    rec.click(page.get_by_role("button", name=configure).first)
    page.locator(f"{DIALOG} step-flow-menu").wait_for()
    rec.step("output")
    rec.click(menu_item(rec, "hue_options"))
    page.locator(PRIMARY).wait_for()
    rec.step("bridge")
    rec.submit()
    rec.step("link")
    rec.submit()
    rec.step("pairing")
    rec.press_link_button()
    page.locator(f"{DIALOG} ha-picker-field").wait_for(timeout=15000)
    rec.step("area")
    rec.click(page.locator(f"{DIALOG} ha-picker-field"))
    rec.click(rec.option(TV_AREA_ID))
    rec.submit()
    page.locator(PRIMARY).wait_for()
    rec.step("settings")
    rec.submit()
    rec.step("saved")


def combined_settings(rec: Recorder) -> None:
    """Add Home Assistant lights to an entry with an entertainment area (#11): the
    output menu, the area as it is, the Home Assistant lights (an area, a place per
    light), the common settings, then the device page with both entities."""
    page = rec.page
    rec.goto("/config/integrations/integration/glowcast")
    rec.begin()
    configure = rec.localize("ui.panel.config.integrations.config_entry.configure")
    rec.click(page.get_by_role("button", name=configure).first)
    page.locator(f"{DIALOG} step-flow-menu").wait_for()
    rec.step("output")
    rec.click(menu_item(rec, "combined_options"))
    page.locator(f"{DIALOG} ha-picker-field").wait_for()
    rec.step("area")
    rec.submit()  # the stored area stays
    radio(rec, 0).wait_for()
    rec.step("follow")
    rec.click(radio(rec, 1))  # a Home Assistant area
    rec.step("follow-area")
    rec.submit()
    page.locator(f"{DIALOG} ha-picker-field").wait_for()
    rec.step("lights-area")
    rec.click(page.locator(f"{DIALOG} ha-picker-field"))
    rec.click(rec.option(DEMO_AREA_ID))
    rec.step("lights-area-chosen")
    rec.submit()
    radio(rec, 0).wait_for()
    rec.step("first-light")
    rec.click(radio(rec, 0))  # left of the screen
    rec.submit()
    page.wait_for_timeout(500)
    rec.step("second-light")
    rec.click(radio(rec, 1))  # right of the screen
    rec.submit()
    page.locator(PRIMARY).wait_for()
    rec.step("settings")
    rec.submit()
    rec.step("saved")
    rec.submit()
    # The entry reloads with the entities of both: the area and the lights.
    page.wait_for_timeout(2000)
    name = page.evaluate(
        f"() => Object.values({HASS}.devices)"
        ".find(d => d.identifiers.some(i => i[0] === 'glowcast')).name"
    )
    rec.click(page.get_by_text(name, exact=True).first)
    page.wait_for_url("**/config/devices/device/**")
    page.wait_for_timeout(1500)
    rec.step("device")
    # Down to Configuration: the entertainment area and the lights.
    page.mouse.wheel(0, 700)
    page.wait_for_timeout(800)
    rec.step("configuration")


def create_ha_lights_entry(rec: Recorder) -> None:
    """A Glowcast entry with Home Assistant lights, through the setup flow's API."""
    flow = rec.page.evaluate(
        f"() => {HASS}.callApi('POST', 'config/config_entries/flow', "
        "{handler: 'glowcast'})"
    )
    path = f"config/config_entries/flow/{flow['flow_id']}"
    tv = rec.page.evaluate(
        f"([p, d]) => {HASS}.callApi('POST', p, d)", [path, {"next_step_id": "tv"}]
    )
    tv_id = tv["data_schema"][0]["selector"]["select"]["options"][0]["value"]
    for data in (
        {"tv": tv_id},
        {"next_step_id": "ha_lights"},
        {"follow": "area"},
        {"area": DEMO_AREA_ID},
        {"position": "left"},
        {"position": "right"},
    ):
        flow = rec.page.evaluate(
            f"([p, d]) => {HASS}.callApi('POST', p, d)", [path, data]
        )
    if flow.get("type") != "create_entry":
        raise RuntimeError(f"The Home Assistant lights setup did not finish: {flow}")


# Flows that need the glowcast_demo stub: switching Glowcast on needs its fake
# TV source and lights, a discovery its service.
SYNCING_FLOWS = {
    "entities",
    "test-lights",
    "screen-discovery",
    "ha-lights-setup",
    "ha-lights-settings",
    "combined-settings",
    "refused",
}

DEMO_TV = "Living room TV"  # the TV's name in demo.py


def blueprint(rec: Recorder) -> None:
    """An automation from the blueprint "Start Glowcast when the TV turns
    on" (#161): the TV, the switch and the optional settings."""
    page = rec.page
    rec.goto("/config/blueprint/dashboard")
    rec.begin()
    rec.click(page.get_by_text("Start Glowcast when the TV turns on").first)
    page.locator("ha-selector-entity").first.wait_for()
    rec.step("editor")
    rec.click(page.locator("ha-selector-entity").nth(0))
    page.wait_for_timeout(800)
    rec.step("tv-list")
    rec.click(page.get_by_text(DEMO_TV, exact=True).last)
    rec.wait(800)
    rec.click(page.locator("ha-selector-entity").nth(1))
    rec.wait(800)
    rec.click(page.get_by_text(f"Glowcast {DEMO_TV}", exact=True).last)
    rec.step("chosen")
    # Also stop, then only after dark.
    switches = page.locator("ha-selector-boolean ha-switch")
    rec.click(switches.nth(0))
    rec.click(switches.nth(1))
    rec.step("after-dark")
    sources = page.locator("ha-selector-text")
    for index, name in enumerate(["HDMI 2", "Netflix"]):
        rec.click(sources.locator(".add-row ha-button"))
        field = sources.locator("input").nth(index)
        field.wait_for()
        field.press_sequentially(name, delay=60)
        rec.wait(300)
    rec.step("sources")


FLOWS: dict[str, Callable[[Recorder], None]] = {
    "setup": setup,
    "settings": settings,
    "device": device,
    "entities": entities,
    "screen-setup": screen_setup,
    "screen-discovery": screen_discovery,
    "ha-lights-setup": ha_lights_setup,
    "ha-lights-settings": ha_lights_settings,
    "combined-settings": combined_settings,
    "blueprint": blueprint,
    "test-lights": test_lights,
    "hardware-check": hardware_check,
    "refused": refused,
}
# Flows that need an instance started for them, so only run when named.
NAMED_ONLY = {"refused"}
# Flows that set Glowcast up themselves: they start without an entry.
SETUP_FLOWS = {
    "setup",
    "screen-setup",
    "screen-discovery",
    "ha-lights-setup",
    "ha-lights-settings",
}


# Flows that start from a fresh entry with an entertainment area only.
AREA_FLOWS = {"combined-settings"}


def prepare(rec: Recorder, flow: str) -> None:
    """Glowcast unset up before the setup flow, set up before the others.

    Also clears what would show in a frame: the notice left by removing an
    entry, and the repair issue a Core install raises.
    """
    entries = rec.ws({"type": "config_entries/get", "domain": "glowcast"})
    if flow in AREA_FLOWS:
        # An entry with an entertainment area only, whatever ran before.
        for entry in entries:
            rec.page.evaluate(
                f"id => {HASS}.callApi('DELETE', "
                "`config/config_entries/entry/${id}`)",
                entry["entry_id"],
            )
        setup(rec)
    elif flow in SETUP_FLOWS:
        for entry in entries:
            rec.page.evaluate(
                f"id => {HASS}.callApi('DELETE', "
                "`config/config_entries/entry/${id}`)",
                entry["entry_id"],
            )
    elif not entries:
        setup(rec)
    use_server_language(rec)
    rec.page.evaluate(
        f"() => {HASS}.callService('persistent_notification', 'dismiss_all')"
    )
    for issue in rec.ws({"type": "repairs/list_issues"})["issues"]:
        if not issue["ignored"]:
            rec.ws(
                {
                    "type": "repairs/ignore_issue",
                    "domain": issue["domain"],
                    "issue_id": issue["issue_id"],
                    "ignore": True,
                }
            )


def use_server_language(rec: Recorder) -> None:
    """Set Home Assistant's own language to the recording's language.

    The profile language (local storage) translates the frontend, but entity
    names come from the server in its language, so a Dutch recording would
    show English names. A reload of Glowcast's entry names its entities again.
    """
    if rec.ws({"type": "get_config"})["language"] == rec.args.lang:
        return
    rec.ws({"type": "config/core/update", "language": rec.args.lang})
    for entry in rec.ws({"type": "config_entries/get", "domain": "glowcast"}):
        rec.page.evaluate(
            f"id => {HASS}.callApi('POST', "
            "`config/config_entries/entry/${id}/reload`)",
            entry["entry_id"],
        )
    rec.page.wait_for_timeout(3000)


def abort_flows_on_exit(page: Page) -> Callable[[], None]:
    """Remember the flows the page starts; return a function that aborts them.

    A run that fails halfway would leave its setup flow open, and Home
    Assistant then refuses a new one for the same TV ("already being
    configured"). Its API does not list flows a user started, so they are
    remembered here.
    """
    started: list[str] = []

    def remember(response: Response) -> None:
        if response.request.method == "POST" and response.url.endswith(
            "/config_entries/flow"
        ):
            with contextlib.suppress(PlaywrightError, KeyError, ValueError):
                started.append(response.json()["flow_id"])

    def abort() -> None:
        for flow_id in started:
            # A finished flow is gone already (404).
            with contextlib.suppress(PlaywrightError):
                page.evaluate(
                    f"id => {HASS}.callApi('DELETE', "
                    "`config/config_entries/flow/${id}`)",
                    flow_id,
                )

    page.on("response", remember)
    return abort


def new_context(browser: Browser, args: argparse.Namespace) -> BrowserContext:
    """A browser in the mobile view, language and theme."""
    dark = args.theme == "dark"
    context = browser.new_context(
        viewport=VIEWPORT,
        device_scale_factor=SCALE,
        is_mobile=True,
        has_touch=True,
        locale=args.lang,
        color_scheme="dark" if dark else "light",
    )
    # The frontend reads its language and theme from local storage.
    context.add_init_script(
        "localStorage.setItem('selectedLanguage', {});"
        "localStorage.setItem('selectedTheme', {});".format(
            json.dumps(json.dumps(args.lang)),
            json.dumps(json.dumps({"theme": "default", "dark": dark})),
        )
    )
    return context


def log_in(page: Page, args: argparse.Namespace) -> None:
    page.goto(args.url)
    page.locator("input[name=username]").fill(args.user, timeout=LOAD_TIMEOUT)
    page.locator("input[name=password]").fill(args.password)
    page.keyboard.press("Enter")
    page.wait_for_function(
        "document.querySelector('home-assistant')?.hass?.user", timeout=LOAD_TIMEOUT
    )


def record(page: Page, args: argparse.Namespace, flow: str) -> None:
    """Record one flow as screenshots and turn them into a GIF."""
    with tempfile.TemporaryDirectory() as tmp:
        rec = Recorder(page, args, "gif", flow, Path(tmp))
        FLOWS[flow](rec)
        if not rec.recording:
            raise RuntimeError(f"The flow {flow!r} never called rec.begin()")
        rec.capture()  # the end state, also if the flow ends on an action
        # Each screenshot shows until the next one; the last one for a pause.
        times = [t for t, _ in rec.frames] + [rec.frames[-1][0] + PAUSE / 1000]
        lines = [
            f"file '{path}'\nduration {times[i + 1] - times[i]:.3f}"
            for i, (_, path) in enumerate(rec.frames)
        ]
        # The concat format ignores the last duration unless the file repeats.
        lines.append(f"file '{rec.frames[-1][1]}'")
        frames = Path(tmp) / "frames.txt"
        frames.write_text("\n".join(lines) + "\n")
        gif = args.out / f"{args.lang}-{args.theme}-{flow}.gif"
        palette = (
            f"fps={FPS},split[a][b];[a]palettegen=stats_mode=diff[p];"
            "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
        )
        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(frames),
                "-filter_complex",
                palette,
                str(gif),
            ],
            check=True,
        )
    size_mb = gif.stat().st_size / 1_000_000
    print(f"saved {gif} ({size_mb:.1f} MB)")
    if gif.stat().st_size > MAX_GIF_BYTES:
        print("  Over 10 MB: too big to attach on GitHub; shorten the flow.")


def fail(page: Page, args: argparse.Namespace) -> NoReturn:
    """Show the error and save the page as it was at that moment."""
    traceback.print_exc()
    page.screenshot(path=args.out / "error.png")
    sys.exit(f"Failed; the page at that moment: {args.out / 'error.png'}")


def main() -> None:
    """Log in once, then record (or photograph) each flow."""
    parser = argparse.ArgumentParser()
    for name in ("url", "bridge", "user", "password", "lang", "theme"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--still", action="store_true")
    parser.add_argument("--no-fake-sync", dest="fake_sync", action="store_false")
    parser.add_argument("flows", nargs="*")
    args = parser.parse_args()
    if unknown := [flow for flow in args.flows if flow not in FLOWS]:
        parser.error(f"unknown flow {unknown}; the flows are {list(FLOWS)}")
    flows = args.flows or [flow for flow in FLOWS if flow not in NAMED_ONLY]
    if not args.fake_sync and (syncing := SYNCING_FLOWS.intersection(flows)):
        if args.flows:
            parser.error(
                f"{sorted(syncing)} switch Glowcast on: start the demo without "
                "--no-fake-sync"
            )
        print(f"Skipping {sorted(syncing)}: the demo runs without the fakes.")
        flows = [flow for flow in flows if flow not in syncing]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = new_context(browser, args)
        page = context.new_page()
        abort_flows = abort_flows_on_exit(page)
        try:
            log_in(page, args)
            for flow in flows:
                prepare(Recorder(page, args, "quiet"), flow)
                if args.still:
                    FLOWS[flow](Recorder(page, args, "still", flow))
                else:
                    record(page, args, flow)
        except Exception:
            fail(page, args)
        finally:
            abort_flows()
            browser.close()


if __name__ == "__main__":
    main()
