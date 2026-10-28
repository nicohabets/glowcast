# Glowcast brand

The Glowcast icon and logo ("Kleurgloed"): a TV whose glow takes on the colours of the picture, on a dark rounded tile, with the wordmark "Glowcast" next to it.

## Files

| File | What it is |
|---|---|
| `icon.svg` | The icon, 512 x 512. The tile is the subject, so there is no padding. Works on light and dark backgrounds. |
| `logo.svg` | The icon followed by the wordmark in dark text, for light backgrounds. |
| `logo-dark.svg` | The same with light text, for dark backgrounds. |

The PNGs that Home Assistant shows are made from these and live in [`custom_components/glowcast/brand/`](../../custom_components/glowcast/brand/), as Home Assistant documents for [brand images](https://contributors.home-assistant.io/core/integrations/brands/) (2026.3 and newer):

| PNG | Size | Made from |
|---|---|---|
| `icon.png`, `icon@2x.png` | 256 x 256, 512 x 512 | `icon.svg` |
| `logo.png`, `logo@2x.png` | 1059 x 256, 2117 x 512 | `logo.svg` |
| `dark_logo.png`, `dark_logo@2x.png` | 1059 x 256, 2117 x 512 | `logo-dark.svg` |

There is no `dark_icon.png`: the icon has its own dark tile, and Home Assistant falls back to `icon.png`.

## Colours

| Colour | Use |
|---|---|
| `#101218` | Tile; wordmark on light backgrounds |
| `#15171E` | TV screen |
| `#F4F1EA` | TV frame and stand; wordmark on dark backgrounds |
| `#3D7BFF`, `#4A86FF` | Glow, left (blue) |
| `#FFC37A`, `#FFCB8A` | Glow, top right (warm white) |
| `#E0489A`, `#EA5AA6` | Glow, bottom right (pink) |

The glow is two layers of the same shapes blurred with `feGaussianBlur` (a wide one at 70% opacity and a tighter one), clipped to the tile.

## Wordmark

- Font: [Outfit](https://github.com/Outfitio/Outfit-Fonts) SemiBold (weight 600), Copyright 2021 The Outfit Project Authors, [SIL Open Font License 1.1](https://openfontlicense.org). Taken from the variable font `ofl/outfit/Outfit[wght].ttf` in [google/fonts](https://github.com/google/fonts) (commit `8b0a1d0`). The font file is not in this repository; the SVGs contain the text as outlines.
- Size: font size 0.7 x the icon height (56 px next to an 80 px icon), so the cap height is about half the icon. Letter spacing -0.01em, with Outfit's kerning.
- Placement: a gap of a quarter of the icon height between the icon and the ink of the "G"; the cap height is centred on the icon's vertical centre. The logo ends at the ink of the "t".

## Making the PNGs

[`scripts/render_brand.py`](../../scripts/render_brand.py) does it; [uv](https://docs.astral.sh/uv/) installs its pinned dependencies:

```bash
uv run scripts/render_brand.py
```

It renders the SVGs with [resvg](https://github.com/linebender/resvg) (through `resvg-py`), which supports the blur filter (not every SVG converter does). It checks that each image is trimmed (no fully transparent row or column at the edges), then compresses it losslessly and interlaced with oxipng, as Home Assistant prefers. The interlaced files are 45-50% larger than non-interlaced ones; the @2x logos are about 155 kB.

To change the wordmark, also pass the font. This rebuilds `logo.svg` and `logo-dark.svg` from `icon.svg` first:

```bash
uv run scripts/render_brand.py --font "Outfit[wght].ttf"
```

After a change, check the PNGs by eye at full size and scaled down to 24 px, on a light and on a dark background. If the glow is missing or cut off, the renderer did not apply the filter.
