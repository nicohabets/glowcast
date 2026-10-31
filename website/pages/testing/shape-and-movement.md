# Black bars and movement

Pictures that do not fill the screen, and pictures that move: a film with black bars, and one scene fading into another. Each room is drawn from that release's own code (see [the room](index.md#the-room)). How the lights follow a scene cut shows at the start of [3 Saturated red](colours.md#area-saturated-red).

<!-- rooms -->
| Scenario | v0.4.0 | v0.3.1 | v0.3.0 | v0.2.0 | v0.1.1 | v0.1.0 |
|---|---|---|---|---|---|---|
| 4 Letterbox | [●](#area-letterbox "changed"){ .glowcast-changed } | [same](#area-letterbox) | [same](#area-letterbox) | [same](#area-letterbox) | [same](#area-letterbox) | [new](#area-letterbox) |
| 5 Fade between scenes | [●](#area-fade-between-scenes "changed"){ .glowcast-changed } | [same](#area-fade-between-scenes) | [same](#area-fade-between-scenes) | [same](#area-fade-between-scenes) | [same](#area-fade-between-scenes) | [new](#area-fade-between-scenes) |

●: what the lights get changed since the previous release. same: the same as in the previous release (within rounding). new: the first release with these lights. -: not available.

### 4 Letterbox { #area-letterbox }

**The picture:** A film at dusk with black bars above and below the picture, and a dim band of sky just under the top bar.

**What the lights should do:** Take their colours from the picture between the bars, not from the bars, so the lights at the top do not turn grey.

=== "v0.4.0 ●"

    ● No grey lights from black bars: Glowcast finds the bars and takes every light's colour from the picture between them.

    [![Letterbox, in the room with v0.4.0](rooms/v0.4.0/area-letterbox.gif){ width="400" loading=lazy }](rooms/v0.4.0/area-letterbox.gif)

=== "v0.3.1"

    Same as v0.1.0.

    [![Letterbox, in the room with v0.3.1](rooms/v0.1.0/area-letterbox.gif){ width="400" loading=lazy }](rooms/v0.1.0/area-letterbox.gif)

=== "v0.3.0"

    Same as v0.1.0.

    [![Letterbox, in the room with v0.3.0](rooms/v0.1.0/area-letterbox.gif){ width="400" loading=lazy }](rooms/v0.1.0/area-letterbox.gif)

=== "v0.2.0"

    Same as v0.1.0.

    [![Letterbox, in the room with v0.2.0](rooms/v0.1.0/area-letterbox.gif){ width="400" loading=lazy }](rooms/v0.1.0/area-letterbox.gif)

=== "v0.1.1"

    Same as v0.1.0.

    [![Letterbox, in the room with v0.1.1](rooms/v0.1.0/area-letterbox.gif){ width="400" loading=lazy }](rooms/v0.1.0/area-letterbox.gif)

=== "v0.1.0"

    The first release: the lights at the top sample the black bar and the strip of picture under it.

    [![Letterbox, in the room with v0.1.0](rooms/v0.1.0/area-letterbox.gif){ width="400" loading=lazy }](rooms/v0.1.0/area-letterbox.gif)

### 5 Fade between scenes { #area-fade-between-scenes }

**The picture:** A blue ocean scene that slowly fades into a green meadow.

**What the lights should do:** Follow the fade smoothly, from blue to green, without jumps.

=== "v0.4.0 ●"

    ● Livelier colours and the strip at the picture's edges; the fade itself stays smooth.

    [![Fade between scenes, in the room with v0.4.0](rooms/v0.4.0/area-fade-between-scenes.gif){ width="400" loading=lazy }](rooms/v0.4.0/area-fade-between-scenes.gif)

=== "v0.3.1"

    Same as v0.1.0.

    [![Fade between scenes, in the room with v0.3.1](rooms/v0.1.0/area-fade-between-scenes.gif){ width="400" loading=lazy }](rooms/v0.1.0/area-fade-between-scenes.gif)

=== "v0.3.0"

    Same as v0.1.0.

    [![Fade between scenes, in the room with v0.3.0](rooms/v0.1.0/area-fade-between-scenes.gif){ width="400" loading=lazy }](rooms/v0.1.0/area-fade-between-scenes.gif)

=== "v0.2.0"

    Same as v0.1.0.

    [![Fade between scenes, in the room with v0.2.0](rooms/v0.1.0/area-fade-between-scenes.gif){ width="400" loading=lazy }](rooms/v0.1.0/area-fade-between-scenes.gif)

=== "v0.1.1"

    Same as v0.1.0.

    [![Fade between scenes, in the room with v0.1.1](rooms/v0.1.0/area-fade-between-scenes.gif){ width="400" loading=lazy }](rooms/v0.1.0/area-fade-between-scenes.gif)

=== "v0.1.0"

    The first release.

    [![Fade between scenes, in the room with v0.1.0](rooms/v0.1.0/area-fade-between-scenes.gif){ width="400" loading=lazy }](rooms/v0.1.0/area-fade-between-scenes.gif)

<!-- /rooms -->
