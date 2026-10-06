# Examples

**English** · [Español](README.es.md)

Games that run from a Game Card, and recipes for integrating one. The image
build compiles the two example games against the image's own libraries and
leaves them in `.local/mun/builds/<name>/games/`, beside the image, never in
it ([os/README.md](../os/README.md)).

- [`mun-collect`](mun-collect/README.md): C11, framebuffer and evdev,
  static; a small test game that asks the console to save on the card
  (`linux-arm64-v0`).
- [`mun-gl-probe`](mun-gl-probe/README.md): SDL2, OpenGL and OpenAL, linked
  against the image's libraries; a probe of the `linux-arm64-gl-v0` profile
  and of content access during play.

- [`shape`](shape/README.md): two MUN Shape packages, `sea` and `paper`,
  drawn and synthesised by a script here; the same contract, two different
  identities ([docs/shape.md](../docs/shape.md)).

A game this repository does not carry, such as a port of one you own, is
compiled from a recipe you keep outside it (`./mun dev build --recipe DIR`,
[game recipes](../os/README.md#game-recipes)) and goes on a card you make;
its data, binary and card image stay in `.local/`.

What a game may expect from the console is in [docs/runtime.md](../docs/runtime.md)
and [docs/saves.md](../docs/saves.md).
