# Liveliness

**Liveliness** sets how lively the lights react to the picture: **Calm**, **Normal** (the default) or **Lively**. It is one setting for a few trade-offs that pull in opposite directions: soft fades against fast scene changes, and lively colours against calm ones. Choose it under **Configure** or on Glowcast's device page (see [Settings](settings.md)); a change while syncing applies at the next picture, without the lights going out.

| | Calm | Normal | Lively |
|---|---|---|---|
| Fade between pictures | over about two pictures: smoother, a little slower | over one picture | over one picture |
| A scene cut | fades like everything else | shows at once (about 80 ms) | shows at once (about 40 ms), also for smaller jumps |
| Colours | a little softer, closer to the average of the picture | as Glowcast always did | colourful parts of the picture count even more |
| Lights at the back of the room | follow the whole picture more | in between | follow their own part of the picture more |

**Normal** is exactly what Glowcast did before this setting existed. **Brightness** and **intensity** work on top of every level; the warm glow on a black picture, the hold when the picture suddenly turns black and the release when there is no picture are the same at every level. [Home Assistant lights](home-assistant-lights.md) get the level's colours, but keep their own pace (about once per second, with a fade); they have no place at the back of the room, so only the colours change for them.

Which one to pick:

- **Calm** for everyday viewing, background light and anyone who finds changing colours distracting.
- **Normal** for films and series.
- **Lively** for games, music videos and sport, where you want the room to jump with the picture.

## The three levels side by side

These rooms show what Glowcast sends to the lights at each level, from left to right Calm, Normal and Lively, for the same pictures. They are drawn by the room tool, which runs Glowcast's own colour engine and fades on a set of test pictures: an illustration, not a photo of real lights. Click an image to see it full size.

### A scene cut

A sudden change from one scene to a saturated red one. At Normal and Lively the lights change with the cut; at Calm they glide over to the new colour in about a second.

[![Three rooms side by side, labelled Calm, Normal and Lively: the picture cuts to a saturated red; in Normal and Lively the lights turn red at once, in Calm they glide to red.](../images/liveliness-cut.gif){ width="400" }](../images/liveliness-cut.gif)

### A slow change between scenes

The picture fades from an ocean to a meadow over 2 seconds. All three levels follow it softly; Calm a little behind the picture.

[![Three rooms side by side, labelled Calm, Normal and Lively: the picture fades from blue to green and yellow, and the lights follow it softly in all three.](../images/liveliness-drift.gif){ width="400" }](../images/liveliness-drift.gif)

### A concert

Two spotlights, purple and orange, on a dark stage: a picture that mixes colourful and dark parts. The more lively the level, the more the spotlights' colours lead.

[![Three rooms side by side, labelled Calm, Normal and Lively, with a purple and an orange spotlight on the TV; the lights are purple and orange, a little softer in Calm.](../images/liveliness-concert.png){ width="400" }](../images/liveliness-concert.png)

### A sunset

A smooth picture with an orange sky on the left and blue on the right. Here the levels hardly differ, apart from the table lamp at the back of the room, which follows the whole picture more at Calm and its own part more at Lively.

[![Three rooms side by side, labelled Calm, Normal and Lively, with a sunset on the TV; the lights are orange, pink and blue in all three.](../images/liveliness-sunset.png){ width="400" }](../images/liveliness-sunset.png)

## One light over time

The same light at each level, at update rate High (3 pictures per second), measured from Glowcast's own fades. Left: the picture cuts from one colour to another. Right: the picture changes slowly over 3 seconds. 0 % is the old colour, 100 % the new one; the grey line is the picture itself.

[![Two line charts. A cut: Normal and Lively reach the new colour about 0.1 seconds after the next picture, Lively a little sooner; Calm is halfway after about 0.3 seconds and nearly there after 1.3 seconds. A slow drift: Normal and Lively follow the picture one picture behind on the same line; Calm a little further behind.](../images/liveliness-chart.png){ width="600" }](../images/liveliness-chart.png)

The light can only follow a new picture once Glowcast has it, so all three start at the first picture after the change (here at about 0.67 seconds). At a cut, Normal and Lively are there within a send or two; Calm takes about a second. On a slow change, Normal and Lively are the same: only jumps count as cuts.
