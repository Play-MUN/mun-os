# MUN Shape sample packages

**English** · [Español](README.es.md)

Two packages for the same contract, [MUN Shape](../../docs/shape.md), with
identities as far apart as the format allows. The console draws both with
the same code, worlds and transitions included; the shell's behaviour
regressions and the laboratory use them, and a publisher can start from
them.

| | `sea` | `paper` |
| --- | --- | --- |
| Palette | deep blue plates, light text, a gold accent | cream plates, ink text, a vermilion accent |
| Card object | organic outline, a window on the sea | card outline, a red sun over ink hills |
| World | a lit gradient, two drifting reliefs with weed, a school of fish, rising bubbles, light rays and caustics, 20 fps | paper with grain and fibres, two ink mountain ranges, drifting specks, falling petals, no light, 10 fps |
| Surfaces | glass | paper |
| Transition | tide in and out, 3.2 s | sweep in, fade out, 1.6 s |
| Sounds | bubbles, a chime, a swell with bubbles on insertion | paper ticks, a brush stroke, plucked strings on insertion |
| Contrast plan | `bridge`: the plates blend through black | `cut`: dark text on light plates cannot blend from MUN's light text |

```sh
./mun card shape check examples/shape/sea --report
./mun card shape check examples/shape/paper --report
./mun card shape init .local/mypkg --example paper     # a copy to change
./mun dev shape examples/shape/sea --window            # the sample in a laboratory console
afplay examples/shape/sea/sfx/insert.wav               # macOS; aplay on Linux
```

From a copy to a card of your own: [dress the console in your
game](../../docs/guides/shape-your-game.md).

A third sample, `reef` (Reef — MUN Shape example), is a template for Shape's motion (`mun-shape/1.1`,
a draft: [docs/shape-motion.md](../../docs/shape-motion.md)): a sea scene
built in code, rendered from 3D and set moving by the console, with
schools of fish going round in depth, a shark, crabs, jellyfish, kelp,
rays, light on the sand, the view drifting, and a school that answers Play.
[`reef-studio`](reef-studio/README.md) is the scene and the bake that make
it; start from either (`./mun card shape init DIR --example reef`). The reef
is MUN Shape's official example of motion: the console's player plays it as
any package, the studio is its source, and `reef` is written only by the
studio; used anywhere else, it is taken whole from one commit
([three parts](reef-studio/README.md#three-parts)). Only a
console built from this source plays the motion, and a bundle of one
carries MUN Collect dressed in the reef (the card `collect-reef`); the
previews up to v0.1.0-dev.4 show the reef's v1 look, its still poster
([trying it](../../docs/shape-motion.md#trying-it)).

Every image and sound of `sea` and `paper` is made by
[`generate.py`](generate.py) with Python's standard library and fixed
seeds: gradients, silhouettes from sums of whole-period sines (so moving
layers tile), supersampled sprites, and additive, filtered-noise and
plucked-string synthesis for the sounds. The reef's images are rendered by
its studio from a scene built in code (with `sea`'s caustics and bubble),
and its sounds are `sea`'s. All of them are this repository's own work
under its licence ([licensing](../../docs/licensing.md)); the studio's page
says what in it is not.

```sh
python3 examples/shape/generate.py            # remake both packages
python3 examples/shape/generate.py --check    # compare with the files here
```

`--check` compares decoded pixels rather than PNG bytes, because another zlib
may compress the same pixels differently; the tests remake the small files
the same way.
