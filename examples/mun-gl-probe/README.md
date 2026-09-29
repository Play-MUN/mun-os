# MUN GL Probe

A card game that stands in for a complete SDL/OpenGL/OpenAL game when no
such game's data is at hand. It exercises the provisional
`linux-arm64-gl-v0` profile ([runtime profiles](../../docs/runtime.md#runtime-profiles))
from inside a real game unit and reports what it found to stderr, which the
unit sends to the journal.

- SDL2 window on the KMS/DRM display, desktop OpenGL loaded at run time
  through `SDL_GL_GetProcAddress` (no `-lGL`), immediate-mode drawing and the
  matrix stack: the calls a classic SDL/OpenGL engine makes.
- OpenAL Soft: opens the default device, plays a 440 Hz tone at start and on
  **S**. The bottom bar is green when a device opened, red otherwise; the
  probe continues without sound, as a robust engine does.
- Keys through SDL: arrows move the copper square, **Esc** exits 0, **F**
  exits 3 (a failed game), **C** aborts (a crashed game). SIGTERM ends the
  loop through `SDL_QUIT`.
- With `MUN_CONTENT_DIR` (or the earlier `NEPTUNE_CONTENT_DIR`) set (a `mount` card), reports which mount holds
  the content directory and its options, counts its entries, reads
  `probe.txt`, and tries to create a file and to execute `probe.sh` there:
  both must be refused. Nothing there is fatal.
- Refuses to open a window without a valid display mode (exit 2 with a
  diagnostic); `MUN_PROBE_FAULT=display_mode` forces that branch for tests.
- Logs the video driver, display mode, GL vendor/renderer/version, the three
  extensions such an engine checks, the OpenAL device and version, key presses
  and a frame count. The frame rate is the probe's own for a trivial scene
  and says nothing about a real game.

The image build compiles it, dynamically linked against the image's SDL2 and
OpenAL Soft, and leaves it beside the image; `BUILD-INFO.json` records the
runtime library versions the image provides:

```sh
./mun dev build --name one
python3 tools/mun-card/mun-card create glprobe --variant game-gl --game .local/mun/builds/one/games/mun-gl-probe/mun-gl-probe
```

Elsewhere, a Debian 13 arm64 system with `libsdl2-dev` and `libopenal-dev`
builds it with `make`.
