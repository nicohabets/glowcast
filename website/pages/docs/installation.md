# Installation

## What you need

- Home Assistant 2025.7.2 or newer
- An LG TV running webOS, set up in Home Assistant with the **LG webOS Smart TV** integration
- Colour lights, one of:
    - A Hue bridge with an entertainment area: a Philips Hue Bridge v2 or Hue Bridge Pro, set up in Home Assistant with the **Philips Hue** integration, and an entertainment area set up in the Hue app (Hue app → Settings → Entertainment areas). The smoothest. The original round bridge (sold 2012-2015) cannot stream.
    - Or colour lights in Home Assistant (loose lights): any colour light, of any brand. No Hue bridge needed.
- [HACS](https://hacs.xyz/)

## Installing with HACS

[Open Glowcast in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=nicohabets&repository=glowcast&category=integration){ .md-button .md-button--primary }

1. Open **HACS**, search for **Glowcast** and open it. Or click the button above: it opens Glowcast in HACS (from Home Assistant 2026.11: the Marketplace) in your own Home Assistant.
2. Click **Download** (bottom right), then **Download** again in the dialog. The newest version is selected.
3. Restart Home Assistant (Settings → System → the restart button in the top right, or the repair HACS shows).
4. Go to **Settings → Devices & services → Add integration → Glowcast**, and continue with the [setup](setup.md).

![The Glowcast setup flow in Home Assistant: choose the TV, choose Hue entertainment area, choose the Hue bridge, press the link button and wait for the pairing, choose the entertainment area, done.](../images/setup-flow.gif){ width="400" }

*Setting up Glowcast: the TV, Hue entertainment area, the Hue bridge and its link button, then the entertainment area. With loose lights you pick the lights instead of the bridge.*

## HACS does not find Glowcast?

Add it as a custom repository: in HACS, open the menu (⋮) in the top right corner → **Custom repositories**, enter `https://github.com/nicohabets/glowcast`, choose type **Integration** and click **Add**. Close the dialog and search again.

## Updates

Updates show up in HACS and under Settings → System → Updates. The [releases](https://github.com/nicohabets/glowcast/releases) say what changed in each version.

From Home Assistant 2026.11, HACS is built into Home Assistant as the [Marketplace](https://hacs.xyz/docs/marketplace/) (Settings → Marketplace); custom repositories added in HACS carry over. Updated steps follow in [#57](https://github.com/nicohabets/glowcast/issues/57).
