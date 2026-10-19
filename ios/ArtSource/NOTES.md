# Bell character layers and sound

## Provenance and release status

The Bell character was designed by Simon Mallas. Its derived layers were made from his design
for this app. The master and working extraction files are not included in this repository and
are not build dependencies. Bundled layers are in this directory, with copies in `mac/assets/`;
app icons are in the iOS asset catalog.

The Bell character art, its derived layers (eyes, expressions and screens), and app icons are
© 2026 Simon Mallas (AI Tortoise). All rights reserved. They are not covered by the MIT license
and may be used only as part of this app. Forks must replace them. See [LICENSE](../../LICENSE).

`ios/BellDesk/Resources/chime.wav` is an original additive synthesis made on 7 October 2026
using Python's standard library, with no samples or generative model. It was dedicated to the
public domain under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).

## Layer coordinates

All layers share a 1179×986 canvas. Stack body, one mouth, and both eyes at (0, 0).
A gaze offset is a translation of an eye layer.

- `body.png`: character with eyes and smile removed; RGBA.
- `mouth_smile.png`, `mouth_line.png`, `mouth_frown.png`, `mouth_o.png`: use exactly one between
  body and eyes; do not move, scale or double it.
- `eye_L.png`: opaque box x 474–541, y 282–381; scale pivot (508, 332).
- `eye_R.png`: opaque box x 636–703, y 282–381; scale pivot (670, 332).

Blink with scaleY around those pivots to about 0.08; do not scaleX. Eye travel is in canvas
pixels from rest, positive right/down: left x −48…+40, y −42…+62; right x −41…+49, y −42…+62.
For a small glance, use about x ±18 and y −12…+10. At a displayed width of 300px, one canvas
pixel is 300/1179 of a screen pixel.
