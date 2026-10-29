"""Screenshots of Glowcast in the local demo; run through `scripts/demo.py shoot`.

Each screen is one function in STEPS, run in order in one logged-in browser
page. To add a screen, write a function that takes a Shoot and calls
shoot.save("name"), and add it to STEPS. Files are named
<lang>-<theme>-<nn>-<name>.png.
"""

import argparse
import json
import ssl
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import Locator, Page, sync_playwright

VIEWPORT = {"width": 1100, "height": 800}
# Where the frontend draws its dialogs and their primary button.
DIALOG = "ha-dialog"
PRIMARY = f"{DIALOG} ha-button[slot=primaryAction]"


class Shoot:
    """The browser page and what the steps need."""

    def __init__(self, page: Page, args: argparse.Namespace) -> None:
        """Keep the page and the command line."""
        self.page = page
        self.url: str = args.url
        self.bridge: str = args.bridge
        self.prefix = f"{args.lang}-{args.theme}"
        self.out: Path = args.out
        self.count = 0

    def save(self, name: str, locator: Locator | None = None) -> None:
        """Save a screenshot of the page (or of one element) after it settles."""
        self.page.wait_for_timeout(800)
        self.count += 1
        path = self.out / f"{self.prefix}-{self.count:02d}-{name}.png"
        (locator or self.page).screenshot(path=path, animations="disabled")
        print(f"saved {path}")

    def goto(self, path: str) -> None:
        """Open a page of the frontend and wait until it has drawn."""
        self.page.goto(self.url + path)
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(1500)

    def ws(self, message: dict[str, Any]) -> Any:
        """Send a websocket command as the logged-in user."""
        return self.page.evaluate(
            "m => document.querySelector('home-assistant').hass.callWS(m)", message
        )

    def submit(self) -> None:
        """Click the primary button of the open dialog."""
        self.page.locator(PRIMARY).click()
        self.page.wait_for_timeout(1500)

    def localize(self, key: str) -> str:
        """A text of the frontend in the chosen language, to find a button by."""
        return self.page.evaluate(
            "k => document.querySelector('home-assistant').hass.localize(k)", key
        )

    def press_link_button(self) -> None:
        """Press the fake bridge's link button."""
        request = urllib.request.Request(
            f"{self.bridge}/demo/press-link-button", data=b"", method="POST"
        )
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(request, context=context, timeout=10):
            pass


def clean_slate(shoot: Shoot) -> None:
    """Remove a Glowcast entry from an earlier run, and the notices around it."""
    for entry in shoot.ws({"type": "config_entries/get", "domain": "glowcast"}):
        shoot.page.evaluate(
            "id => document.querySelector('home-assistant').hass.callApi("
            "'DELETE', `config/config_entries/entry/${id}`)",
            entry["entry_id"],
        )
    # Removing an entry leaves a notice; a Core install has a repair issue.
    shoot.page.evaluate(
        "() => document.querySelector('home-assistant').hass.callService("
        "'persistent_notification', 'dismiss_all')"
    )
    for issue in shoot.ws({"type": "repairs/list_issues"})["issues"]:
        if not issue["ignored"]:
            shoot.ws(
                {
                    "type": "repairs/ignore_issue",
                    "domain": issue["domain"],
                    "issue_id": issue["issue_id"],
                    "ignore": True,
                }
            )


def setup_flow(shoot: Shoot) -> None:
    """The setup flow: TV, bridge, link button, pairing, area, done."""
    page = shoot.page
    shoot.goto("/config/integrations/dashboard/add?domain=glowcast")
    # "Do you want to set up Glowcast?"
    page.locator(f"dialog-box {PRIMARY}").click()
    page.locator(PRIMARY).wait_for()
    shoot.save("setup-tv")
    shoot.submit()
    shoot.save("setup-bridge")
    shoot.submit()
    shoot.save("setup-link")
    shoot.submit()
    shoot.save("setup-pairing")
    shoot.press_link_button()
    page.locator(f"{DIALOG} ha-picker-field").wait_for(timeout=15000)
    shoot.save("setup-area")
    page.locator(f"{DIALOG} ha-picker-field").click()
    shoot.save("setup-area-choices")
    page.locator("ha-dropdown-item", has_text="TV area").click()
    shoot.submit()
    shoot.save("setup-done")
    shoot.submit()


def integration_page(shoot: Shoot) -> None:
    """Glowcast's integration page with its entry."""
    shoot.goto("/config/integrations/integration/glowcast")
    shoot.save("integration")


def settings(shoot: Shoot) -> None:
    """The settings screen and the choices of the update rate."""
    page = shoot.page
    shoot.goto("/config/integrations/integration/glowcast")
    configure = shoot.localize("ui.panel.config.integrations.config_entry.configure")
    page.get_by_role("button", name=configure).first.click()
    page.locator(PRIMARY).wait_for()
    shoot.save("settings")
    page.locator(f"{DIALOG} ha-picker-field").last.click()
    shoot.save("settings-update-rate")
    page.keyboard.press("Escape")
    shoot.submit()


def device_page(shoot: Shoot) -> None:
    """The device page with the switch; switching on stops at once (no TV)."""
    page = shoot.page
    device_id = page.evaluate(
        "() => Object.values(document.querySelector('home-assistant').hass.devices)"
        ".find(d => d.identifiers.some(i => i[0] === 'glowcast')).id"
    )
    shoot.goto(f"/config/devices/device/{device_id}")
    shoot.save("device")
    # The source cannot reach a TV on 127.0.0.1, so after the start-up grace
    # (about 10 s) the pipeline stops and the switch turns off again.
    switch = page.evaluate(
        "() => Object.values(document.querySelector('home-assistant').hass.entities)"
        ".find(e => e.platform === 'glowcast').entity_id"
    )
    page.locator("ha-entity-toggle").first.click()
    for state in ("on", "off"):
        page.wait_for_function(
            "([id, s]) => document.querySelector('home-assistant')"
            ".hass.states[id]?.state === s",
            arg=[switch, state],
            timeout=60000,
        )
    shoot.save("device-switch-stopped")


STEPS: list[Callable[[Shoot], None]] = [
    clean_slate,
    setup_flow,
    integration_page,
    settings,
    device_page,
]


def main() -> None:
    """Log in once and run every step."""
    parser = argparse.ArgumentParser()
    for name in ("url", "bridge", "user", "password", "lang", "theme"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    dark = args.theme == "dark"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport=VIEWPORT,
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
        page = context.new_page()
        page.goto(args.url)
        page.locator("input[name=username]").fill(args.user)
        page.locator("input[name=password]").fill(args.password)
        page.keyboard.press("Enter")
        page.wait_for_function(
            "document.querySelector('home-assistant')?.hass?.user", timeout=30000
        )
        shoot = Shoot(page, args)
        try:
            for step in STEPS:
                step(shoot)
        except Exception:
            page.screenshot(path=args.out / "error.png")
            print(f"Failed; the page at that moment: {args.out / 'error.png'}")
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
