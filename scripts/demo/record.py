"""GIFs of Glowcast's flows in the local demo; run through `scripts/demo.py record`.

Each flow is one function in FLOWS. It starts on a recognisable screen, calls
rec.begin() when that screen is ready (the GIF starts there), clicks with
rec.click() (a ring shows where) and calls rec.step("name") on every screen
worth a pause. With --still, every rec.step() saves a PNG instead. To add a
flow, write such a function and add it to FLOWS.

Files: <lang>-<theme>-<flow>.gif, or <lang>-<theme>-<flow>-<nn>-<name>.png.
"""

import argparse
import json
import ssl
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

import imageio_ffmpeg
from playwright.sync_api import Browser, BrowserContext, Locator, Page, sync_playwright

# Home Assistant's mobile view, recorded at scale factor 2: GIFs 800 px wide.
VIEWPORT = {"width": 400, "height": 860}
SCALE = 2
FPS = 10
PAUSE = 1000  # ms on each step of a GIF, after it settles
LOAD_TIMEOUT = 120000  # ms; right after `start` the frontend can take a minute
MAX_GIF_BYTES = 10_000_000  # GitHub's limit for an attached image
# Where the frontend draws its dialogs and their primary button.
DIALOG = "ha-dialog"
PRIMARY = f"{DIALOG} ha-button[slot=primaryAction]"
HASS = "document.querySelector('home-assistant').hass"
# A ring where the next click lands. A popover sits in the top layer, so it
# shows above Home Assistant's modal dialogs too.
RING = """([x, y]) => {
  const ring = document.createElement('div');
  ring.popover = 'manual';
  ring.style.cssText = `position: fixed; inset: auto; margin: 0; padding: 0;
    left: ${x - 20}px; top: ${y - 20}px; width: 40px; height: 40px;
    border-radius: 50%; border: 3px solid #ff4081;
    background: rgba(255, 64, 129, 0.3); pointer-events: none;`;
  document.body.appendChild(ring);
  ring.showPopover();
  setTimeout(() => ring.remove(), 900);
}"""


class Recorder:
    """The browser page and what the flows need."""

    def __init__(self, page: Page, args: argparse.Namespace, mode: str) -> None:
        """Mode "gif" pauses and shows clicks, "still" saves PNGs, "quiet" neither."""
        self.page = page
        self.args = args
        self.mode = mode
        self.flow = ""
        self.count = 0
        self.begun = 0.0

    def begin(self) -> None:
        """The flow's first screen is ready: the GIF starts here."""
        self.begun = time.monotonic()
        self.step("start")

    def step(self, name: str) -> None:
        """Pause on this screen in a GIF, or save it as a PNG."""
        self.page.wait_for_timeout(600)
        if self.mode == "gif":
            self.page.wait_for_timeout(PAUSE)
        elif self.mode == "still":
            self.count += 1
            prefix = f"{self.args.lang}-{self.args.theme}-{self.flow}"
            path = self.args.out / f"{prefix}-{self.count:02d}-{name}.png"
            self.page.screenshot(path=path, animations="disabled")
            print(f"saved {path}")

    def click(self, locator: Locator) -> None:
        """Click, showing first where (in a GIF)."""
        if self.mode == "gif":
            locator.scroll_into_view_if_needed()
            if box := locator.bounding_box():
                center = [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2]
                self.page.evaluate(RING, center)
                self.page.wait_for_timeout(500)
        locator.click()

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
        items = self.page.locator("ha-dropdown-item")
        index = items.evaluate_all(
            "(items, value) => items.findIndex(i => i.value === value)", value
        )
        return items.nth(index)

    def submit(self) -> None:
        """Click the primary button of the open dialog."""
        self.click(self.page.locator(PRIMARY))
        self.page.wait_for_timeout(300)

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
    rec.goto("/config/integrations/dashboard/add?domain=glowcast")
    # "Do you want to set up Glowcast?" over the integrations page.
    page.locator(f"dialog-box {PRIMARY}").wait_for()
    rec.begin()
    rec.click(page.locator(f"dialog-box {PRIMARY}"))
    page.locator(PRIMARY).wait_for()
    rec.step("tv")
    rec.submit()
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
    rec.click(rec.option("area-tv"))
    rec.submit()
    rec.step("done")
    rec.submit()
    rec.step("integration")


def settings(rec: Recorder) -> None:
    """Change the update rate on the settings screen."""
    page = rec.page
    rec.goto("/config/integrations/integration/glowcast")
    rec.begin()
    configure = rec.localize("ui.panel.config.integrations.config_entry.configure")
    rec.click(page.get_by_role("button", name=configure).first)
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


FLOWS: dict[str, Callable[[Recorder], None]] = {
    "setup": setup,
    "settings": settings,
    "device": device,
}


def prepare(rec: Recorder, flow: str) -> None:
    """Glowcast unset up before the setup flow, set up before the others.

    Also clears what would show in a frame: the notice left by removing an
    entry, and the repair issue a Core install raises.
    """
    entries = rec.ws({"type": "config_entries/get", "domain": "glowcast"})
    if flow == "setup":
        for entry in entries:
            rec.page.evaluate(
                f"id => {HASS}.callApi('DELETE', "
                "`config/config_entries/entry/${id}`)",
                entry["entry_id"],
            )
    elif not entries:
        setup(rec)
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


def new_context(
    browser: Browser, args: argparse.Namespace, **options: Any
) -> BrowserContext:
    """A browser in the mobile view, language and theme."""
    dark = args.theme == "dark"
    context = browser.new_context(
        viewport=VIEWPORT,
        device_scale_factor=SCALE,
        is_mobile=True,
        has_touch=True,
        locale=args.lang,
        color_scheme="dark" if dark else "light",
        **options,
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


def record(browser: Browser, args: argparse.Namespace, state: Any, flow: str) -> None:
    """Record one flow on video and turn it into a GIF."""
    with tempfile.TemporaryDirectory() as videos:
        size = {key: value * SCALE for key, value in VIEWPORT.items()}
        context = new_context(
            browser,
            args,
            storage_state=state,
            record_video_dir=videos,
            record_video_size=size,
        )
        page = context.new_page()
        opened = time.monotonic()
        rec = Recorder(page, args, "gif")
        rec.flow = flow
        try:
            FLOWS[flow](rec)
        except Exception:
            fail(page, args)
        finally:
            context.close()
        assert page.video is not None
        video = page.video.path()
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
                "-ss",
                f"{rec.begun - opened:.2f}",
                "-i",
                str(video),
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
    parser.add_argument("flows", nargs="*")
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = new_context(browser, args)
        page = context.new_page()
        try:
            log_in(page, args)
            for flow in args.flows or FLOWS:
                prepare(Recorder(page, args, "quiet"), flow)
                if args.still:
                    rec = Recorder(page, args, "still")
                    rec.flow = flow
                    FLOWS[flow](rec)
                else:
                    record(browser, args, context.storage_state(), flow)
        except Exception:
            fail(page, args)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
