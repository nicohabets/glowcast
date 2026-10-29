# Glowcast brand

The Glowcast icon and logo ("Glow strip"): a TV on a dark rounded tile, with six separate light segments around it (blue on the left, through violet and pink, to warm on the right), each with a soft glow behind it, and the wordmark "Glowcast" next to it. The segments stay apart at small sizes, so the colours still read at 32 px.

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
| `#3D7BFF` | Segment, left |
| `#6A6CF5` | Segment, top 1 |
| `#B05CC8` | Segment, top 2 |
| `#EA5AA6` | Segment, top 3 |
| `#FF8F6A` | Segment, top 4 |
| `#FFC37A` | Segment, right |

The glow is the same six segments, 6 units larger on each side, blurred with `feGaussianBlur` (stdDeviation 22) at 90% opacity behind the sharp segments, clipped to the tile.

## Wordmark

- Font: [Outfit](https://github.com/Outfitio/Outfit-Fonts) SemiBold (weight 600), Copyright 2021 The Outfit Project Authors, [SIL Open Font License 1.1](https://openfontlicense.org). Taken from the variable font `ofl/outfit/Outfit[wght].ttf` in [google/fonts](https://github.com/google/fonts) (commit `8b0a1d0`). The font file is not in this repository; the SVGs contain the text as outlines.
- Size: font size 0.7 x the icon height (56 px next to an 80 px icon), so the cap height is about half the icon. Letter spacing -0.01em, with Outfit's kerning.
- Placement: a gap of a quarter of the icon height between the icon and the ink of the "G"; the cap height is centred on the icon's vertical centre. The logo ends at the ink of the "t".

## Making the PNGs

[`scripts/render_brand.py`](../../scripts/render_brand.py) does it; [uv](https://docs.astral.sh/uv/) installs its pinned dependencies:

```bash
uv run scripts/render_brand.py
```

It renders the SVGs with [resvg](https://github.com/linebender/resvg) (through `resvg-py`), which supports the blur filter (not every SVG converter does). It checks that each image is trimmed (no fully transparent row or column at the edges), then compresses it losslessly and interlaced with oxipng, as Home Assistant prefers. The interlaced files are 45-50% larger than non-interlaced ones; the @2x logos are about 115 kB.

To change the wordmark, also pass the font. This rebuilds `logo.svg` and `logo-dark.svg` from `icon.svg` first:

```bash
uv run scripts/render_brand.py --font "Outfit[wght].ttf"
```

After a change, check the PNGs by eye at full size and scaled down to 24 px, on a light and on a dark background. The six segments must stay separate at 24 px. If the glow is missing or cut off, the renderer did not apply the filter.

## Illustrations

The illustrations of what Glowcast does live in [`docs/images/illustrations/`](../images/illustrations/): an isometric, generic living room in which only the light changes from scene to scene. Each scene is an SVG (the source) with an 800 px PNG made from it for the README; scene 1 also has an animated WebP loop. The background is transparent, so the same file works on a light and a dark page.

| Scene | What it shows |
|---|---|
| `scene-1-picture-leads` | A sunset on the TV; the strip and lamps take warm on the left, blue on the right. The `-loop.webp` (800 px wide, 5 s) changes the picture and the lights follow. |
| `scene-2-dark-scene` | A dark picture; every light at a dim warm glow |
| `scene-3-saturated-colour` | A strong red picture; the lights show pure red |
| `scene-4-loose-lights` | Planned ([#76](https://github.com/nicohabets/glowcast/issues/76)): lamps elsewhere in the room each follow their own side of the picture |
| `scene-5-before` | The same room with the TV and all lights off |

Like the icon, they show no Philips Hue or LG branding, no people and no text.
