# A computer screen

!!! warning "A preview, in development"
    For now the screen helper is a developer tool that you start from a clone of the repository; see [the screen helper](https://github.com/nicohabets/glowcast/blob/main/screen_helper/README.md). An installable app is planned.

Glowcast can also follow the screen of a PC or Mac, through a small screen helper on that computer.

1. Start the helper on the computer. It shows a 6-digit pairing code.
2. Home Assistant finds it on your network by itself: **Settings → Devices & services** shows **Discovered: Glowcast - *computer name***. Click **Add**.
3. Enter the pairing code, then pick the Hue bridge and the entertainment area, as for a TV.

## Adding it by hand

If the helper is not discovered (for example when Home Assistant and the computer are on different networks), add it by hand: **Add integration → Glowcast → Computer screen**, enter the computer's IP address or host name and the helper's port (8488 unless you started it with another one), then the pairing code.

## Good to know

- Each computer can have one Glowcast entry; when it gets a new address, Home Assistant finds it again and updates the address.
- If the helper no longer accepts Glowcast's key (for example after `--unpair`), Home Assistant asks for a new pairing code; your settings are kept. Pairing again uses the address Glowcast has; if the computer also has a new address that discovery does not find, add it by hand once more instead: that stores the new address and the new pairing, with your settings kept.
- A screen is followed at up to 4 pictures per second.
- The [status](status.md) shows TV off when the screen helper stopped or cannot be reached, or the screen is locked, asleep or gone, for 3 seconds in a row.
