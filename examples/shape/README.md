# MUN Shape sample packages

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
./mun card shape init mypkg --example paper     # a copy to change
afplay examples/shape/sea/sfx/insert.wav        # macOS; aplay on Linux
```

Every image and sound here is made by [`generate.py`](generate.py) with
Python's standard library and fixed seeds: gradients, silhouettes from sums
of whole-period sines (so moving layers tile), supersampled sprites, and
additive, filtered-noise and plucked-string synthesis for the sounds. They
are this repository's own work under its licence
([licensing](../../docs/licensing.md)).

```sh
python3 examples/shape/generate.py            # remake both packages
python3 examples/shape/generate.py --check    # compare with the files here
```

`--check` compares decoded pixels rather than PNG bytes, because another zlib
may compress the same pixels differently; the tests remake the small files
the same way.
