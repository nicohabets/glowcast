# How Glowcast is tested

Glowcast is tested in layers, most of them without a TV. Every change is checked by tests; a change to what the lights get is also drawn in a room and measured on real Hue lights. Only what is specific to the TV is left for a check on the TV itself.

## On every change

- **Unit tests and CI.** Every pull request runs the full test suite twice: on the newest Home Assistant and on the oldest one Glowcast supports (2025.7.2). The colour engine, the pipeline (the fades, the hold on black, the glow, letting go of the lights) and each kind of lights have their own tests, with fake TVs, fake lights and a fake clock.
- **The end-to-end check.** Glowcast runs in a real Home Assistant, follows test patterns (known pixels: red, green, blue, white, black, left red and right blue, a letterbox picture) from a small screen helper, and streams to a fake Hue bridge on the same computer. The check compares the colours that would reach the lights with what they should be. It runs on every pull request.

## The room

The room is a drawing of a living room with a TV, a lightstrip round it and lamps. It is made from Glowcast's own code: a fixed set of pictures goes through the same colour engine and pipeline as at home, and each lamp in the drawing shows the colour and brightness Glowcast sends to it. No camera and no photos: what you see is exactly what Glowcast sends to the lights.

Every pull request that changes what the lights get shows the room before and after the change. On these pages, the same room is drawn for every release since v0.1.0, from each release's own code, so you can see how Glowcast got better and what stayed the same:

- [Colours](colours.md): a sunset, a saturated red, a concert
- [Black bars and movement](shape-and-movement.md): a letterbox film, a 4:3 picture with bars at the sides, a fade between scenes
- [Dark and no picture](dark.md): a sudden dark picture, no picture at all
- [Home Assistant lights](home-assistant-lights.md): lights of any brand at their own place, and an entertainment area and Home Assistant lights together

<!-- rooms -->
| Scenario | v0.4.0 | v0.3.1 | v0.3.0 | v0.2.0 | v0.1.1 | v0.1.0 |
|---|---|---|---|---|---|---|
| 1 Sunset | [●](colours.md#area-sunset "changed"){ .glowcast-changed } | [same](colours.md#area-sunset) | [●](colours.md#area-sunset "changed"){ .glowcast-changed } | [same](colours.md#area-sunset) | [●](colours.md#area-sunset "changed"){ .glowcast-changed } | [new](colours.md#area-sunset) |
| 3 Saturated red | [●](colours.md#area-saturated-red "changed"){ .glowcast-changed } | [same](colours.md#area-saturated-red) | [same](colours.md#area-saturated-red) | [same](colours.md#area-saturated-red) | [●](colours.md#area-saturated-red "changed"){ .glowcast-changed } | [new](colours.md#area-saturated-red) |
| 6 Concert | [●](colours.md#area-concert "changed"){ .glowcast-changed } | [same](colours.md#area-concert) | [same](colours.md#area-concert) | [same](colours.md#area-concert) | [same](colours.md#area-concert) | [new](colours.md#area-concert) |
| 4 Letterbox | [●](shape-and-movement.md#area-letterbox "changed"){ .glowcast-changed } | [same](shape-and-movement.md#area-letterbox) | [same](shape-and-movement.md#area-letterbox) | [same](shape-and-movement.md#area-letterbox) | [same](shape-and-movement.md#area-letterbox) | [new](shape-and-movement.md#area-letterbox) |
| 7 4:3 picture | [●](shape-and-movement.md#area-43-picture "changed"){ .glowcast-changed } | [same](shape-and-movement.md#area-43-picture) | [same](shape-and-movement.md#area-43-picture) | [same](shape-and-movement.md#area-43-picture) | [same](shape-and-movement.md#area-43-picture) | [new](shape-and-movement.md#area-43-picture) |
| 5 Fade between scenes | [●](shape-and-movement.md#area-fade-between-scenes "changed"){ .glowcast-changed } | [same](shape-and-movement.md#area-fade-between-scenes) | [same](shape-and-movement.md#area-fade-between-scenes) | [same](shape-and-movement.md#area-fade-between-scenes) | [same](shape-and-movement.md#area-fade-between-scenes) | [new](shape-and-movement.md#area-fade-between-scenes) |
| 2 Sudden dark picture | [●](dark.md#area-sudden-dark-picture "changed"){ .glowcast-changed } | [same](dark.md#area-sudden-dark-picture) | [same](dark.md#area-sudden-dark-picture) | [same](dark.md#area-sudden-dark-picture) | [●](dark.md#area-sudden-dark-picture "changed"){ .glowcast-changed } | [new](dark.md#area-sudden-dark-picture) |
| 8 No picture | [●](dark.md#area-no-picture "changed"){ .glowcast-changed } | [same](dark.md#area-no-picture) | [same](dark.md#area-no-picture) | [●](dark.md#area-no-picture "changed"){ .glowcast-changed } | [●](dark.md#area-no-picture "changed"){ .glowcast-changed } | [new](dark.md#area-no-picture) |
| Home Assistant lights: 1 Sunset | [●](home-assistant-lights.md#ha-sunset "changed"){ .glowcast-changed } | [same](home-assistant-lights.md#ha-sunset) | [new](home-assistant-lights.md#ha-sunset) | - | - | - |
| Home Assistant lights: 5 Fade between scenes | [●](home-assistant-lights.md#ha-fade-between-scenes "changed"){ .glowcast-changed } | [same](home-assistant-lights.md#ha-fade-between-scenes) | [new](home-assistant-lights.md#ha-fade-between-scenes) | - | - | - |
| Home Assistant lights: 8 No picture | [●](home-assistant-lights.md#ha-no-picture "changed"){ .glowcast-changed } | [same](home-assistant-lights.md#ha-no-picture) | [new](home-assistant-lights.md#ha-no-picture) | - | - | - |
| Together: 9 Home Assistant lights unavailable | [●](home-assistant-lights.md#combined-home-assistant-lights-unavailable "changed"){ .glowcast-changed } | [same](home-assistant-lights.md#combined-home-assistant-lights-unavailable) | [new](home-assistant-lights.md#combined-home-assistant-lights-unavailable) | - | - | - |

●: what the lights get changed since the previous release. same: the same as in the previous release (within rounding). new: the first release with these lights. -: not available.

<!-- /rooms -->

### The same room, other changes { #same-room-other-changes }

A release can be "same" in every scenario and still change things the room does not show. **v0.3.1:** syncing continues after a Home Assistant restart (checked on the test bench, with its restart run); a Home Assistant light that is also in the entertainment area is left out of the Home Assistant lights (the bench's run with an area and Home Assistant lights together); the settings work while the Hue bridge cannot be reached (unit tests); and the outputs share their helpers, with an identical room.

## The test bench

The test bench checks Glowcast on real Hue lights, without the TV: a computer monitor shows the picture, Hue lights in an entertainment area of their own follow it, and a webcam that sees both judges the result. It exists since v0.2.0. Every run starts by calibrating itself (where the lights and the monitor are, the camera's exposure, how much daylight there is), and then checks:

- **The colour per side:** each light shows the colour of its own side of the picture.
- **The delay** from a change on the screen to the light changing.
- **The hold on black:** a short dark scene keeps the colours.
- **The glow:** a longer dark scene shows a faint warm glow.
- **Letting go:** without a picture, Glowcast lets go of the lights.
- **The restore:** after stopping, the lights are back to how they were before.

The bench publishes numbers only, never camera images.

### The delay on the bench

The time until a light changes, as the median and the slowest 10 % (p90). Screen mode: from the monitor changing to the light changing, both seen in the same camera pictures. Test patterns: from the screen helper switching pattern to the light passing halfway to its new colour, including up to about 75 ms of the camera's own delay. Measured on the test bench, not on a TV: on a TV the time to capture a picture comes on top.

| Release | Screen mode (median / p90) | Test patterns (median / p90) |
|---|---|---|
| v0.4.0 | 288 / 438 ms | 290 / 433 ms |
| v0.3.1 | 423 / 523 ms | 427 / 571 ms |
| v0.1.0 - v0.3.0 | not measured | not measured |

The v0.3.1 numbers were measured on the code at the v0.3.1 level on 30 October 2026, before the v0.4 changes. Old releases are not measured again on the bench.

## Only on the TV

Some things can only be checked on a real LG TV, by hand, before a release: the TV's own capture of the picture (webOS), HDR and Dolby Vision, and streaming apps with copy protection (DRM), which give a black picture. See [Known limits](../docs/limits.md).
